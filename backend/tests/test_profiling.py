import numpy as np
import pandas as pd
import pytest

from app.profiling import BaselineProfiler


def test_profile_row_count():
    df = pd.DataFrame({"a": [1, 2, 3, 4]})
    profile = BaselineProfiler().profile(df)
    assert profile["row_count"] == 4


def test_profile_numeric_column_stats():
    values = np.linspace(0.0, 100.0, 50)
    df = pd.DataFrame({"amount": values})
    profile = BaselineProfiler().profile(df)
    entry = profile["columns"]["amount"]

    assert entry["kind"] == "numeric"
    assert entry["dtype"] == str(df["amount"].dtype)
    assert entry["null_rate"] == 0.0
    assert entry["min"] == 0.0
    assert entry["max"] == 100.0
    assert entry["mean"] == values.mean()
    assert entry["std"] == values.std()
    assert len(entry["histogram"]["bin_edges"]) == 11
    assert len(entry["histogram"]["counts"]) == 10
    assert sum(entry["histogram"]["counts"]) == 50
    assert len(entry["sample_values"]) == 50


def test_profile_categorical_column_stats():
    df = pd.DataFrame({"city": ["NYC"] * 6 + ["LA"] * 3 + ["SF"] * 1})
    profile = BaselineProfiler().profile(df)
    entry = profile["columns"]["city"]

    assert entry["kind"] == "categorical"
    assert entry["cardinality"] == 3
    assert entry["top_values"]["NYC"] == 6
    assert entry["top_values"]["LA"] == 3
    assert entry["top_values"]["SF"] == 1


def test_profile_null_rate():
    df = pd.DataFrame({"a": [1.0, None, 3.0, None]})
    profile = BaselineProfiler().profile(df)
    assert profile["columns"]["a"]["null_rate"] == 0.5


def test_profile_boolean_column_is_categorical():
    df = pd.DataFrame({"flag": [True, False, True, True]})
    profile = BaselineProfiler().profile(df)
    assert profile["columns"]["flag"]["kind"] == "categorical"
    assert profile["columns"]["flag"]["cardinality"] == 2


def test_profile_empty_numeric_column_handles_gracefully():
    df = pd.DataFrame({"a": pd.Series([np.nan, np.nan], dtype="float64")})
    profile = BaselineProfiler().profile(df)
    entry = profile["columns"]["a"]
    assert entry["null_rate"] == 1.0
    assert entry["min"] is None
    assert entry["histogram"]["bin_edges"] == []


def test_profile_is_json_serializable():
    import json

    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    profile = BaselineProfiler().profile(df)
    json.dumps(profile)  # raises if anything is a numpy/pandas scalar


def test_profile_datetime_column_stats():
    """Phase 7.5 Part 2: a real datetime64 column is profiled as its own
    kind, never folded into categorical with a meaningless top-values table
    over individual timestamps."""
    dates = pd.date_range("2022-01-01", periods=30, freq="D")
    df = pd.DataFrame({"order_date": dates})
    profile = BaselineProfiler().profile(df)
    entry = profile["columns"]["order_date"]

    assert entry["kind"] == "datetime"
    assert "top_values" not in entry
    assert entry["min"] == dates.min().isoformat()
    assert entry["max"] == dates.max().isoformat()
    assert entry["span_days"] == 29.0


def test_profile_datetime_column_with_nulls():
    dates = pd.to_datetime(["2022-01-01", None, "2022-01-03"])
    df = pd.DataFrame({"order_date": dates})
    profile = BaselineProfiler().profile(df)
    entry = profile["columns"]["order_date"]
    assert entry["kind"] == "datetime"
    assert entry["null_rate"] == pytest.approx(1 / 3)
    assert entry["min"] is not None


def test_identifier_shaped_column_excluded_from_profiling():
    """Phase 7.5 Part 1: a primary-key-shaped column is excluded, not
    grounds to reject the whole baseline."""
    df = pd.DataFrame(
        {
            "order_id": [f"order_{i}" for i in range(50)],
            "amount": np.linspace(1.0, 100.0, 50),
        }
    )
    profile = BaselineProfiler().profile(df)

    assert "order_id" not in profile["columns"]
    assert "amount" in profile["columns"]
    excluded = {e["column"]: e["reason"] for e in profile["excluded_columns"]}
    assert "order_id" in excluded
    assert "identifier" in excluded["order_id"]


def test_numeric_and_datetime_columns_never_excluded_as_identifiers():
    """The identifier exclusion only ever applies to categorical/text
    columns - a continuous numeric measure or a genuinely unique-per-row
    timestamp is normal, not evidence of an identifier."""
    dates = pd.date_range("2022-01-01", periods=50, freq="h")  # unique per row
    df = pd.DataFrame({"order_date": dates, "price": np.linspace(1.0, 500.0, 50)})
    profile = BaselineProfiler().profile(df)

    assert profile["excluded_columns"] == []
    assert "order_date" in profile["columns"]
    assert profile["columns"]["order_date"]["kind"] == "datetime"
    assert "price" in profile["columns"]
