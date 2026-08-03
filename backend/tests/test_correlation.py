import numpy as np
import pandas as pd
import pytest

from app.contract import DataContract, SourceType
from app.correlation import RENAME_PAIR, correlate_events
from app.profiling import BaselineProfiler
from app.validation.engine import ValidationEngine
from tests.corruption import CorruptionSuite

SEED = 7


@pytest.fixture
def clean_df():
    return pd.DataFrame(
        {
            "id": range(200),
            "amount": np.linspace(10.0, 500.0, 200),
            "city": (["New York", "Los Angeles", "San Francisco", "Chicago"] * 50),
        }
    )


@pytest.fixture
def baseline_profile(clean_df):
    return BaselineProfiler().profile(clean_df)


def _contract(df) -> DataContract:
    return DataContract(data=df, source_type=SourceType.FILE, source_id="src-1")


def test_rename_pair_correlates_into_one_group(clean_df, baseline_profile):
    corrupted, _truth = CorruptionSuite().apply(clean_df, "rename_column", seed=SEED, column="city", new_name="town")
    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    groups = correlate_events(failures, baseline_profile, _contract(corrupted))

    correlated = [g for g in groups if g.is_correlated]
    assert len(correlated) == 1
    assert correlated[0].correlation_rule == RENAME_PAIR
    assert {m.column for m in correlated[0].members} == {"city", "town"}


def test_genuine_drop_plus_unrelated_column_does_not_correlate(clean_df, baseline_profile):
    """A dtype-mismatched pair (categorical dropped, numeric added) must be
    left as two independent events, not merged into a false rename."""
    corrupted = clean_df.drop(columns=["city"]).copy()
    corrupted["extra_metric"] = np.arange(len(corrupted), dtype=float)

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)
    assert any(f.column == "city" for f in failures)  # missing_column
    assert any(f.column == "extra_metric" for f in failures)  # unexpected_column

    groups = correlate_events(failures, baseline_profile, _contract(corrupted))

    assert not any(g.is_correlated for g in groups)
    singleton_columns = {m.column for g in groups for m in g.members}
    assert {"city", "extra_metric"} <= singleton_columns


def test_multiple_rename_candidates_pair_by_profile_similarity(clean_df, baseline_profile):
    """Two simultaneous renames on differently-typed columns must each pair
    with their own partner, not cross-match (city<->amt, amount<->town)."""
    specs = [
        ("rename_column", {"column": "city", "new_name": "town"}),
        ("rename_column", {"column": "amount", "new_name": "amt"}),
    ]
    corrupted, _truths = CorruptionSuite().compose(clean_df, specs, seed=SEED)

    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)
    groups = correlate_events(failures, baseline_profile, _contract(corrupted))

    correlated = [g for g in groups if g.is_correlated]
    assert len(correlated) == 2
    paired_column_sets = [{m.column for m in g.members} for g in correlated]
    assert {"city", "town"} in paired_column_sets
    assert {"amount", "amt"} in paired_column_sets


def test_non_schema_failures_pass_through_uncorrelated(clean_df, baseline_profile):
    corrupted, _truth = CorruptionSuite().apply(clean_df, "inject_nulls", seed=SEED, column="amount", rate=0.3)
    failures = ValidationEngine().validate(_contract(corrupted), baseline_profile)

    groups = correlate_events(failures, baseline_profile, _contract(corrupted))

    assert all(not g.is_correlated for g in groups)
    assert sum(len(g.members) for g in groups) == len(failures)


def test_no_missing_or_unexpected_columns_produces_only_singletons(clean_df, baseline_profile):
    failures = ValidationEngine().validate(_contract(clean_df), baseline_profile)
    groups = correlate_events(failures, baseline_profile, _contract(clean_df))
    assert groups == []  # clean data: no failures, no groups at all
