"""
Unit tests for silver_to_gold.classify_entity() - the heuristic that decides
whether a Silver entity becomes a dim_<entity> or a fact_<domain>_<entity>
table in Gold, plus the config/entity_overrides.yaml override path that's
checked before the heuristic runs.
"""
import pandas as pd

from silver_to_gold import classify_entity


def _df(**cols):
    return pd.DataFrame(cols)


def test_single_id_no_measures_one_date_classified_as_dimension():
    df = _df(
        customer_id=["CUST001", "CUST002"],
        name=["Alice", "Bob"],
        signup_date=pd.to_datetime(["2026-01-01", "2026-02-01"]),
    )
    assert classify_entity("retail", "customers", df) == "dimension"


def test_two_id_columns_classified_as_fact():
    df = _df(
        order_id=["ORD001", "ORD002"],
        customer_id=["CUST001", "CUST002"],
        status=["completed", "pending"],
    )
    assert classify_entity("retail", "orders", df) == "fact"


def test_numeric_measure_classified_as_fact():
    df = _df(
        ticket_id=["TKT001", "TKT002"],
        amount=[12.5, 88.0],
    )
    assert classify_entity("support", "charges", df) == "fact"


def test_two_date_columns_classified_as_fact():
    df = _df(
        ticket_id=["TKT001", "TKT002"],
        opened_at=pd.to_datetime(["2026-01-01", "2026-01-02"]),
        closed_at=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    assert classify_entity("support", "tickets", df) == "fact"


def test_override_forces_dimension_despite_fact_shape():
    # This DataFrame has two id columns - the heuristic alone would say
    # "fact" - but an override should win.
    df = _df(order_id=["ORD001"], customer_id=["CUST001"])
    overrides = {"retail": {"orders": "dimension"}}
    assert classify_entity("retail", "orders", df, overrides) == "dimension"


def test_override_forces_fact_despite_dimension_shape():
    df = _df(customer_id=["CUST001"], name=["Alice"])
    overrides = {"retail": {"customers": "fact"}}
    assert classify_entity("retail", "customers", df, overrides) == "fact"


def test_no_matching_override_falls_back_to_heuristic():
    df = _df(customer_id=["CUST001"], name=["Alice"])
    overrides = {"retail": {"orders": "dimension"}}  # different entity
    assert classify_entity("retail", "customers", df, overrides) == "dimension"


def test_missing_overrides_file_falls_back_to_heuristic():
    df = _df(order_id=["ORD001"], customer_id=["CUST001"])
    assert classify_entity("retail", "orders", df, overrides=None) == "fact"
