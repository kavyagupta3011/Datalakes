"""
Integration tests for the incremental/CDC Gold-load functions in
silver_to_gold.py. These need a real reachable PostgreSQL instance (the
`pg_engine` fixture skips the whole module automatically if one isn't
available), since the loader uses real DDL (ALTER TABLE ADD PRIMARY KEY,
CREATE INDEX, etc.) that an in-memory SQLite stand-in can't faithfully
emulate.

Each test uses a uuid-suffixed entity/domain name so it never collides with
real pipeline tables, and `cleanup_tables` drops everything afterward.
"""
import pandas as pd
import pytest

from silver_to_gold import load_dimension_incremental, load_fact_incremental

pytestmark = pytest.mark.integration


def test_dimension_first_load_assigns_sequential_keys(pg_engine, cleanup_tables, unique_suffix):
    entity = f"testwidget{unique_suffix}"
    cleanup_tables.append(f"dim_{entity}")
    dim_df = pd.DataFrame({"widget_id": ["W1", "W2", "W3"], "name": ["a", "b", "c"]})

    result = load_dimension_incremental(pg_engine, entity, dim_df, "widget_id", full_reload=False)

    assert sorted(result[f"{entity}_key"]) == [1, 2, 3]
    assert len(result) == 3


def test_dimension_rerun_with_no_new_rows_appends_nothing(pg_engine, cleanup_tables, unique_suffix):
    entity = f"testwidget{unique_suffix}"
    cleanup_tables.append(f"dim_{entity}")
    dim_df = pd.DataFrame({"widget_id": ["W1", "W2", "W3"], "name": ["a", "b", "c"]})

    load_dimension_incremental(pg_engine, entity, dim_df, "widget_id", full_reload=False)
    result = load_dimension_incremental(pg_engine, entity, dim_df, "widget_id", full_reload=False)

    assert len(result) == 3
    assert sorted(result[f"{entity}_key"]) == [1, 2, 3]


def test_dimension_incremental_add_continues_keys_from_max(pg_engine, cleanup_tables, unique_suffix):
    entity = f"testwidget{unique_suffix}"
    cleanup_tables.append(f"dim_{entity}")
    first = pd.DataFrame({"widget_id": ["W1", "W2", "W3"], "name": ["a", "b", "c"]})
    load_dimension_incremental(pg_engine, entity, first, "widget_id", full_reload=False)

    second = pd.DataFrame(
        {"widget_id": ["W1", "W2", "W3", "W4", "W5"], "name": ["a", "b", "c", "d", "e"]}
    )
    result = load_dimension_incremental(pg_engine, entity, second, "widget_id", full_reload=False)

    assert len(result) == 5
    keys = sorted(result[f"{entity}_key"])
    assert keys == [1, 2, 3, 4, 5]  # no duplicate/reused keys for the original 3
    new_keys = result.loc[result["widget_id"].isin(["W4", "W5"]), f"{entity}_key"]
    assert set(new_keys) == {4, 5}


def test_full_reload_reassigns_keys_from_scratch(pg_engine, cleanup_tables, unique_suffix):
    entity = f"testwidget{unique_suffix}"
    cleanup_tables.append(f"dim_{entity}")
    first = pd.DataFrame({"widget_id": ["W1", "W2", "W3"], "name": ["a", "b", "c"]})
    load_dimension_incremental(pg_engine, entity, first, "widget_id", full_reload=False)

    result = load_dimension_incremental(pg_engine, entity, first, "widget_id", full_reload=True)
    assert sorted(result[f"{entity}_key"]) == [1, 2, 3]


def _fact_df(rows):
    """rows: list of (natural_key, amount) tuples. _row_checksum is derived
    deterministically from the natural key so re-running with the same rows
    produces the same checksums (mirrors how bronze_to_silver computes it)."""
    df = pd.DataFrame(rows, columns=["order_id", "amount"])
    df["_row_checksum"] = df["order_id"].apply(lambda v: f"chk-{v}")
    return df


def test_fact_first_load_assigns_sequential_fact_ids(pg_engine, cleanup_tables, unique_suffix):
    domain, entity = "testdom", f"orders{unique_suffix}"
    cleanup_tables.append(f"fact_{domain}_{entity}")
    fact_df = _fact_df([("O1", 10.0), ("O2", 20.0), ("O3", 30.0)])

    load_fact_incremental(pg_engine, domain, entity, fact_df, full_reload=False)

    stored = pd.read_sql_table(f"fact_{domain}_{entity}", pg_engine)
    assert sorted(stored["fact_id"]) == [1, 2, 3]


def test_fact_rerun_with_same_rows_appends_nothing(pg_engine, cleanup_tables, unique_suffix):
    domain, entity = "testdom", f"orders{unique_suffix}"
    cleanup_tables.append(f"fact_{domain}_{entity}")
    fact_df = _fact_df([("O1", 10.0), ("O2", 20.0), ("O3", 30.0)])

    load_fact_incremental(pg_engine, domain, entity, fact_df, full_reload=False)
    load_fact_incremental(pg_engine, domain, entity, fact_df, full_reload=False)

    stored = pd.read_sql_table(f"fact_{domain}_{entity}", pg_engine)
    assert len(stored) == 3  # no duplicates from the second, identical run


def test_fact_incremental_add_continues_fact_id_from_max(pg_engine, cleanup_tables, unique_suffix):
    domain, entity = "testdom", f"orders{unique_suffix}"
    cleanup_tables.append(f"fact_{domain}_{entity}")
    load_fact_incremental(
        pg_engine, domain, entity, _fact_df([("O1", 10.0), ("O2", 20.0), ("O3", 30.0)]), full_reload=False
    )

    load_fact_incremental(
        pg_engine,
        domain,
        entity,
        _fact_df([("O1", 10.0), ("O2", 20.0), ("O3", 30.0), ("O4", 40.0)]),
        full_reload=False,
    )

    stored = pd.read_sql_table(f"fact_{domain}_{entity}", pg_engine)
    assert len(stored) == 4
    assert sorted(stored["fact_id"]) == [1, 2, 3, 4]
    new_id = stored.loc[stored["order_id"] == "O4", "fact_id"].iloc[0]
    assert new_id == 4
