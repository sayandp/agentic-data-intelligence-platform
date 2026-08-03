import numpy as np
import pandas as pd
import pytest

from app.contract import DataContract, SourceType
from app.correlation import RENAME_PAIR, CorrelatedGroup
from app.diagnosis.models import FixAction
from app.profiling import BaselineProfiler
from app.repair import apply_fix_chain, attempt_fix
from app.validation.engine import RuleOutcome, RuleStatus, ValidationFailure


@pytest.fixture
def clean_df():
    return pd.DataFrame(
        {
            "id": range(60),
            "amount": np.linspace(1.0, 100.0, 60),
            "city": (["New York", "Los Angeles", "Chicago"] * 20),
        }
    )


@pytest.fixture
def baseline_profile(clean_df):
    return BaselineProfiler().profile(clean_df)


def _contract(df) -> DataContract:
    return DataContract(data=df, source_type=SourceType.FILE, source_id="src-1")


def test_verified_rename_is_committed(clean_df, baseline_profile):
    renamed = clean_df.rename(columns={"city": "town"})
    group = CorrelatedGroup(
        members=[
            ValidationFailure(rule_failed="schema_conformance:missing_column:city", column="city", detail={"expected_dtype": "str"}),
            ValidationFailure(rule_failed="schema_conformance:unexpected_column:town", column="town", detail={"actual_dtype": "str"}),
        ],
        correlation_rule=RENAME_PAIR,
    )

    result = attempt_fix(
        _contract(renamed), group, FixAction.RENAME_COLUMN, {"current_name": "town", "target_name": "city"}, baseline_profile
    )

    assert result.verified
    assert "city" in result.new_df.columns
    assert "town" not in result.new_df.columns


def test_verified_dtype_cast_is_committed(clean_df, baseline_profile):
    corrupted = clean_df.copy()
    corrupted["amount"] = corrupted["amount"].astype(str)
    group = CorrelatedGroup(
        members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})]
    )

    result = attempt_fix(
        _contract(corrupted), group, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"}, baseline_profile
    )

    assert result.verified
    assert result.new_df["amount"].dtype == "float64"


def test_unresolved_fix_is_reverted_byte_identical_to_pre_fix(clean_df, baseline_profile):
    """strip_whitespace can't fix a case-only corruption - the categorical_drift
    check must still fire post-fix, forcing a revert to the exact pre-fix frame."""
    corrupted = clean_df.copy()
    corrupted["city"] = corrupted["city"].str.upper()  # case corruption, not whitespace
    pre_fix_snapshot = corrupted.copy()

    group = CorrelatedGroup(members=[ValidationFailure(rule_failed="categorical_drift:city", column="city", detail={})])

    result = attempt_fix(_contract(corrupted), group, FixAction.STRIP_WHITESPACE, {"column": "city"}, baseline_profile)

    assert not result.verified
    assert result.error is not None
    pd.testing.assert_frame_equal(result.new_df, pre_fix_snapshot)


def test_normalize_case_actually_resolves_the_same_corruption(clean_df, baseline_profile):
    corrupted = clean_df.copy()
    corrupted["city"] = corrupted["city"].str.upper()
    group = CorrelatedGroup(members=[ValidationFailure(rule_failed="categorical_drift:city", column="city", detail={})])

    result = attempt_fix(_contract(corrupted), group, FixAction.NORMALIZE_CASE, {"column": "city"}, baseline_profile)

    assert result.verified
    assert set(result.new_df["city"].unique()) == {"New York", "Los Angeles", "Chicago"}


def test_fix_that_unmasks_a_not_applicable_rule_is_kept_and_the_reveal_surfaced(clean_df, baseline_profile):
    """Do-no-harm, corrected: safe_type_cast on 'amount' clears its own
    dtype_mismatch. The corrupted values are 1000x the baseline scale -
    distribution_drift couldn't be scored at all while 'amount' was
    non-numeric (not_applicable, not passed - see
    ValidationEngine._check_drift), so the drift becoming visible the
    instant the cast succeeds is REVEALED, not caused. The fix is kept
    (this is not a regression: nothing that was evaluated-and-passing
    started failing), and the reveal comes back on revealed_failures for
    the caller to raise as its own validation event."""
    corrupted = clean_df.copy()
    corrupted["amount"] = (clean_df["amount"] * 1000).astype(str)

    group = CorrelatedGroup(
        members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})]
    )

    result = attempt_fix(
        _contract(corrupted), group, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"}, baseline_profile
    )

    assert result.verified
    assert result.new_failures == []
    assert result.new_df["amount"].dtype == "float64"
    assert [r.rule_id for r in result.revealed_failures] == ["distribution_drift:amount"]
    revealed = result.revealed_failures[0]
    assert revealed.status == RuleStatus.FAILED
    assert revealed.column == "amount"


def test_fix_that_only_resolves_its_own_rule_is_still_committed(clean_df, baseline_profile):
    """Sanity check: a fix that resolves its own rule and reveals nothing new
    is unaffected by the do-no-harm check."""
    corrupted = clean_df.copy()
    corrupted["amount"] = corrupted["amount"].astype(str)
    group = CorrelatedGroup(
        members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})]
    )

    result = attempt_fix(
        _contract(corrupted), group, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"}, baseline_profile
    )

    assert result.verified
    assert result.new_failures == []
    assert result.revealed_failures == []


class _StubEngineNormalizeCaseCollapse:
    """Simulates, deterministically, the failure-set transition a real
    normalize_case collapse can produce: ValidationEngine's own checks are
    column-independent (categorical_drift's raw-differs/normalized-matches
    condition is invariant under any further casefold-preserving remap, so
    a same-column collapse can only ever re-fire the SAME rule, never a
    different one - see repair.py's do-no-harm note). Cross-column fallout
    from collapsing two baseline-distinct categories (e.g. 'Active'/'active'
    canonicalizing to the same string and dragging a *different* column's
    derived grouping out of tolerance) is exactly the shape of bug the
    do-no-harm guard exists for; this stub pins that scenario down
    deterministically rather than fighting PSI/casefold arithmetic for an
    equivalent organic repro. `region` is explicitly PASSED pre-fix (not
    merely absent/not_applicable) - that's what makes its post-fix failure a
    genuine regression rather than a reveal.
    """

    def __init__(self):
        self.calls = 0

    def evaluate(self, contract, baseline_profile):
        self.calls += 1
        if "status" not in contract.data.columns:
            return []
        current_status = set(contract.data["status"].dropna().unique())
        if current_status == {"Active ", "active", "Closed"}:
            # pre-fix: status corruption failing, region evaluated and clean
            return [
                RuleOutcome("categorical_drift:status", RuleStatus.FAILED, column="status", detail={}),
                RuleOutcome("categorical_drift:region", RuleStatus.PASSED, column="region", detail={}),
            ]
        if current_status == {"active", "Closed"}:
            # post-fix: categorical_drift:status resolved, but collapsing
            # 'Active'/'active' into one category silently changed the
            # region column's implied grouping - a rule that WAS PASSING
            # pre-fix, not merely not_applicable.
            return [
                RuleOutcome("categorical_drift:status", RuleStatus.PASSED, column="status", detail={}),
                RuleOutcome("categorical_drift:region", RuleStatus.FAILED, column="region", detail={}),
            ]
        return []


def test_normalize_case_collapsing_distinct_categories_is_reverted(clean_df, baseline_profile, monkeypatch):
    """Do-no-harm (Part 0), the normalize_case/category-collapse narrative
    from the phase brief: baseline treats 'Active' and 'active' as distinct
    legitimate categories; canonicalizing a whitespace corruption on 'status'
    collapses them into one, resolving categorical_drift:status while
    tripping categorical_drift:region - a rule this fix never touched and
    that was not failing before it ran."""
    df = clean_df.copy()
    df["status"] = (["Active "] + ["active"] * 29 + ["Closed"] * 30)[:60]
    df["region"] = ["east"] * 60
    pre_fix_snapshot = df.copy()

    group = CorrelatedGroup(members=[ValidationFailure(rule_failed="categorical_drift:status", column="status", detail={})])
    engine = _StubEngineNormalizeCaseCollapse()

    def _fake_canonicalize(_df, _action, _spec, baseline_profile=None):
        from app.actions import FixOutcome

        new_df = df.copy()
        new_df["status"] = new_df["status"].map(lambda v: "active" if v.strip().casefold() == "active" else v)
        reversal = {
            "kind": "column_values",
            "column": "status",
            "original_values": {str(i): v for i, v in df["status"].items()},
        }
        return FixOutcome(True, new_df, reversal)

    monkeypatch.setattr("app.repair.apply_action", _fake_canonicalize)

    result = attempt_fix(_contract(df), group, FixAction.NORMALIZE_CASE, {"column": "status"}, baseline_profile, engine=engine)

    assert not result.verified
    assert result.new_failures == ["categorical_drift:region"]
    assert "categorical_drift:status" in result.error  # resolved rule recorded
    assert "categorical_drift:region" in result.error  # newly-introduced rule recorded
    pd.testing.assert_frame_equal(result.new_df, pre_fix_snapshot)


def test_fix_that_cannot_even_apply_is_not_verified(clean_df, baseline_profile):
    dirty = clean_df.copy()
    dirty["amount"] = dirty["amount"].astype(str)
    dirty.loc[0, "amount"] = "$not-a-number"
    group = CorrelatedGroup(
        members=[ValidationFailure(rule_failed="schema_conformance:dtype_mismatch:amount", column="amount", detail={"expected_dtype": "float64"})]
    )

    result = attempt_fix(
        _contract(dirty), group, FixAction.SAFE_TYPE_CAST, {"column": "amount", "target_dtype": "float64"}, baseline_profile
    )

    assert not result.verified
    assert result.reversal_record is None  # nothing was ever applied to revert


# ---- fix_chain replay ----


def test_apply_fix_chain_replays_committed_fixes_in_order(clean_df):
    renamed = clean_df.rename(columns={"city": "town"})
    fix_chain = [{"action": "rename_column", "spec": {"current_name": "city", "target_name": "town"}}]

    replayed = apply_fix_chain(clean_df, fix_chain)

    pd.testing.assert_frame_equal(replayed, renamed)


def test_apply_fix_chain_empty_is_identity(clean_df):
    replayed = apply_fix_chain(clean_df, [])
    pd.testing.assert_frame_equal(replayed, clean_df)
    replayed_none = apply_fix_chain(clean_df, None)
    pd.testing.assert_frame_equal(replayed_none, clean_df)


def test_apply_fix_chain_raises_on_a_broken_entry(clean_df):
    fix_chain = [{"action": "rename_column", "spec": {"current_name": "does_not_exist", "target_name": "x"}}]
    with pytest.raises(ValueError):
        apply_fix_chain(clean_df, fix_chain)


def test_apply_fix_chain_reproduces_attempt_fix_result_from_source(clean_df, baseline_profile):
    """The whole point of persisting a fix_chain instead of a mutated copy:
    replaying it against a fresh fetch from source must reproduce exactly
    what was committed during the original run."""
    corrupted = clean_df.rename(columns={"city": "town"})
    group = CorrelatedGroup(
        members=[
            ValidationFailure(rule_failed="schema_conformance:missing_column:city", column="city", detail={"expected_dtype": "str"}),
            ValidationFailure(rule_failed="schema_conformance:unexpected_column:town", column="town", detail={"actual_dtype": "str"}),
        ],
        correlation_rule=RENAME_PAIR,
    )
    spec = {"current_name": "town", "target_name": "city"}
    result = attempt_fix(_contract(corrupted), group, FixAction.RENAME_COLUMN, spec, baseline_profile)
    assert result.verified

    fix_chain = [{"action": "rename_column", "spec": spec}]
    replayed = apply_fix_chain(corrupted, fix_chain)

    pd.testing.assert_frame_equal(replayed, result.new_df)
