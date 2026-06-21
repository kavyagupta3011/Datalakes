"""
bronze_to_silver.py
--------------------
The core, fully-generic Bronze -> Silver ETL. Nothing in here is specific to
any domain - it works off whatever build_catalog.py discovered.

Per-file decision sequence (mirrors how a real lakehouse engine gates work):
  flagged for review (unknown domain)  -> still processed, but flagged
  file missing on disk                 -> FAILED
  silver output already exists & file unchanged (checksum match) -> SKIPPED (idempotent)
  size > 200MB                         -> SKIPPED (too large for in-memory demo processing)
  extension is unstructured            -> COPIED as-is into the Silver partition
  extension is structured              -> decode -> clean -> harmonize -> write Parquet
  unknown extension                    -> SKIPPED, reason logged

Every row that reaches Silver carries lineage columns so it can always be
traced back to the exact Bronze file (and checksum) it came from - this is
the project's main traceability mechanism, deliberately file-based rather
than a heavyweight database governance layer.
"""
import json
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from common import (
    BRONZE_DIR,
    CATALOG_FILE,
    LINEAGE_FILE,
    PIPELINE_RUN_ID,
    SILVER_DIR,
    UNSTRUCTURED_FORMATS,
    log,
    now_iso,
    read_json,
    write_json,
)
from schema_engine import _normalize as normalize_name
from schema_engine import harmonize_columns, load_yaml
from common import CONFIG_DIR

MAX_SIZE_BYTES = 200 * 1024 * 1024
_HEURISTICS = load_yaml(CONFIG_DIR / "heuristics.yaml")
_BOOL_TRUE = set(_HEURISTICS.get("boolean_value_map", {}).get("true_values", []))
_BOOL_FALSE = set(_HEURISTICS.get("boolean_value_map", {}).get("false_values", []))

# Offline OCR only (no LLM/API-key dependent structuring) - applies to image
# formats and PDFs among the unstructured passthrough set. Audio formats
# (.mp3/.wav) are still copied through as-is, just never OCR'd.
OCR_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".pdf"}


def ocr_extract_text(path: Path) -> dict:
    """
    Best-effort offline text extraction via the system Tesseract binary
    (through pytesseract) - no network calls, no API key, no LLM. Always
    returns a result dict rather than raising, since this runs against
    arbitrary/possibly-corrupt unstructured files and must never take the
    pipeline down with it.
    """
    try:
        import pytesseract
        from PIL import Image

        if path.suffix.lower() == ".pdf":
            try:
                from pdf2image import convert_from_path
            except ImportError:
                return {
                    "text": "",
                    "char_count": 0,
                    "engine": "tesseract",
                    "error": "pdf2image not installed - PDF OCR skipped",
                }
            pages = convert_from_path(str(path))
            text = "\n".join(pytesseract.image_to_string(p) for p in pages)
        else:
            text = pytesseract.image_to_string(Image.open(path))

        text = text.strip()
        return {"text": text, "char_count": len(text), "engine": "tesseract", "error": None}
    except Exception as exc:  # noqa: BLE001 - OCR must never crash the pipeline
        return {"text": "", "char_count": 0, "engine": "tesseract", "error": str(exc)}


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def decode_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def decode_excel(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        if not df.empty:
            return df
    return pd.DataFrame()


def decode_json(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_json(path)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except (ValueError, TypeError):
        pass
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return pd.json_normalize(data)


def decode_xml(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_xml(path)
        if not df.empty:
            return df
    except Exception:
        pass
    # manual fallback: flatten first level of repeated child elements
    tree = ET.parse(path)
    root = tree.getroot()
    records = []
    # records are assumed to be the repeated direct children of root
    for child in root:
        rec = dict(child.attrib)
        for sub in child:
            rec[sub.tag] = sub.text
        records.append(rec)
    return pd.DataFrame(records)


DECODERS = {
    ".csv": decode_csv,
    ".xlsx": decode_excel,
    ".xls": decode_excel,
    ".json": decode_json,
    ".xml": decode_xml,
}


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
def structural_clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_name(c) for c in df.columns]
    return df


def strip_strings(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def drop_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(how="all")


def infer_and_cast(series: pd.Series):
    nonnull = series.dropna()
    if nonnull.empty:
        return series, "empty"

    str_vals = nonnull.astype(str).str.strip().str.lower()
    bool_hit_ratio = str_vals.isin(_BOOL_TRUE | _BOOL_FALSE).mean()
    if bool_hit_ratio >= 0.9:
        mapping = {v: True for v in _BOOL_TRUE} | {v: False for v in _BOOL_FALSE}
        casted = series.apply(
            lambda v: mapping.get(str(v).strip().lower()) if pd.notna(v) else None
        )
        return casted, "boolean"

    # Numeric check runs BEFORE datetime: pd.to_datetime on already-numeric
    # values (e.g. floats from an Excel column) interprets them as nanosecond
    # epoch offsets rather than failing, which would wrongly classify plain
    # numeric measures (order totals, scores, etc.) as dates. Genuine date
    # strings ("2026-01-01", "06/01/2026") fail numeric coercion and fall
    # through to the datetime check below, so this ordering is safe.
    num = pd.to_numeric(nonnull, errors="coerce")
    if num.notna().mean() >= 0.7:
        return pd.to_numeric(series, errors="coerce"), "numeric"

    dt = pd.to_datetime(nonnull, errors="coerce", format="mixed")
    if dt.notna().mean() >= 0.7:
        return pd.to_datetime(series, errors="coerce", format="mixed"), "datetime"

    return series, "string"


def clean_dataframe(df: pd.DataFrame):
    df = structural_clean_columns(df)
    df = strip_strings(df)
    df = drop_empty_rows(df)
    rows_before_dedup = len(df)
    df = df.drop_duplicates()
    duplicates_removed = rows_before_dedup - len(df)

    type_report = {}
    for col in df.columns:
        df[col], dtype_label = infer_and_cast(df[col])
        type_report[col] = dtype_label

    return df, {"duplicates_removed": duplicates_removed, "types": type_report}


def quality_profile(df: pd.DataFrame) -> dict:
    n_rows = len(df)
    n_cols = len(df.columns)
    null_pct = {c: round(float(df[c].isna().mean()) * 100, 2) for c in df.columns}
    completeness = round(100 - (sum(null_pct.values()) / max(n_cols, 1)), 2)
    return {
        "row_count": n_rows,
        "column_count": n_cols,
        "null_pct_by_column": null_pct,
        "completeness_score": completeness,
        "sample_values": {
            c: [str(v) for v in df[c].dropna().head(3).tolist()] for c in df.columns
        },
    }


def detect_schema_drift(profile_path: Path, current_columns):
    if not profile_path.exists():
        return None
    prev = read_json(profile_path, {})
    prev_cols = set(prev.get("null_pct_by_column", {}).keys())
    cur_cols = set(current_columns)
    added = cur_cols - prev_cols
    removed = prev_cols - cur_cols
    if added or removed:
        return {"added_columns": sorted(added), "removed_columns": sorted(removed)}
    return None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def extract_source_date(stem: str):
    tokens = stem.split("_")
    for tok in reversed(tokens):
        if tok.isdigit() and len(tok) == 8:
            return tok[:4], tok[4:6]
        if tok.isdigit() and len(tok) == 4:
            return tok, "00"
    return "unknown", "00"


def silver_partition_dir(domain: str, entity: str, year: str, month: str) -> Path:
    return SILVER_DIR / f"domain={domain}" / f"entity={entity}" / f"year={year}" / f"month={month}"


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------
def process_file(rel_path: str, meta: dict) -> dict:
    bronze_path = BRONZE_DIR / rel_path
    domain, entity, fmt = meta["domain"], meta["entity"], meta["format"]
    year, month = extract_source_date(bronze_path.stem)
    out_dir = silver_partition_dir(domain, entity, year, month)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "bronze_path": rel_path,
        "domain": domain,
        "entity": entity,
        "status": None,
        "reason": None,
        "silver_path": None,
        "rows_in": None,
        "rows_out": None,
    }

    if not bronze_path.exists():
        result.update(status="FAILED", reason="file missing on disk")
        return result

    if meta["size_bytes"] > MAX_SIZE_BYTES:
        result.update(status="SKIPPED", reason="exceeds size limit")
        return result

    if fmt in UNSTRUCTURED_FORMATS:
        dest = out_dir / bronze_path.name
        if dest.exists():
            result.update(status="SKIPPED", reason="already copied (idempotent)")
            return result
        shutil.copy2(bronze_path, dest)
        result.update(status="COPIED", silver_path=str(dest.relative_to(SILVER_DIR)))

        if fmt in OCR_FORMATS:
            ocr = ocr_extract_text(bronze_path)
            ocr_sidecar = out_dir / f"{bronze_path.stem}.ocr.json"
            write_json(
                ocr_sidecar,
                {
                    "_bronze_path": rel_path,
                    "_domain": domain,
                    "_entity": entity,
                    "_format": fmt,
                    "_file_checksum": meta["checksum"],
                    "_processed_at": now_iso(),
                    "_pipeline_run_id": PIPELINE_RUN_ID,
                    **ocr,
                },
            )
            result["ocr"] = {"char_count": ocr["char_count"], "error": ocr["error"]}
            log(
                f"  [OCR] {rel_path}: extracted {ocr['char_count']} char(s)"
                + (f" (error: {ocr['error']})" if ocr["error"] else "")
            )
        return result

    if fmt not in DECODERS:
        result.update(status="SKIPPED", reason=f"unsupported extension {fmt}")
        return result

    dest_parquet = out_dir / f"{bronze_path.stem}.parquet"
    profile_path = out_dir / f"{bronze_path.stem}.profile.json"
    if dest_parquet.exists():
        result.update(status="SKIPPED", reason="silver output exists (idempotent)")
        return result

    try:
        raw_df = DECODERS[fmt](bronze_path)
    except Exception as exc:  # noqa: BLE001
        result.update(status="FAILED", reason=f"decode error: {exc}")
        return result

    rows_in = len(raw_df)
    cleaned_df, clean_report = clean_dataframe(raw_df)
    cleaned_df, mapping_report = harmonize_columns(cleaned_df, domain, entity)

    # lineage columns - this is what makes every Silver/Gold row traceable
    # back to its exact Bronze source file and pipeline run.
    cleaned_df["_bronze_path"] = rel_path
    cleaned_df["_domain"] = domain
    cleaned_df["_entity"] = entity
    cleaned_df["_format"] = fmt
    cleaned_df["_file_checksum"] = meta["checksum"]
    cleaned_df["_processed_at"] = now_iso()
    cleaned_df["_pipeline_run_id"] = PIPELINE_RUN_ID
    cleaned_df["_row_checksum"] = pd.util.hash_pandas_object(
        cleaned_df.drop(columns=[c for c in cleaned_df.columns if c.startswith("_")]),
        index=False,
    ).astype(str)
    cleaned_df["_needs_review"] = domain == "unknown" or any(
        m["needs_review"] for m in mapping_report
    )

    cleaned_df.to_parquet(dest_parquet, index=False, compression="snappy")

    drift = detect_schema_drift(profile_path, cleaned_df.columns)
    profile = quality_profile(cleaned_df)
    profile.update(
        {
            "source_file": rel_path,
            "duplicates_removed": clean_report["duplicates_removed"],
            "column_type_inference": clean_report["types"],
            "column_mapping_report": mapping_report,
            "schema_drift": drift,
        }
    )
    write_json(profile_path, profile)

    result.update(
        status="SUCCESS",
        silver_path=str(dest_parquet.relative_to(SILVER_DIR)),
        rows_in=rows_in,
        rows_out=len(cleaned_df),
        needs_review=bool(cleaned_df["_needs_review"].iloc[0]) if len(cleaned_df) else False,
        drift=drift,
    )
    return result


def run(max_workers: int = 8):
    catalog = read_json(CATALOG_FILE, {"files": {}})
    files = catalog.get("files", {})
    if not files:
        log("Catalog is empty - run build_catalog.py first.")
        return []

    log(f"Processing {len(files)} cataloged Bronze files (run_id={PIPELINE_RUN_ID})...")
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_file, rel, meta): rel for rel, meta in files.items()}
        for fut in as_completed(futures):
            res = fut.result()
            log(f"  [{res['status']}] {res['bronze_path']} ({res.get('reason') or 'ok'})")
            results.append(res)

    # processing log
    write_json(SILVER_DIR / "processing_log.json", {"run_id": PIPELINE_RUN_ID, "results": results})

    review_needed = [r for r in results if r.get("needs_review") or r["domain"] == "unknown"]
    write_json(SILVER_DIR / "review_needed.json", review_needed)

    # lineage report: bronze -> silver, append-only across runs
    lineage = read_json(LINEAGE_FILE, {"entries": []})
    for r in results:
        if r["status"] in ("SUCCESS", "COPIED"):
            lineage["entries"].append(
                {
                    "pipeline_run_id": PIPELINE_RUN_ID,
                    "bronze_path": r["bronze_path"],
                    "silver_path": r["silver_path"],
                    "domain": r["domain"],
                    "entity": r["entity"],
                    "rows_in": r.get("rows_in"),
                    "rows_out": r.get("rows_out"),
                    "processed_at": now_iso(),
                }
            )
    write_json(LINEAGE_FILE, lineage)

    summary = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    log(f"Bronze->Silver complete. Summary: {summary}. "
        f"{len(review_needed)} file(s) flagged for review.")
    return results


if __name__ == "__main__":
    run()
