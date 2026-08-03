import numpy as np
import pandas as pd
import pytest

from app.actions import apply_action, revert_action
from app.diagnosis.models import FixAction


@pytest.fixture
def df():
    return pd.DataFrame(
        {
            "id": range(10),
            "amount": np.linspace(1.0, 10.0, 10),
            "city": ["New York", "Los Angeles"] * 5,
        }
    )


# ---- rename_column ----


def test_rename_column_renames_and_reverses_exactly(df):
    outcome = apply_action(df, FixAction.RENAME_COLUMN, {"current_name": "city", "target_name": "town"})

    assert outcome.success
    assert "town" in outcome.new_df.columns
    assert "city" not in outcome.new_df.columns

    reverted = revert_action(outcome.new_df, outcome.reversal_record)
    pd.testing.assert_frame_equal(reverted, df)


def test_rename_column_rejects_missing_source_column(df):
    outcome = apply_action(df, FixAction.RENAME_COLUMN, {"current_name": "not_a_column", "target_name": "x"})
    assert not outcome.success
    assert outcome.error is not None


# ---- safe_type_cast ----


def test_safe_type_cast_succeeds_on_clean_numeric_strings_and_reverses_exactly():
    clean = pd.DataFrame({"amount": ["1.5", "2.5", "3.5"]})
    outcome = apply_action(clean, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"})

    assert outcome.success
    assert outcome.new_df["amount"].dtype == "float64"
    assert list(outcome.new_df["amount"]) == [1.5, 2.5, 3.5]

    reverted = revert_action(outcome.new_df, outcome.reversal_record)
    pd.testing.assert_frame_equal(reverted, clean)


def test_safe_type_cast_rejected_unless_100_percent_of_non_null_values_succeed():
    dirty = pd.DataFrame({"amount": ["1.5", "2.5", "$3.50"]})  # one value can't cast cleanly
    outcome = apply_action(dirty, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"})

    assert not outcome.success
    assert outcome.error is not None
    assert outcome.new_df is dirty  # unmodified


def test_safe_type_cast_preserves_nulls():
    df = pd.DataFrame({"amount": ["1.5", None, "3.5"]})
    outcome = apply_action(df, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"})

    assert outcome.success
    assert outcome.new_df["amount"].isna().sum() == 1


def test_safe_type_cast_rejects_missing_column(df):
    outcome = apply_action(df, FixAction.SAFE_TYPE_CAST, {"column": "nope", "target_dtype": "float64"})
    assert not outcome.success


# ---- strip_whitespace ----


def test_strip_whitespace_strips_and_reverses_exactly():
    df = pd.DataFrame({"city": ["  New York  ", "Chicago", " LA"]})
    outcome = apply_action(df, FixAction.STRIP_WHITESPACE, {"column": "city"})

    assert outcome.success
    assert list(outcome.new_df["city"]) == ["New York", "Chicago", "LA"]

    reverted = revert_action(outcome.new_df, outcome.reversal_record)
    pd.testing.assert_frame_equal(reverted, df)


def test_strip_whitespace_leaves_case_untouched():
    df = pd.DataFrame({"city": ["  NEW YORK  "]})
    outcome = apply_action(df, FixAction.STRIP_WHITESPACE, {"column": "city"})
    assert outcome.new_df["city"].iloc[0] == "NEW YORK"


# ---- normalize_case ----


def test_normalize_case_restores_baseline_canonical_form_and_reverses_exactly():
    df = pd.DataFrame({"city": ["NEW YORK", "new york", "Chicago"]})
    baseline_profile = {"columns": {"city": {"top_values": {"New York": 10, "Chicago": 5}}}}

    outcome = apply_action(df, FixAction.NORMALIZE_CASE, {"column": "city"}, baseline_profile=baseline_profile)

    assert outcome.success
    assert list(outcome.new_df["city"]) == ["New York", "New York", "Chicago"]

    reverted = revert_action(outcome.new_df, outcome.reversal_record)
    pd.testing.assert_frame_equal(reverted, df)


def test_normalize_case_leaves_unmatched_values_untouched():
    df = pd.DataFrame({"city": ["NEW YORK", "Miami"]})  # "Miami" isn't in baseline
    baseline_profile = {"columns": {"city": {"top_values": {"New York": 10}}}}

    outcome = apply_action(df, FixAction.NORMALIZE_CASE, {"column": "city"}, baseline_profile=baseline_profile)

    assert outcome.new_df["city"].iloc[1] == "Miami"


def test_normalize_case_rejects_without_baseline_profile(df):
    outcome = apply_action(df, FixAction.NORMALIZE_CASE, {"column": "city"}, baseline_profile=None)
    assert not outcome.success


# ---- dispatch ----


def test_apply_action_rejects_escalate_as_not_executable(df):
    with pytest.raises(ValueError):
        apply_action(df, FixAction.ESCALATE, {})
