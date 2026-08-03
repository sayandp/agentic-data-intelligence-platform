"""Phase 7.5 Part 2: app/datetime_coercion.py, the shared module run once
at connector fetch time so profiling/exploration/modeling never each run
their own detection and never disagree."""

from __future__ import annotations

import pandas as pd

from app.datetime_coercion import normalize_datetime_columns


def test_already_datetime64_column_is_used_as_is():
    dates = pd.to_datetime(["2022-01-01", "2022-01-02"])
    df = pd.DataFrame({"order_date": dates})
    result = normalize_datetime_columns(df)
    assert result.coercions == []
    assert result.parse_attempts == {}
    assert pd.api.types.is_datetime64_any_dtype(result.data["order_date"])


def test_declared_sql_date_is_coerced_unconditionally():
    """Priority 2: a declared temporal SQL type is authoritative - coerced
    outright, never gated behind a parse-rate threshold."""
    df = pd.DataFrame({"order_date": ["2022-01-01", "2022-01-02", "2022-01-03"]})
    result = normalize_datetime_columns(df, declared_schema={"order_date": "DATE"})
    assert pd.api.types.is_datetime64_any_dtype(result.data["order_date"])
    assert len(result.coercions) == 1
    assert result.coercions[0]["method"] == "declared_schema"
    assert result.coercions[0]["parse_success_rate"] == 1.0
    # the declared path never populates parse_attempts (that's the
    # heuristic-only path's bookkeeping for the partial-parse rule)
    assert result.parse_attempts == {}


def test_declared_temporal_type_wins_even_with_a_low_parse_rate():
    """Authoritative means authoritative - unparseable individual values
    become NaT (a null-rate signal elsewhere), not a reason to skip
    coercion the way the heuristic path would."""
    df = pd.DataFrame({"order_date": ["2022-01-01", "garbage", "also garbage"]})
    result = normalize_datetime_columns(df, declared_schema={"order_date": "DATE"})
    assert pd.api.types.is_datetime64_any_dtype(result.data["order_date"])
    assert result.coercions[0]["parse_success_rate"] < 1.0


def test_declared_non_temporal_type_is_left_alone():
    df = pd.DataFrame({"notes": ["a", "b", "c"]})
    result = normalize_datetime_columns(df, declared_schema={"notes": "TEXT"})
    assert result.coercions == []
    assert not pd.api.types.is_datetime64_any_dtype(result.data["notes"])


def test_high_parse_rate_text_column_is_coerced():
    """Priority 3: no declared schema, but nearly every value parses as a
    date - coerced, and recorded as an actual coercion."""
    df = pd.DataFrame({"order_date": [f"2022-01-{i:02d}" for i in range(1, 29)]})
    result = normalize_datetime_columns(df)
    assert pd.api.types.is_datetime64_any_dtype(result.data["order_date"])
    assert result.parse_attempts["order_date"]["coerced"] is True
    assert result.parse_attempts["order_date"]["parse_rate"] == 1.0
    assert any(c["column"] == "order_date" and c["method"] == "parsed_text" for c in result.coercions)


def test_partial_parse_column_is_left_untouched_and_recorded():
    """A column where most values look like dates and some don't is a
    data-quality signal, not an ambiguous type - never silently coerced
    with the failures null'd out."""
    values = [f"2022-01-{i:02d}" for i in range(1, 8)] + ["not a date"] * 3  # 7/10 = 70%
    df = pd.DataFrame({"maybe_date": values})
    result = normalize_datetime_columns(df, min_parseable_ratio=0.99)

    assert not pd.api.types.is_datetime64_any_dtype(result.data["maybe_date"])
    assert result.coercions == []
    assert result.parse_attempts["maybe_date"]["coerced"] is False
    assert 0.0 < result.parse_attempts["maybe_date"]["parse_rate"] < 1.0
    # untouched means untouched - the original string values survive
    assert list(result.data["maybe_date"]) == values


def test_bare_month_names_are_not_misidentified_as_dates():
    """pandas' free-text date parser is liberal enough to accept a bare
    month name ("jan") as a valid date, defaulting year/day - which would
    otherwise silently misclassify an ordinary categorical column as
    datetime. A value with no digit at all is never even attempted."""
    df = pd.DataFrame({"month": ["jan", "jan", "feb"]})
    result = normalize_datetime_columns(df)
    assert not pd.api.types.is_datetime64_any_dtype(result.data["month"])
    assert result.coercions == []
    assert "month" not in result.parse_attempts  # never even a candidate


def test_numeric_and_boolean_columns_are_never_datetime_candidates():
    df = pd.DataFrame({"amount": [1.0, 2.0, 3.0], "flag": [True, False, True]})
    result = normalize_datetime_columns(df, declared_schema={"amount": "DATE", "flag": "DATE"})
    assert result.coercions == []
    assert result.parse_attempts == {}


def test_purely_non_date_text_column_produces_no_attempt_record():
    df = pd.DataFrame({"city": ["New York", "Los Angeles", "Chicago"]})
    result = normalize_datetime_columns(df)
    assert result.coercions == []
    assert result.parse_attempts == {}


def test_empty_and_all_null_columns_are_skipped_without_error():
    df = pd.DataFrame({"empty": pd.array([None, None], dtype="object")})
    result = normalize_datetime_columns(df)
    assert result.coercions == []
    assert result.parse_attempts == {}
