import numpy as np
import pandas as pd
import pytest

from tests.corruption import CorruptionSuite
from tests.corruption.injectors import (
    change_dtype,
    drop_column,
    inject_nulls,
    inject_whitespace_case,
    rename_column,
    shift_distribution,
    truncate_rows,
)

SEED = 42


@pytest.fixture
def clean_df():
    return pd.DataFrame(
        {
            "id": range(200),
            "amount": np.linspace(10.0, 500.0, 200),
            "city": (["New York", "Los Angeles", "San Francisco", "Chicago"] * 50),
        }
    )


def test_rename_column_renames_and_preserves_shape(clean_df):
    corrupted, truth = rename_column(clean_df, SEED, column="city", new_name="town")

    assert "city" not in corrupted.columns
    assert "town" in corrupted.columns
    assert corrupted.shape == clean_df.shape
    assert truth.corruption_type == "rename_column"
    assert truth.expected_risk_level == "low"
    assert truth.expected_detection == "schema_conformance"
    assert truth.seed == SEED
    # original untouched
    assert "city" in clean_df.columns


def test_change_dtype_casts_numeric_to_string(clean_df):
    corrupted, truth = change_dtype(clean_df, SEED, column="amount")

    assert not pd.api.types.is_numeric_dtype(corrupted["amount"])
    assert all(isinstance(v, str) for v in corrupted["amount"])
    assert pd.api.types.is_numeric_dtype(clean_df["amount"])
    assert truth.expected_risk_level == "low"
    assert truth.seed == SEED


def test_inject_nulls_hits_requested_rate(clean_df):
    corrupted, truth = inject_nulls(clean_df, SEED, column="amount", rate=0.4, tolerance=0.05)

    null_rate = corrupted["amount"].isna().mean()
    assert null_rate == pytest.approx(0.4, abs=0.01)
    assert clean_df["amount"].isna().sum() == 0
    assert truth.expected_risk_level == "high"


def test_inject_nulls_below_tolerance_is_low_risk(clean_df):
    _, truth = inject_nulls(clean_df, SEED, column="amount", rate=0.02, tolerance=0.05)
    assert truth.expected_risk_level == "low"


def test_inject_nulls_is_reproducible_under_fixed_seed(clean_df):
    corrupted_a, _ = inject_nulls(clean_df, SEED, column="amount", rate=0.3)
    corrupted_b, _ = inject_nulls(clean_df, SEED, column="amount", rate=0.3)
    assert corrupted_a.equals(corrupted_b)


def test_inject_nulls_differs_under_different_seed(clean_df):
    corrupted_a, _ = inject_nulls(clean_df, SEED, column="amount", rate=0.3)
    corrupted_b, _ = inject_nulls(clean_df, SEED + 1, column="amount", rate=0.3)
    assert not corrupted_a.equals(corrupted_b)


def test_drop_column_removes_column(clean_df):
    corrupted, truth = drop_column(clean_df, SEED, column="city")

    assert "city" not in corrupted.columns
    assert list(corrupted.columns) == ["id", "amount"]
    assert "city" in clean_df.columns
    assert truth.expected_risk_level == "high"


def test_shift_distribution_offset_moves_mean(clean_df):
    corrupted, truth = shift_distribution(clean_df, SEED, column="amount", mode="offset", factor=1000.0)

    assert corrupted["amount"].mean() == pytest.approx(clean_df["amount"].mean() + 1000.0)
    assert truth.expected_risk_level == "high"
    assert truth.parameters["mode"] == "offset"


def test_shift_distribution_scale_multiplies_values(clean_df):
    corrupted, truth = shift_distribution(clean_df, SEED, column="amount", mode="scale", factor=3.0)

    assert corrupted["amount"].iloc[0] == pytest.approx(clean_df["amount"].iloc[0] * 3.0)
    assert truth.parameters["mode"] == "scale"


def test_inject_whitespace_case_mangles_values(clean_df):
    corrupted, truth = inject_whitespace_case(clean_df, SEED, column="city", rate=1.0)

    changed = (corrupted["city"] != clean_df["city"]).sum()
    assert changed == len(clean_df)
    # every mangled value normalizes back to a known clean value
    known = {"NEW YORK", "LOS ANGELES", "SAN FRANCISCO", "CHICAGO"}
    for value in corrupted["city"]:
        assert value.strip().upper() in known
    assert truth.expected_risk_level == "low"


def test_inject_whitespace_case_is_reproducible_under_fixed_seed(clean_df):
    corrupted_a, _ = inject_whitespace_case(clean_df, SEED, column="city", rate=0.5)
    corrupted_b, _ = inject_whitespace_case(clean_df, SEED, column="city", rate=0.5)
    assert corrupted_a.equals(corrupted_b)


def test_truncate_rows_drops_tail(clean_df):
    corrupted, truth = truncate_rows(clean_df, SEED, fraction=0.25)

    assert len(corrupted) == 150
    assert list(corrupted["id"]) == list(range(150))
    assert truth.parameters["original_row_count"] == 200
    assert truth.expected_risk_level == "high"


def test_every_ground_truth_records_its_seed(clean_df):
    suite = CorruptionSuite()
    for name in suite.names():
        _, truth = suite.apply(clean_df, name, seed=SEED)
        assert truth.seed == SEED


def test_suite_apply_all_isolates_corruptions(clean_df):
    suite = CorruptionSuite()
    results = suite.apply_all(clean_df, kwargs_by_name={"drop_column": {"column": "city"}})

    assert set(results) == set(suite.names())
    # dropping a column in one corruption must not affect another's frame
    dropped_df, _ = results["drop_column"]
    renamed_df, _ = results["rename_column"]
    assert "city" not in dropped_df.columns
    assert "city" in renamed_df.columns
    assert list(clean_df.columns) == ["id", "amount", "city"]


def test_suite_apply_all_is_reproducible_under_fixed_base_seed(clean_df):
    suite = CorruptionSuite()
    results_a = suite.apply_all(clean_df, names=["inject_nulls"], base_seed=7)
    results_b = suite.apply_all(clean_df, names=["inject_nulls"], base_seed=7)

    df_a, _ = results_a["inject_nulls"]
    df_b, _ = results_b["inject_nulls"]
    assert df_a.equals(df_b)


def test_suite_unknown_corruption_raises(clean_df):
    with pytest.raises(ValueError):
        CorruptionSuite().apply(clean_df, "not_a_real_corruption", seed=SEED)


def test_suite_compose_applies_multiple_corruptions_in_sequence(clean_df):
    suite = CorruptionSuite()
    specs = [
        ("rename_column", {"column": "city", "new_name": "town"}),
        ("change_dtype", {"column": "amount"}),
    ]

    corrupted, truths = suite.compose(clean_df, specs, seed=SEED)

    assert len(truths) == 2
    assert truths[0].corruption_type == "rename_column"
    assert truths[1].corruption_type == "change_dtype"
    # composition uses derived seeds (seed, seed+1, ...), not the same seed twice
    assert truths[0].seed == SEED
    assert truths[1].seed == SEED + 1
    # both corruptions actually landed on the one output frame
    assert "town" in corrupted.columns
    assert "city" not in corrupted.columns
    assert not pd.api.types.is_numeric_dtype(corrupted["amount"])
    # original untouched
    assert "city" in clean_df.columns
    assert pd.api.types.is_numeric_dtype(clean_df["amount"])


def test_suite_compose_is_reproducible_under_fixed_seed(clean_df):
    suite = CorruptionSuite()
    specs = [("inject_nulls", {"column": "amount", "rate": 0.3}), ("inject_whitespace_case", {"column": "city"})]

    corrupted_a, _ = suite.compose(clean_df, specs, seed=SEED)
    corrupted_b, _ = suite.compose(clean_df, specs, seed=SEED)

    assert corrupted_a.equals(corrupted_b)
