"""
build_catalog.py
-----------------
Lightweight, file-based registration/discovery layer for the Bronze zone.

By design this is NOT a database-backed governance catalog (no users table,
no RBAC, no permissions schema) - just enough metadata to make every file
traceable: where it came from, what domain/entity it belongs to, its
fingerprint, and whether it's a duplicate of something already seen. This
keeps the project focused on "generic lakehouse + cleaning + OLAP" instead
of enterprise metadata plumbing.

Output: catalog/catalog.json - re-built (and merged) on every run.

Run: python src/build_catalog.py
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from common import BRONZE_DIR, CATALOG_FILE, log, read_json, sha256_file, write_json

DATE_TOKEN_RE = re.compile(r"^\d{4,8}$")


def infer_domain_entity(path: Path):
    """
    Convention: bronze/<domain>/<entity>_<...>.<ext>
    Files dropped directly under bronze/ (no domain subfolder) fall back to
    domain="unknown" - this is intentional, it's how the pipeline proves it
    can handle files nobody bothered to organize properly.
    """
    rel = path.relative_to(BRONZE_DIR)
    parts = rel.parts
    if len(parts) >= 2:
        domain = parts[0]
    else:
        domain = "unknown"

    stem_tokens = path.stem.split("_")
    # drop trailing date-like tokens (e.g. 20260601, 2026)
    while stem_tokens and DATE_TOKEN_RE.match(stem_tokens[-1]):
        stem_tokens.pop()
    entity = "_".join(stem_tokens) if stem_tokens else "unknown"
    return domain, entity


def build_catalog():
    existing = read_json(CATALOG_FILE, {"files": {}})
    checksums_seen = {}
    for rec in existing.get("files", {}).values():
        checksums_seen[rec["checksum"]] = rec["path"]

    entries = {}
    duplicate_count = 0
    new_count = 0

    for path in sorted(BRONZE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel_path = str(path.relative_to(BRONZE_DIR))
        checksum = sha256_file(path)
        domain, entity = infer_domain_entity(path)

        is_duplicate_of = None
        if checksum in checksums_seen and checksums_seen[checksum] != rel_path:
            is_duplicate_of = checksums_seen[checksum]
            duplicate_count += 1
        checksums_seen.setdefault(checksum, rel_path)

        prior = existing.get("files", {}).get(rel_path)
        first_seen = prior["first_seen"] if prior else datetime.now(timezone.utc).isoformat()
        if not prior:
            new_count += 1

        entries[rel_path] = {
            "path": rel_path,
            "domain": domain,
            "entity": entity,
            "format": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "checksum": checksum,
            "first_seen": first_seen,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "duplicate_of": is_duplicate_of,
        }

    catalog = {"built_at": datetime.now(timezone.utc).isoformat(), "files": entries}
    write_json(CATALOG_FILE, catalog)
    log(
        f"Catalog built: {len(entries)} files tracked "
        f"({new_count} new, {duplicate_count} duplicates flagged) -> {CATALOG_FILE}"
    )
    return catalog


if __name__ == "__main__":
    build_catalog()
