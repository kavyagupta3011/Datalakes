"""
api.py
------
REST API over the Gold/OLAP layer, plus a deliberately small write surface
(upload + pipeline trigger) so the frontend can be a real full-stack app
instead of a read-only viewer. No auth - this is a portfolio demo; see
README "Design choices / scope" before pointing this at anything real.

Read endpoints:
  GET /health                 - DB connectivity check
  GET /tables                 - list dim_/fact_/cuboid_ tables, grouped
  GET /tables/{name}          - paginated rows from any one table
                                 (?limit=100&offset=0)
  GET /drill/{fact_table}     - traceability: given cuboid-style filters as
                                 query params (e.g. ?customers_key=3&year=2026),
                                 return the underlying fact rows - which still
                                 carry _bronze_path/_file_checksum lineage
                                 columns, so you can point at the exact Bronze
                                 file behind any aggregated number.

Write endpoints (small, intentionally unauthenticated demo surface):
  POST /upload                - drop a file into bronze/<domain>/ following
                                 the pipeline's naming convention
  POST /pipeline/run          - run the orchestrator in the background
  GET  /pipeline/status       - poll progress (orchestration_state.json +
                                 in-memory running flag)

Run:
  python src/api.py                       # http://localhost:8000
  uvicorn api:app --reload --port 8000     # dev mode, from src/
Docs: http://localhost:8000/docs (FastAPI auto-generates Swagger UI)
"""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from common import BRONZE_DIR, CATALOG_DIR, STRUCTURED_FORMATS, UNSTRUCTURED_FORMATS, get_engine, log, now_iso, read_json
from gold_olap import drill_to_source

app = FastAPI(
    title="Lakehouse Gold API",
    description="REST API over the Gold/OLAP layer of the generic Medallion lakehouse, with a small upload + pipeline-trigger surface for the frontend.",
    version="1.1.0",
)

# Wide open for the demo frontend (any origin/port). Tighten this if you
# ever deploy this beyond localhost/docker-compose.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _table_names():
    return inspect(get_engine()).get_table_names()


def df_to_records(df: pd.DataFrame):
    """JSON-safe records: datetimes -> ISO strings, NaN/NaT -> null."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records")


@app.get("/health")
def health():
    try:
        names = _table_names()
        return {"status": "ok", "table_count": len(names)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}")


@app.get("/tables")
def list_tables():
    names = sorted(_table_names())
    return {
        "dimensions": [t for t in names if t.startswith("dim_")],
        "facts": [t for t in names if t.startswith("fact_")],
        "cuboids": [t for t in names if t.startswith("cuboid_")],
    }


@app.get("/tables/{name}")
def get_table(
    name: str,
    limit: int = Query(100, ge=1, le=5000, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Rows to skip, for pagination"),
):
    if name not in _table_names():
        raise HTTPException(status_code=404, detail=f"table '{name}' not found - see /tables for valid names.")

    engine = get_engine()
    total = pd.read_sql_query(text(f'SELECT COUNT(*) AS n FROM "{name}"'), engine)["n"].iloc[0]
    df = pd.read_sql_query(
        text(f'SELECT * FROM "{name}" LIMIT :limit OFFSET :offset'),
        engine,
        params={"limit": limit, "offset": offset},
    )
    return {
        "table": name,
        "total_rows": int(total),
        "limit": limit,
        "offset": offset,
        "rows": df_to_records(df),
    }


@app.get("/drill/{fact_table}")
def drill(
    fact_table: str,
    request: Request,
    limit: int = Query(200, ge=1, le=5000, description="Max underlying fact rows to return"),
):
    """
    Traceability endpoint: pass any cuboid grouping key as a query param
    (e.g. /drill/fact_retail_orders?customers_key=3&year=2026&month=6) and
    get back the underlying fact rows with full lineage columns intact.
    """
    if fact_table not in _table_names() or not fact_table.startswith("fact_"):
        raise HTTPException(status_code=404, detail=f"fact table '{fact_table}' not found - see /tables for valid names.")

    filters = {}
    for key, value in request.query_params.items():
        if key == "limit":
            continue
        try:
            filters[key] = int(value)
        except ValueError:
            filters[key] = value

    df = drill_to_source(fact_table, **filters)
    return {
        "fact_table": fact_table,
        "filters": filters,
        "row_count": len(df),
        "rows": df_to_records(df.head(limit)),
    }


ALLOWED_UPLOAD_FORMATS = STRUCTURED_FORMATS | UNSTRUCTURED_FORMATS


@app.post("/upload")
async def upload_file(
    domain: str = Form(..., description="Target domain folder, e.g. retail / education / support / a new one"),
    entity: str = Form(..., description="Entity name within the domain, e.g. orders, customers"),
    file: UploadFile = File(...),
):
    """
    Demo write endpoint: drops the uploaded file into bronze/<domain>/ using
    the pipeline's <entity>_<timestamp>.<ext> naming convention, so the next
    POST /pipeline/run picks it up automatically - same as dropping a file
    in by hand. Minimal validation (extension allow-list, simple identifier
    names) - no auth. See README "Design choices / scope".
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_UPLOAD_FORMATS)}",
        )

    domain_clean = domain.strip().lower().replace(" ", "_").replace("-", "_")
    entity_clean = entity.strip().lower().replace(" ", "_").replace("-", "_")
    if not domain_clean.isidentifier() or not entity_clean.isidentifier():
        raise HTTPException(
            status_code=400,
            detail="domain/entity must be simple names (letters, numbers, underscores).",
        )

    dest_dir = BRONZE_DIR / domain_clean
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace("-", "").replace(":", "").split("+")[0].split(".")[0].replace("T", "")
    dest_path = dest_dir / f"{entity_clean}_{stamp}{suffix}"

    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    log(f"Uploaded file saved to {dest_path}")
    return {
        "status": "ok",
        "domain": domain_clean,
        "entity": entity_clean,
        "path": str(dest_path.relative_to(BRONZE_DIR.parent)),
    }


_pipeline_lock = threading.Lock()
_pipeline_state = {"running": False, "started_at": None, "finished_at": None, "last_result": None}


def _run_pipeline_background(full_reload: bool):
    with _pipeline_lock:
        _pipeline_state["running"] = True
        _pipeline_state["started_at"] = now_iso()
        _pipeline_state["finished_at"] = None
        _pipeline_state["last_result"] = None

    script = Path(__file__).resolve().parent / "orchestrator.py"
    args = [sys.executable, str(script)]
    if full_reload:
        args.append("--full-reload")

    proc = subprocess.run(args, capture_output=True, text=True)

    with _pipeline_lock:
        _pipeline_state["running"] = False
        _pipeline_state["finished_at"] = now_iso()
        _pipeline_state["last_result"] = {
            "returncode": proc.returncode,
            "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-30:]),
            "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-30:]),
        }


@app.post("/pipeline/run")
def trigger_pipeline(background_tasks: BackgroundTasks, full_reload: bool = Query(False)):
    """Runs src/orchestrator.py (all 5 steps, retry/backoff included) as a
    background subprocess. Poll GET /pipeline/status for progress."""
    with _pipeline_lock:
        if _pipeline_state["running"]:
            raise HTTPException(status_code=409, detail="a pipeline run is already in progress")
    background_tasks.add_task(_run_pipeline_background, full_reload)
    return {"status": "started", "full_reload": full_reload}


@app.get("/pipeline/status")
def pipeline_status():
    persisted = read_json(CATALOG_DIR / "orchestration_state.json", {"steps": {}})
    with _pipeline_lock:
        snapshot = dict(_pipeline_state)
    return {
        **snapshot,
        "steps": persisted.get("steps", {}),
        "updated_at": persisted.get("updated_at"),
    }


if __name__ == "__main__":
    import uvicorn

    log("Starting Gold API on http://0.0.0.0:8000 (docs at /docs) ...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
