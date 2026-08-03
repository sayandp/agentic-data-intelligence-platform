"""The rule-based gate: the ONLY thing that decides whether a fix auto-applies.

CRITICAL DESIGN CONSTRAINT: the LLM's risk_level and confidence are INPUTS to
this decision, never the decision itself. A diagnosis claiming
action=safe_type_cast, risk_level=low, confidence=0.99 for what is actually a
dropped column must still be refused - the applicability matrix below is
derived from ValidationEngine/correlation output alone and is checked FIRST,
independently of anything the model said. The LLM cannot manufacture
permission the detection layer didn't already grant.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.actions import ALLOWLIST_ACTIONS
from app.correlation import RENAME_PAIR, CorrelatedGroup
from app.diagnosis.models import FixAction

DEFAULT_CONFIDENCE_THRESHOLD = 0.8

AUTO_APPLY = "auto_apply"
ESCALATE = "escalate"


@dataclass
class GateDecision:
    decision: str  # AUTO_APPLY | ESCALATE
    action: FixAction | None
    reasons: list[str]


def permitted_actions(group: CorrelatedGroup) -> set[FixAction]:
    """The applicability matrix. Everything not listed here permits nothing."""
    if group.correlation_rule == RENAME_PAIR:
        return {FixAction.RENAME_COLUMN}
    if len(group.members) == 1:
        member = group.members[0]
        if ":dtype_mismatch:" in member.rule_failed:
            return {FixAction.SAFE_TYPE_CAST}
        if member.rule_failed.startswith("categorical_drift"):
            return {FixAction.STRIP_WHITESPACE, FixAction.NORMALIZE_CASE}
    return set()


def build_fix_spec(group: CorrelatedGroup, action: FixAction) -> dict:
    """Deterministically derives the fix's parameters from the validation
    events themselves - never from diagnosis.suggested_fix.parameters."""
    if action == FixAction.RENAME_COLUMN:
        missing = next(m for m in group.members if ":missing_column:" in m.rule_failed)
        unexpected = next(m for m in group.members if ":unexpected_column:" in m.rule_failed)
        # The corrupted data currently HAS `unexpected.column` and is MISSING
        # `missing.column` (the baseline-expected name) - the fix renames the
        # former to the latter, not the other way around.
        return {"current_name": unexpected.column, "target_name": missing.column}
    if action == FixAction.SAFE_TYPE_CAST:
        member = group.members[0]
        return {"column": member.column, "target_dtype": (member.detail or {}).get("expected_dtype")}
    if action in (FixAction.STRIP_WHITESPACE, FixAction.NORMALIZE_CASE):
        return {"column": group.members[0].column}
    raise ValueError(f"no deterministic spec builder for action '{action}'")


def evaluate_gate(
    group: CorrelatedGroup,
    diagnosis_json: dict | None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> GateDecision:
    reasons: list[str] = []

    if not diagnosis_json or "error" in diagnosis_json:
        return GateDecision(ESCALATE, None, ["no valid diagnosis (parse failure or quota exhaustion)"])

    suggested = diagnosis_json.get("suggested_fix") or {}
    action_str = suggested.get("action")
    try:
        action = FixAction(action_str)
    except ValueError:
        return GateDecision(ESCALATE, None, [f"suggested action {action_str!r} is not a recognized FixAction"])

    matrix_allowed = permitted_actions(group)
    if action not in matrix_allowed:
        if matrix_allowed:
            allowed_desc = f"the matrix permits {sorted(a.value for a in matrix_allowed)} for this rule"
        else:
            # Four whole rule families (null_threshold, distribution_drift,
            # row_count_drop, both encoding checks) permit no auto-fix at
            # all, for any diagnosis, at any confidence - that's a real,
            # deliberate "nothing is allowed here", not an empty Python list
            # that happened to leak into a human-facing reason string.
            allowed_desc = "no fixes are permitted for this rule by the applicability matrix"
        reasons.append(f"action '{action.value}' not permitted for this rule - {allowed_desc}")
    if action not in ALLOWLIST_ACTIONS:
        reasons.append(f"action '{action.value}' is not on the global allowlist")

    risk_level = diagnosis_json.get("risk_level")
    if risk_level != "low":
        reasons.append(f"risk_level={risk_level!r}, not 'low'")

    # STRICT inequality: confidence <= threshold FAILS, confidence must
    # EXCEED the threshold to pass. A diagnosis sitting at EXACTLY the
    # threshold value is rejected, not accepted - deliberate (a threshold
    # is a floor a diagnosis must clear, not tie). This is directly
    # responsible for a real observed cliff (Phase 7.5 Part 5,
    # scripts/part6_evaluation.py against a live model): of 11 live
    # diagnoses, 9 sat at exactly confidence=0.95; the swept scorecard was
    # flat from threshold=0.00 through 0.94 and then dropped from 5
    # auto-applies to 0 in a single step at threshold=0.95, purely because
    # of this `<=`. At the values a real model actually returns, the
    # confidence check contributes little separating signal on its own -
    # see the README for what's actually doing the discriminating there
    # (the applicability matrix and risk_level, not this number). Kept
    # anyway, not deleted: a future or differently-calibrated model may
    # spread meaningfully, and the mechanism needs to survive to catch it
    # when it does.
    confidence = diagnosis_json.get("confidence")
    if confidence is None or confidence <= confidence_threshold:
        reasons.append(f"confidence={confidence} does not exceed threshold {confidence_threshold}")

    if reasons:
        return GateDecision(ESCALATE, action, reasons)
    return GateDecision(AUTO_APPLY, action, ["action permitted by matrix and allowlist; risk low; confidence sufficient"])
