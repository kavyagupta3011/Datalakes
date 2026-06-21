"""
gold_olap.py
------------
Generic OLAP cuboid materializer. Works on whatever fact_<domain>_<entity>
tables silver_to_gold.py happened to create - it does not know in advance
that "orders" or "tickets" exist.

For every fact table found it materializes:
  cuboid_<fact>_apex           - grand total (1 row) over every measure
  cuboid_<fact>_by_month       - rollup by year/month (needs a date key)
  cuboid_<fact>_by_<dim>       - slice by each dimension FK present
  cuboid_<fact>_by_month_<dim> - dice: month x dimension drill combo

It also exposes drill_to_source(), which goes the other way: given a cuboid
filter, return the underlying fact rows - which still carry the original
_bronze_path / _file_checksum lineage columns. That's the full traceability
chain: cuboid -> fact row -> Silver partition -> exact Bronze file.
"""
import pandas as pd
from sqlalchemy import inspect, text

from common import get_engine, log


def get_dim_tables(engine):
    insp = inspect(engine)
    dims = {}
    for t in insp.get_table_names():
        if t.startswith("dim_") and t != "dim_date":
            entity = t[len("dim_"):]
            dims[entity] = f"{entity}_key"
    return dims


def get_fact_tables(engine):
    insp = inspect(engine)
    return [t for t in insp.get_table_names() if t.startswith("fact_")]


def numeric_measure_cols(df, exclude):
    return [
        c
        for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude
    ]


def build_cuboids_for_fact(engine, fact_table: str, dim_key_cols: set):
    df = pd.read_sql_table(fact_table, engine)
    if df.empty:
        log(f"  {fact_table}: no rows, skipping cuboids")
        return []

    key_cols = {c for c in df.columns if c.endswith("_key")}
    dim_fks = sorted(key_cols & dim_key_cols)
    date_key_cols = sorted(c for c in key_cols if c not in dim_key_cols and c != "fact_id")
    primary_date_key = date_key_cols[0] if date_key_cols else None

    measures = numeric_measure_cols(df, exclude=key_cols | {"fact_id"})
    if not measures:
        df["_record_count"] = 1
        measures = ["_record_count"]

    agg = {m: ["sum", "count", "mean"] for m in measures}
    created = []

    # apex (grand total)
    apex = df[measures].agg(["sum", "count", "mean"]).T
    apex.columns = ["sum", "count", "mean"]
    apex = apex.reset_index().rename(columns={"index": "measure"})
    name = f"cuboid_{fact_table}_apex"
    apex.to_sql(name, engine, if_exists="replace", index=False)
    created.append(name)

    if primary_date_key:
        dim_date = pd.read_sql_table("dim_date", engine)
        merged = df.merge(
            dim_date[["date_key", "year", "month", "month_name"]],
            left_on=primary_date_key,
            right_on="date_key",
            how="left",
        )

        by_month = merged.groupby(["year", "month", "month_name"], dropna=False).agg(agg)
        by_month.columns = ["_".join(c) for c in by_month.columns]
        by_month = by_month.reset_index()
        name = f"cuboid_{fact_table}_by_month"
        by_month.to_sql(name, engine, if_exists="replace", index=False)
        created.append(name)

    for dim_fk in dim_fks:
        by_dim = df.groupby(dim_fk, dropna=False).agg(agg)
        by_dim.columns = ["_".join(c) for c in by_dim.columns]
        by_dim = by_dim.reset_index()
        name = f"cuboid_{fact_table}_by_{dim_fk}"
        by_dim.to_sql(name, engine, if_exists="replace", index=False)
        created.append(name)

        if primary_date_key:
            by_combo = merged.groupby(["year", "month", dim_fk], dropna=False).agg(agg)
            by_combo.columns = ["_".join(c) for c in by_combo.columns]
            by_combo = by_combo.reset_index()
            name = f"cuboid_{fact_table}_by_month_{dim_fk}"
            by_combo.to_sql(name, engine, if_exists="replace", index=False)
            created.append(name)

    log(f"  {fact_table}: materialized {len(created)} cuboids ({', '.join(created)})")
    return created


def build_cuboids():
    engine = get_engine()
    dims = get_dim_tables(engine)
    dim_key_cols = set(dims.values())
    facts = get_fact_tables(engine)
    if not facts:
        log("No fact_* tables found - run silver_to_gold.py first.")
        return []

    all_created = []
    for fact_table in facts:
        all_created += build_cuboids_for_fact(engine, fact_table, dim_key_cols)
    log(f"OLAP build complete: {len(all_created)} cuboid tables across {len(facts)} fact tables.")
    return all_created


def drill_to_source(fact_table: str, **filters) -> pd.DataFrame:
    """
    Traceability helper: given a cuboid grouping key (e.g. customers_key=3,
    year=2026, month=6), return the underlying fact rows - which still carry
    _bronze_path / _file_checksum so you can point at the exact Bronze file
    a cuboid number came from.
    """
    engine = get_engine()
    df = pd.read_sql_table(fact_table, engine)
    if "year" in filters or "month" in filters:
        date_key_cols = [c for c in df.columns if c.endswith("_key") and c not in ("fact_id",)]
        dim_date = pd.read_sql_table("dim_date", engine)
        for dk in date_key_cols:
            merged = df.merge(dim_date[["date_key", "year", "month"]], left_on=dk, right_on="date_key", how="left")
            if "year" in filters and (merged["year"] == filters["year"]).any():
                df = merged
                break
    for col, val in filters.items():
        if col in df.columns:
            df = df[df[col] == val]
    return df


if __name__ == "__main__":
    build_cuboids()
