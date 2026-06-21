"""
schema_engine.py
-----------------
Generic column-name harmonization engine. Same logic regardless of domain -
the only domain-specific input it consumes is config/domain_aliases.yaml,
everything else (heuristics, normalization, memory) is fully generic.

Resolution order for every raw column name (first match wins, no scoring):
  1. Previously-approved mapping memory
  2. Exact alias match from config/domain_aliases.yaml (curated, so it's
     auto-approved into memory)
  3. Heuristic name-contains rule from config/heuristics.yaml
  4. Heuristic numeric-range guess, only for unnamed/garbage column names
  5. Fallback: just the normalized raw name, flagged for human review so
     it's visible, not silently guessed

There's no numeric confidence score anywhere - trust is just "which method
resolved this column," and only mappings resolved via a curated alias (or
previously approved by a human) are auto-approved. Heuristic and fallback
resolutions are applied immediately (the pipeline never blocks on review)
but are marked unapproved/needs-review until a human confirms them via
config/domain_aliases.yaml or mappings/column_mappings.json.

Mapping memory persists to mappings/column_mappings.json so the system gets
"smarter" the more files of a given shape it sees - new domains/entities get
mapping suggestions, not just hardcoded business logic.
"""
import re
import threading
from datetime import datetime, timezone

import pandas as pd

from common import CONFIG_DIR, MAPPING_MEMORY_FILE, load_yaml, log, read_json, write_json

_ALIASES = None
_HEURISTICS = None
_MEMORY = None
_MEMORY_LOCK = threading.Lock()  # bronze_to_silver.py processes files concurrently

# Methods whose mappings get auto-approved into memory (curated/human-trusted)
# vs. ones that need a human to confirm before being treated as settled.
AUTO_APPROVED_METHODS = {"alias"}
# Methods loose enough that the resulting row should be flagged for review.
NEEDS_REVIEW_METHODS = {"heuristic_range", "fallback"}


def _normalize(name: str) -> str:
    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def _aliases():
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = load_yaml(CONFIG_DIR / "domain_aliases.yaml")
    return _ALIASES


def _heuristics():
    global _HEURISTICS
    if _HEURISTICS is None:
        _HEURISTICS = load_yaml(CONFIG_DIR / "heuristics.yaml")
    return _HEURISTICS


def _memory():
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = read_json(MAPPING_MEMORY_FILE, [])
    return _MEMORY


def _save_memory():
    write_json(MAPPING_MEMORY_FILE, _memory())


def _memory_lookup(domain, entity, raw_norm):
    for m in _memory():
        if m["domain"] == domain and m["entity"] == entity and m["raw_column"] == raw_norm:
            return m
    return None


def _memory_remember(domain, entity, raw_norm, mapped, method):
    existing = _memory_lookup(domain, entity, raw_norm)
    if existing:
        existing["last_seen"] = datetime.now(timezone.utc).isoformat()
        return
    _memory().append(
        {
            "domain": domain,
            "entity": entity,
            "raw_column": raw_norm,
            "mapped_column": mapped,
            "method": method,
            "approved": method in AUTO_APPROVED_METHODS,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
    )


def _alias_match(domain, entity, raw_norm):
    domain_block = _aliases().get(domain, {})
    entity_block = domain_block.get(entity, {})
    for canonical, alias_list in entity_block.items():
        candidates = {_normalize(canonical)} | {_normalize(a) for a in alias_list}
        if raw_norm in candidates:
            return canonical
    return None


def _heuristic_name_contains(raw_norm):
    for rule in _heuristics().get("name_contains", []):
        if rule["contains"] in raw_norm:
            return rule.get("maps_to")
    return None


def _heuristic_numeric_range(raw_norm, series: pd.Series):
    # Only applies to genuinely unhelpful column names like "unnamed_0", "col_3"
    if not re.match(r"^(unnamed|col|column|field)_?\d*$", raw_norm):
        return None
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.dropna()
    if len(valid) < max(3, 0.5 * len(series)):
        return None
    for rule in _heuristics().get("numeric_range_guess", []):
        if valid.between(rule["min"], rule["max"]).mean() > 0.9:
            return rule.get("maps_to")
    return None


def infer_column(domain, entity, raw_name, series: pd.Series = None):
    """
    Returns (mapped_name, method). First matching rule in the resolution
    order wins - no numeric scoring, just "which method resolved this."
    """
    raw_norm = _normalize(raw_name)

    mem = _memory_lookup(domain, entity, raw_norm)
    if mem and mem.get("approved"):
        return mem["mapped_column"], "memory"

    alias_hit = _alias_match(domain, entity, raw_norm)
    if alias_hit:
        _memory_remember(domain, entity, raw_norm, alias_hit, "alias")
        return alias_hit, "alias"

    heuristic_hit = _heuristic_name_contains(raw_norm)
    if heuristic_hit:
        _memory_remember(domain, entity, raw_norm, heuristic_hit, "heuristic_name")
        return heuristic_hit, "heuristic_name"

    if series is not None:
        range_hit = _heuristic_numeric_range(raw_norm, series)
        if range_hit:
            _memory_remember(domain, entity, raw_norm, range_hit, "heuristic_range")
            return range_hit, "heuristic_range"

    _memory_remember(domain, entity, raw_norm, raw_norm, "fallback")
    return raw_norm, "fallback"


def harmonize_columns(df: pd.DataFrame, domain: str, entity: str):
    """
    Renames every column in df to its canonical form. Returns (df, report)
    where report is a list of dicts describing each mapping decision -
    useful for lineage/debugging, not stored anywhere by default.
    """
    report = []
    new_names = {}
    used = set()
    with _MEMORY_LOCK:
        for col in df.columns:
            if str(col).startswith("_"):
                new_names[col] = col  # lineage / system columns pass through untouched
                continue
            mapped, method = infer_column(domain, entity, col, df[col])
            final = mapped
            suffix = 1
            while final in used:
                suffix += 1
                final = f"{mapped}_dup{suffix}"
            used.add(final)
            new_names[col] = final
            report.append(
                {
                    "raw": col,
                    "mapped": final,
                    "method": method,
                    "needs_review": method in NEEDS_REVIEW_METHODS,
                }
            )
        _save_memory()
    df = df.rename(columns=new_names)
    return df, report


def pending_review_count() -> int:
    return sum(1 for m in _memory() if not m.get("approved"))


if __name__ == "__main__":
    log(f"Mapping memory has {len(_memory())} entries, {pending_review_count()} pending review.")
