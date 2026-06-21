"""
silver_to_gold.py
------------------
Builds a generic star schema in PostgreSQL directly from whatever domains
and entities happen to exist in the Silver layer - no entity is hardcoded.

How an entity is classified, generically:
  - "dimension" if it looks like master/reference data: usually one
    identifier column, few/no numeric measures, at most one descriptive date.
  - "fact" if it looks transactional: 2+ identifier columns (it references
    other entities), OR it carries numeric measures, OR it carries 2+ date
    columns (e.g. opened_at/closed_at - an event with a duration).

  This heuristic can be overridden per domain.entity via
  config/entity_overrides.yaml, which is checked first - see load_overrides().

Tables produced:
  dim_date            - generic calendar dimension built from every date
                         column found across all Silver entities
  dim_<entity>         - one per dimension-classified entity (surrogate key
                         + natural id + descriptive attributes)
  fact_<domain>_<entity> - one per fact-classified entity, with foreign keys
                         resolved against whichever dim_<entity> and dim_date
                         rows match, plus the original lineage columns kept
                         intact for traceability back to Bronze.

Load modes:
  incremental (default) - existing dim/fact tables are left in place;
    only natural-id rows not already present (dims) or rows whose
    _row_checksum isn't already present (facts) are appended, with
    surrogate keys continuing from MAX(key)+1. Stable across runs.
  --full-reload          - truncates and rebuilds every table from scratch
    (surrogate keys are reassigned). Use for bootstrapping or after a
    schema change.
"""
import argparse
import re

import pandas as pd
from sqlalchemy import inspect, text

from common import CONFIG_DIR, SILVER_DIR, get_engine, load_yaml, log

ID_COL_RE = re.compile(r"_id$")
LINEAGE_COLS = [
    "_bronze_path",
    "_domain",
    "_entity",
    "_format",
    "_file_checksum",
    "_processed_at",
    "_pipeline_run_id",
    "_row_checksum",
    "_needs_review",
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_entities():
    """Returns {(domain, entity): combined_dataframe} from every Silver parquet file."""
    groups = {}
    for parquet_path in SILVER_DIR.rglob("*.parquet"):
        parts = {p.split("=")[0]: p.split("=")[1] for p in parquet_path.parts if "=" in p}
        domain, entity = parts.get("domain"), parts.get("entity")
        if not domain or not entity:
            continue
        df = pd.read_parquet(parquet_path)
        groups.setdefault((domain, entity), []).append(df)

    return {key: pd.concat(dfs, ignore_index=True) for key, dfs in groups.items()}


def load_overrides() -> dict:
    """
    Manual fact/dimension classification overrides, checked before the
    heuristic. Format (config/entity_overrides.yaml):

        retail:
          customers: dimension
          orders: fact

    Returns {} if the file doesn't exist or is empty - the heuristic alone
    then decides everything, exactly as before this feature existed.
    """
    path = CONFIG_DIR / "entity_overrides.yaml"
    if not path.exists():
        return {}
    return load_yaml(path) or {}


def classify_entity(domain: str, entity: str, df: pd.DataFrame, overrides: dict = None) -> str:
    overrides = overrides or {}
    forced = overrides.get(domain, {}).get(entity)
    if forced in ("dimension", "fact"):
        log(f"  {domain}.{entity}: classification overridden -> {forced} (config/entity_overrides.yaml)")
        return forced

    business_cols = [c for c in df.columns if not c.startswith("_")]
    id_cols = [c for c in business_cols if ID_COL_RE.search(c)]
    date_cols = [c for c in business_cols if pd.api.types.is_datetime64_any_dtype(df[c])]
    measure_cols = [
        c
        for c in business_cols
        if pd.api.types.is_numeric_dtype(df[c]) and c not in id_cols
    ]
    if len(id_cols) >= 2 or len(measure_cols) >= 1 or len(date_cols) >= 2:
        return "fact"
    return "dimension"


# ---------------------------------------------------------------------------
# dim_date
# ---------------------------------------------------------------------------
def build_dim_date(entity_frames: dict) -> pd.DataFrame:
    all_dates = set()
    for df in entity_frames.values():
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                all_dates.update(df[col].dropna().dt.date.tolist())

    if not all_dates:
        return pd.DataFrame()

    dim = pd.DataFrame({"date": sorted(all_dates)})
    dim["date"] = pd.to_datetime(dim["date"])
    dim["date_key"] = dim["date"].dt.strftime("%Y%m%d").astype(int)
    dim["year"] = dim["date"].dt.year
    dim["quarter"] = dim["date"].dt.quarter
    dim["month"] = dim["date"].dt.month
    dim["month_name"] = dim["date"].dt.month_name()
    dim["day"] = dim["date"].dt.day
    dim["day_of_week"] = dim["date"].dt.day_name()
    dim["is_weekend"] = dim["date"].dt.dayofweek >= 5
    return dim[
        ["date_key", "date", "year", "quarter", "month", "month_name", "day", "day_of_week", "is_weekend"]
    ]


def date_to_key(series: pd.Series) -> pd.Series:
    """Returns a nullable Int64 (YYYYMMDD), matching dim_date.date_key's dtype
    so this merges cleanly even when some rows have missing dates."""
    formatted = pd.to_datetime(series, errors="coerce").dt.strftime("%Y%m%d")
    return pd.to_numeric(formatted, errors="coerce").astype("Int64")


# ---------------------------------------------------------------------------
# dim_<entity>
# ---------------------------------------------------------------------------
def build_dimension_table(entity: str, df: pd.DataFrame):
    """Returns (dim_df, natural_id_col). dim_df has no surrogate key yet -
    that's assigned by load_dimension_incremental() so keys stay stable
    across runs instead of being reassigned every time."""
    business_cols = [c for c in df.columns if not c.startswith("_")]
    id_cols = [c for c in business_cols if ID_COL_RE.search(c)]
    natural_id = id_cols[0] if id_cols else business_cols[0]

    keep_cols = business_cols + ["_bronze_path", "_file_checksum"]
    dim = df[keep_cols].drop_duplicates(subset=[natural_id], keep="first").reset_index(drop=True)
    return dim, natural_id


# ---------------------------------------------------------------------------
# fact_<domain>_<entity>
# ---------------------------------------------------------------------------
def build_fact_table(domain, entity, df, dim_registry):
    """dim_registry: {entity_name: (dim_df, natural_id_col)}. Returns the
    fact rows with FKs resolved but no fact_id yet - assigned by
    load_fact_incremental() so ids stay stable across runs."""
    fact = df.copy()
    business_cols = [c for c in fact.columns if not c.startswith("_")]

    # link every date column to dim_date
    for col in business_cols:
        if pd.api.types.is_datetime64_any_dtype(fact[col]):
            fact[f"{col}_key"] = date_to_key(fact[col])

    # link any column matching another entity's natural id to that dim's surrogate key
    for dim_entity, (dim_df, natural_id) in dim_registry.items():
        if dim_entity == entity:
            continue
        if natural_id in fact.columns:
            lookup = dim_df[[natural_id, f"{dim_entity}_key"]].drop_duplicates(subset=[natural_id])
            before = len(fact)
            fact = fact.merge(lookup, on=natural_id, how="left")
            matched = fact[f"{dim_entity}_key"].notna().sum()
            log(
                f"    fact_{domain}_{entity}: resolved {matched}/{before} rows against dim_{dim_entity}"
            )

    return fact


# ---------------------------------------------------------------------------
# Postgres load
# ---------------------------------------------------------------------------
def table_exists(engine, name: str) -> bool:
    return inspect(engine).has_table(name)


def load_table(engine, name, df, pk_col=None, index_cols=None, unique_cols=None):
    """Full create-or-replace load (used for first-ever load and --full-reload)."""
    df.to_sql(name, engine, if_exists="replace", index=False, chunksize=1000)
    with engine.begin() as conn:
        if pk_col:
            conn.execute(text(f'ALTER TABLE "{name}" ADD PRIMARY KEY ("{pk_col}")'))
        for col in index_cols or []:
            if col in df.columns and col != pk_col:
                conn.execute(text(f'CREATE INDEX ON "{name}" ("{col}")'))
        for col in unique_cols or []:
            if col in df.columns and col != pk_col:
                conn.execute(text(f'CREATE UNIQUE INDEX ON "{name}" ("{col}")'))
    log(f"  loaded {name}: {len(df)} rows (full load)")


def append_rows(engine, name, df):
    if df.empty:
        log(f"  {name}: no new rows")
        return
    df.to_sql(name, engine, if_exists="append", index=False, chunksize=1000)
    log(f"  {name}: appended {len(df)} new row(s)")


def load_dim_date_incremental(engine, dim_date_df: pd.DataFrame, full_reload: bool):
    name = "dim_date"
    if dim_date_df.empty:
        return pd.read_sql_table(name, engine) if table_exists(engine, name) else dim_date_df

    if full_reload or not table_exists(engine, name):
        load_table(engine, name, dim_date_df, pk_col="date_key")
        return dim_date_df

    existing = pd.read_sql_table(name, engine)
    new_rows = dim_date_df[~dim_date_df["date_key"].isin(set(existing["date_key"]))]
    if new_rows.empty:
        log(f"  dim_date: no new dates (0 appended, {len(existing)} existing)")
        return existing
    append_rows(engine, name, new_rows)
    return pd.concat([existing, new_rows], ignore_index=True)


def load_dimension_incremental(engine, entity: str, dim_df: pd.DataFrame, natural_id: str, full_reload: bool):
    """Returns the full (existing + newly appended) dim dataframe, with
    surrogate keys, for use as the FK lookup source by fact tables."""
    name = f"dim_{entity}"
    key_col = f"{entity}_key"

    if full_reload or not table_exists(engine, name):
        dim_df = dim_df.copy()
        dim_df.insert(0, key_col, range(1, len(dim_df) + 1))
        load_table(engine, name, dim_df, pk_col=key_col, unique_cols=[natural_id])
        return dim_df

    existing = pd.read_sql_table(name, engine)
    new_rows = dim_df[~dim_df[natural_id].isin(set(existing[natural_id]))].copy()
    if new_rows.empty:
        log(f"  dim_{entity}: no new rows (0 appended, {len(existing)} existing)")
        return existing

    next_key = int(existing[key_col].max()) + 1
    new_rows.insert(0, key_col, range(next_key, next_key + len(new_rows)))
    append_rows(engine, name, new_rows)
    return pd.concat([existing, new_rows], ignore_index=True)


def load_fact_incremental(engine, domain: str, entity: str, fact_df: pd.DataFrame, full_reload: bool):
    name = f"fact_{domain}_{entity}"
    index_cols = [c for c in fact_df.columns if c.endswith("_key")]

    if full_reload or not table_exists(engine, name):
        fact_df = fact_df.copy()
        fact_df.insert(0, "fact_id", range(1, len(fact_df) + 1))
        load_table(engine, name, fact_df, pk_col="fact_id", index_cols=index_cols, unique_cols=["_row_checksum"])
        return

    existing_checksums = set(
        pd.read_sql_query(text(f'SELECT "_row_checksum" FROM "{name}"'), engine)["_row_checksum"]
    )
    new_rows = fact_df[~fact_df["_row_checksum"].isin(existing_checksums)].copy()
    if new_rows.empty:
        log(f"  fact_{domain}_{entity}: no new rows ({len(existing_checksums)} existing)")
        return

    with engine.begin() as conn:
        max_id = conn.execute(text(f'SELECT MAX(fact_id) FROM "{name}"')).scalar() or 0
    new_rows.insert(0, "fact_id", range(max_id + 1, max_id + 1 + len(new_rows)))
    append_rows(engine, name, new_rows)


def run(full_reload: bool = False):
    engine = get_engine()
    overrides = load_overrides()
    entity_frames = discover_entities()
    if not entity_frames:
        log("No Silver parquet files found - run bronze_to_silver.py first.")
        return

    log(f"Discovered {len(entity_frames)} Silver entities: "
        f"{[f'{d}.{e}' for d, e in entity_frames.keys()]}")
    log(f"Load mode: {'FULL RELOAD' if full_reload else 'INCREMENTAL (append new rows only)'}")

    classifications = {
        key: classify_entity(key[0], key[1], df, overrides) for key, df in entity_frames.items()
    }
    for (domain, entity), cls in classifications.items():
        log(f"  classified {domain}.{entity} -> {cls}")

    dim_date = build_dim_date(entity_frames)
    load_dim_date_incremental(engine, dim_date, full_reload)

    dim_registry = {}
    for (domain, entity), df in entity_frames.items():
        if classifications[(domain, entity)] != "dimension":
            continue
        dim_df, natural_id = build_dimension_table(entity, df)
        full_dim = load_dimension_incremental(engine, entity, dim_df, natural_id, full_reload)
        dim_registry[entity] = (full_dim, natural_id)

    for (domain, entity), df in entity_frames.items():
        if classifications[(domain, entity)] != "fact":
            continue
        fact_df = build_fact_table(domain, entity, df, dim_registry)
        load_fact_incremental(engine, domain, entity, fact_df, full_reload)

    log("Silver -> Gold load complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/refresh the Gold star schema from Silver.")
    parser.add_argument(
        "--full-reload",
        action="store_true",
        help="Truncate and rebuild every table from scratch instead of appending only new rows "
             "(surrogate keys are reassigned). Use for bootstrapping or after a schema change.",
    )
    args = parser.parse_args()
    run(full_reload=args.full_reload)
