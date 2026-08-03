import numpy as np
import pandas as pd
import pytest

from app.baseline_sanity import BaselineSanityError, assert_baseline_sane, check_sanity_floors
from app.profiling import BaselineProfiler


def test_healthy_profile_passes():
    df = pd.DataFrame(
        {
            "id": range(50),
            "amount": np.linspace(1.0, 100.0, 50),
            "city": (["NYC", "LA", "SF", "Chicago", "Houston"] * 10),
        }
    )
    profile = BaselineProfiler().profile(df)
    assert check_sanity_floors(profile) == []
    assert_baseline_sane(profile)  # does not raise


def test_high_null_column_fails_floor():
    # Two distinct surviving values so this trips *only* the null floor, not
    # the zero-variance floor too (both would legitimately fire on constant
    # non-null data, which is a different test below).
    df = pd.DataFrame({"a": [1.0, 2.0] + [None] * 98})
    profile = BaselineProfiler().profile(df)

    reasons = check_sanity_floors(profile)

    assert len(reasons) == 1
    assert "'a'" in reasons[0]
    assert "null" in reasons[0]
    with pytest.raises(BaselineSanityError) as exc_info:
        assert_baseline_sane(profile)
    assert exc_info.value.reasons == reasons


def test_zero_variance_numeric_column_fails_floor():
    df = pd.DataFrame({"a": [42.0] * 20})
    profile = BaselineProfiler().profile(df)

    reasons = check_sanity_floors(profile)

    assert len(reasons) == 1
    assert "zero variance" in reasons[0]


def test_identifier_like_column_excluded_not_rejected():
    """Phase 7.5 Part 1: an identifier-shaped column (a primary key) is
    EXCLUDED from profiling, not grounds to reject the whole baseline - the
    baseline is still created from everything else. A primary key is not
    evidence the dataset is garbage."""
    df = pd.DataFrame(
        {
            "customer_ref": [f"cust-{i}" for i in range(20)],
            "amount": np.linspace(1.0, 100.0, 20),
        }
    )
    profile = BaselineProfiler().profile(df)

    assert "customer_ref" not in profile["columns"]
    assert "amount" in profile["columns"]
    excluded = {e["column"]: e["reason"] for e in profile["excluded_columns"]}
    assert "customer_ref" in excluded
    assert "identifier" in excluded["customer_ref"]

    assert check_sanity_floors(profile) == []
    assert_baseline_sane(profile)  # does not raise


def test_small_dataset_does_not_trip_cardinality_floor():
    """With too few rows, distinct-per-row is trivial and not a real identifier signal."""
    df = pd.DataFrame({"city": ["NYC", "LA", "SF"]})
    profile = BaselineProfiler().profile(df)

    assert check_sanity_floors(profile) == []
    assert profile["excluded_columns"] == []
    assert "city" in profile["columns"]


def test_all_columns_excluded_rejects_baseline():
    """If EVERY column is identifier-shaped, there is nothing left to
    baseline against - this is the one case identifier-shaped columns still
    cause a rejection, and only because nothing survives, not because of
    the identifier shape itself."""
    df = pd.DataFrame({"customer_ref": [f"cust-{i}" for i in range(20)]})
    profile = BaselineProfiler().profile(df)

    assert profile["columns"] == {}
    assert len(profile["excluded_columns"]) == 1

    reasons = check_sanity_floors(profile)
    assert len(reasons) == 1
    assert "excluded" in reasons[0]
    with pytest.raises(BaselineSanityError):
        assert_baseline_sane(profile)


def test_multiple_floor_violations_all_reported():
    df = pd.DataFrame(
        {
            "mostly_null": [1.0, 2.0] + [None] * 98,
            "constant": [7.0] * 100,
            "customer_ref": [f"cust-{i}" for i in range(100)],
            # Kept so the frame isn't ALL identifier-shaped - isolating null
            # and zero-variance as the only two genuine REJECTION reasons;
            # customer_ref is excluded, never a rejection reason itself.
            "amount": np.linspace(1.0, 100.0, 100),
        }
    )
    profile = BaselineProfiler().profile(df)

    reasons = check_sanity_floors(profile)

    # mostly_null -> null floor; constant -> zero-variance; customer_ref is
    # excluded from profiling, not a rejection reason at all.
    assert len(reasons) == 2
    assert any("mostly_null" in r for r in reasons)
    assert any("constant" in r for r in reasons)
    assert "customer_ref" not in profile["columns"]
