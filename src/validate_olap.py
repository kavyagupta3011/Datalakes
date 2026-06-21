"""
validate_olap.py
-----------------
Sanity-checks the Gold + OLAP layer:
  1. every fact_* table has at least one cuboid_*_apex table
  2. every cuboid table is non-empty
  3. the apex cuboid's sum/count for each measure matches an independent
     recomputation straight from the fact table (catches aggregation bugs)

Run: python src/validate_olap.py
"""
import sys

import pandas as pd
from sqlalchemy import inspect

from common import get_engine, log
from gold_olap import numeric_measure_cols


def validate():
    engine = get_engine()
    insp = inspect(engine)
    tables = insp.get_table_names()
    facts = [t for t in tables if t.startswith("fact_")]
    cuboids = [t for t in tables if t.startswith("cuboid_")]

    failures = []

    if not facts:
        failures.append("No fact_* tables found in Gold.")
    if not cuboids:
        failures.append("No cuboid_* tables found - did you run gold_olap.py?")

    for fact_table in facts:
        apex_name = f"cuboid_{fact_table}_apex"
        if apex_name not in tables:
            failures.append(f"Missing apex cuboid for {fact_table} ({apex_name})")
            continue

        fact_df = pd.read_sql_table(fact_table, engine)
        key_cols = {c for c in fact_df.columns if c.endswith("_key")} | {"fact_id"}
        measures = numeric_measure_cols(fact_df, exclude=key_cols)
        apex_df = pd.read_sql_table(apex_name, engine).set_index("measure")

        if not measures:
            measures = ["_record_count"]
            fact_df["_record_count"] = 1

        for m in measures:
            expected_sum = float(fact_df[m].sum())
            expected_count = int(fact_df[m].count())
            if m not in apex_df.index:
                failures.append(f"{apex_name}: measure '{m}' missing")
                continue
            actual_sum = float(apex_df.loc[m, "sum"])
            actual_count = int(apex_df.loc[m, "count"])
            if abs(expected_sum - actual_sum) > 1e-6 or expected_count != actual_count:
                failures.append(
                    f"{apex_name}: measure '{m}' mismatch "
                    f"(expected sum={expected_sum}, count={expected_count}; "
                    f"got sum={actual_sum}, count={actual_count})"
                )

    for cuboid in cuboids:
        n = pd.read_sql_table(cuboid, engine).shape[0]
        if n == 0:
            failures.append(f"{cuboid} is empty")

    if failures:
        log(f"VALIDATION FAILED ({len(failures)} issue(s)):")
        for f in failures:
            log(f"  - {f}")
        sys.exit(1)
    else:
        log(
            f"VALIDATION PASSED: {len(facts)} fact table(s), {len(cuboids)} cuboid(s) "
            f"all present, non-empty, and arithmetically consistent."
        )


if __name__ == "__main__":
    validate()
