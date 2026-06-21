"""
Unit tests for bronze_to_silver.infer_and_cast() - the boolean / numeric /
datetime / string type-inference heuristic applied to every Silver column.

Includes a regression test for a real bug found during manual API testing:
pd.to_datetime() on already-numeric values (e.g. floats read from an Excel
column) interprets them as nanosecond-epoch offsets instead of failing, which
used to make ordinary numeric measures (order totals, scores, ...) get
misclassified as dates before the numeric check ever ran. Numeric inference
now runs first; this test pins that ordering down so it can't regress.
"""
import pandas as pd

from bronze_to_silver import infer_and_cast


def test_pure_numeric_with_missing_marker_classified_as_numeric():
    # Mirrors the synthetic "Order Total" column: mostly floats, ~5% "N/A".
    series = pd.Series([12.5, 99.0, "N/A", 250.75, 4.2])
    casted, label = infer_and_cast(series)
    assert label == "numeric"
    assert pd.api.types.is_numeric_dtype(casted)
    assert casted.isna().sum() == 1  # "N/A" coerced to NaN, not dropped/crashed


def test_numeric_not_misclassified_as_datetime_regression():
    # Plain floats - must NOT be classified as datetime just because
    # pd.to_datetime() can "successfully" parse them as epoch offsets.
    series = pd.Series([423.45, 19.99, 5.0, 88.10, 500.0])
    casted, label = infer_and_cast(series)
    assert label == "numeric"
    assert not pd.api.types.is_datetime64_any_dtype(casted)


def test_genuine_date_strings_classified_as_datetime():
    series = pd.Series(["2026-01-01", "2026-02-15", "06/03/2026", "2026-04-20"])
    casted, label = infer_and_cast(series)
    assert label == "datetime"
    assert pd.api.types.is_datetime64_any_dtype(casted)


def test_boolean_values_classified_as_boolean():
    series = pd.Series(["true", "false", "TRUE", "False", "true"])
    casted, label = infer_and_cast(series)
    assert label == "boolean"
    assert set(casted.dropna().unique()) <= {True, False}


def test_mostly_free_text_classified_as_string():
    series = pd.Series(["Great service!", "Could be faster", "", "Loved it", "meh"])
    _, label = infer_and_cast(series)
    assert label == "string"


def test_empty_series_classified_as_empty():
    series = pd.Series([None, None, None])
    _, label = infer_and_cast(series)
    assert label == "empty"


def test_below_threshold_mixed_values_fall_back_to_string():
    # Only 1/5 values is numeric-parseable (20% < 70% threshold), and none
    # parse as dates either -> falls through to string.
    series = pd.Series(["abc", "def", "ghi", "jkl", "34"])
    _, label = infer_and_cast(series)
    assert label == "string"
