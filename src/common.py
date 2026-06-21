"""
Shared constants, paths, DB connection and small helpers used across the
pipeline. Keeping this in one place is what lets every other module stay
generic (no hardcoded paths or connection strings scattered around).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import create_engine

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
BRONZE_DIR = ROOT / "bronze"
SILVER_DIR = ROOT / "silver"
CONFIG_DIR = ROOT / "config"
CATALOG_DIR = ROOT / "catalog"
LINEAGE_DIR = ROOT / "lineage"
MAPPINGS_DIR = ROOT / "mappings"

for d in (BRONZE_DIR, SILVER_DIR, CATALOG_DIR, LINEAGE_DIR, MAPPINGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

CATALOG_FILE = CATALOG_DIR / "catalog.json"
LINEAGE_FILE = LINEAGE_DIR / "lineage.json"
MAPPING_MEMORY_FILE = MAPPINGS_DIR / "column_mappings.json"

UNSTRUCTURED_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".mp3", ".wav", ".pdf"}
STRUCTURED_FORMATS = {".csv", ".xlsx", ".xls", ".json", ".xml"}

PIPELINE_RUN_ID = os.environ.get("PIPELINE_RUN_ID") or datetime.now(timezone.utc).strftime(
    "run_%Y%m%dT%H%M%S"
)

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Fingerprint a file for duplicate detection / change tracking."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # unique tmp name so concurrent writers (different files) never collide
    tmp = path.with_suffix(f".{threading.get_ident()}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_engine():
    """
    Single source of truth for Postgres connectivity. Reads PG_HOST / PG_PORT /
    PG_DB / PG_USER / PG_PASSWORD from the environment (see .env.example).

    For local testing without a TCP listener, PG_HOST may instead be a unix
    socket *directory* (psycopg2 / libpq treat a non-empty 'host' that looks
    like a path as a socket directory automatically) - no code changes needed.
    """
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DB", "lakehouse")
    user = os.environ.get("PG_USER", "postgres")
    password = os.environ.get("PG_PASSWORD", "")

    url = f"postgresql+psycopg2://{user}:{password}@/{db}?host={host}&port={port}"
    return create_engine(url, future=True)
