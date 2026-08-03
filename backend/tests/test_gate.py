import pytest

from app.correlation import RENAME_PAIR, CorrelatedGroup
from app.diagnosis.models import FixAction
from app.gate import AUTO_APPLY, ESCALATE, build_fix_spec, evaluate_gate, permitted_actions
from app.validation.engine import ValidationFailure


def _rename_group() -> CorrelatedGroup:
    return CorrelatedGroup(
        members=[
            ValidationFailure(rule_failed="schema_conformance:missing_column:city", column="city", detail={"expected_dtype": "str"}),
            ValidationFailure(rule_failed="schema_conformance:unexpected_column:town", column="town", detail={"actual_dtype": "str"}),
        ],
        correlation_rule=RENAME_PAIR,
    )


def _uncorrelated_missing_group(column="amount") -> CorrelatedGroup:
    return CorrelatedGroup(
        members=[ValidationFailure(rule_failed=f"schema_conformance:missing_column:{column}", column=column, detail={"expected_dtype": "float64"})]
    )


def _dtype_mismatch_group(column="amount") -> CorrelatedGroup:
    return CorrelatedGroup(
        members=[ValidationFailure(rule_failed=f"schema_conformance:dtype_mismatch:{column}", column=column, detail={"expected_dtype": "float64", "actual_dtype": "str"})]
    )


def _categorical_drift_group(column="city") -> CorrelatedGroup:
    return CorrelatedGroup(members=[ValidationFailure(rule_failed=f"categorical_drift:{column}", column=column, detail={})])


def _null_threshold_group(column="amount") -> CorrelatedGroup:
    return CorrelatedGroup(members=[ValidationFailure(rule_failed=f"null_threshold:{column}", column=column, detail={})])


def _distribution_drift_group(column="amount") -> CorrelatedGroup:
    return CorrelatedGroup(members=[ValidationFailure(rule_failed=f"distribution_drift:{column}", column=column, detail={})])


def _row_count_drop_group() -> CorrelatedGroup:
    return CorrelatedGroup(members=[ValidationFailure(rule_failed="schema_conformance:row_count_drop", column=None, detail={})])


def _encoding_group() -> CorrelatedGroup:
    return CorrelatedGroup(members=[ValidationFailure(rule_failed="low_confidence_encoding:ascii", column=None, detail={})])


# ---- applicability matrix ----


def test_correlated_rename_pair_permits_rename_column():
    assert permitted_actions(_rename_group()) == {FixAction.RENAME_COLUMN}


def test_dtype_mismatch_permits_safe_type_cast():
    assert permitted_actions(_dtype_mismatch_group()) == {FixAction.SAFE_TYPE_CAST}


def test_categorical_drift_permits_strip_and_normalize():
    assert permitted_actions(_categorical_drift_group()) == {FixAction.STRIP_WHITESPACE, FixAction.NORMALIZE_CASE}


@pytest.mark.parametrize(
    "group_factory",
    [
        _uncorrelated_missing_group,
        _null_threshold_group,
        _distribution_drift_group,
        _row_count_drop_group,
        _encoding_group,
    ],
)
def test_everything_else_permits_nothing(group_factory):
    assert permitted_actions(group_factory()) == set()


# ---- the addendum's own test: matrix wins even when risk/confidence look perfect ----


def test_drop_column_with_wrong_but_well_formed_diagnosis_does_not_auto_apply():
    """A drop_column failure where the model - influenced or simply wrong -
    returns action=safe_type_cast, risk_level=low, confidence=0.99. Every
    LLM-reported field looks perfect; only the matrix, which the LLM has no
    input into, catches that this action was never applicable to this rule."""
    group = _uncorrelated_missing_group("amount")
    diagnosis = {
        "cause_category": "dtype_change",
        "likely_cause": "wrong or manipulated diagnosis",
        "suggested_fix": {"action": "safe_type_cast", "parameters": {}},
        "risk_level": "low",
        "confidence": 0.99,
    }

    decision = evaluate_gate(group, diagnosis)

    assert decision.decision == ESCALATE
    assert any("not permitted for this rule" in r for r in decision.reasons)


# ---- gate conditions, each independently required ----


def test_auto_applies_when_all_conditions_hold():
    group = _dtype_mismatch_group()
    diagnosis = {
        "suggested_fix": {"action": "safe_type_cast", "parameters": {}},
        "risk_level": "low",
        "confidence": 0.95,
    }
    decision = evaluate_gate(group, diagnosis, confidence_threshold=0.8)
    assert decision.decision == AUTO_APPLY
    assert decision.action == FixAction.SAFE_TYPE_CAST


def test_escalates_when_risk_level_is_high():
    group = _dtype_mismatch_group()
    diagnosis = {"suggested_fix": {"action": "safe_type_cast", "parameters": {}}, "risk_level": "high", "confidence": 0.95}
    decision = evaluate_gate(group, diagnosis, confidence_threshold=0.8)
    assert decision.decision == ESCALATE


def test_escalates_when_confidence_at_or_below_threshold():
    group = _dtype_mismatch_group()
    diagnosis = {"suggested_fix": {"action": "safe_type_cast", "parameters": {}}, "risk_level": "low", "confidence": 0.8}
    decision = evaluate_gate(group, diagnosis, confidence_threshold=0.8)
    assert decision.decision == ESCALATE


def test_escalates_on_missing_diagnosis():
    decision = evaluate_gate(_dtype_mismatch_group(), None)
    assert decision.decision == ESCALATE


def test_escalates_on_error_shaped_diagnosis():
    decision = evaluate_gate(_dtype_mismatch_group(), {"error": "parse_failure", "detail": "..."})
    assert decision.decision == ESCALATE


def test_escalates_on_unrecognized_action_string():
    diagnosis = {"suggested_fix": {"action": "delete_everything", "parameters": {}}, "risk_level": "low", "confidence": 0.99}
    decision = evaluate_gate(_dtype_mismatch_group(), diagnosis)
    assert decision.decision == ESCALATE


def test_escalate_action_itself_never_auto_applies():
    """The model can legitimately choose action=escalate; the gate must
    treat that exactly like any other non-permitted action, never apply it."""
    diagnosis = {"suggested_fix": {"action": "escalate", "parameters": {}}, "risk_level": "low", "confidence": 0.99}
    decision = evaluate_gate(_categorical_drift_group(), diagnosis)
    assert decision.decision == ESCALATE


# ---- deterministic fix spec derivation (never trusts LLM parameters) ----


def test_rename_spec_derived_from_group_members_not_diagnosis():
    spec = build_fix_spec(_rename_group(), FixAction.RENAME_COLUMN)
    # current_name is what the corrupted data has now (unexpected_column);
    # target_name is the baseline-expected name (missing_column).
    assert spec == {"current_name": "town", "target_name": "city"}


def test_safe_type_cast_spec_uses_baseline_expected_dtype_from_event_detail():
    spec = build_fix_spec(_dtype_mismatch_group("amount"), FixAction.SAFE_TYPE_CAST)
    assert spec == {"column": "amount", "target_dtype": "float64"}


def test_strip_whitespace_spec_uses_event_column():
    spec = build_fix_spec(_categorical_drift_group("city"), FixAction.STRIP_WHITESPACE)
    assert spec == {"column": "city"}
