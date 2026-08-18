
"""The 'diagnose one group, gate it, apply-and-verify or escalate' step,
factored out so it runs identically regardless of who triggered it: the
synchronous auto-apply loop in app/routers/ingest.py, a human approval in
app/routers/approvals.py, or - the reason this needed factoring out at all -
a failure REVEALED by attempt_fix's do-no-harm re-evaluation
(app/repair.py) from either of those two paths. A revealed failure gets
diagnosed and gated exactly like any other detected one; there is no
second, lesser code path for it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.baseline_sanity import BaselineSanityError, assert_baseline_sane
from app.connectors.factory import build_connector
from app.contract import DataContract
from app.privacy.classification import PrivacyClassification
from app.correlation import CorrelatedGroup
from app.diagnosis.agent import DiagnosisOutcome, DiagnosticAgent
from app.diagnosis.models import FixAction
from app.gate import AUTO_APPLY, build_fix_spec, evaluate_gate, permitted_actions
from app.models import AgentTrace, Baseline, DataSource, Run, ValidationEvent
from app.profiling import BaselineProfiler
from app.repair import apply_fix_chain, attempt_fix, base_contract_for_run
from app.state_machine import AUTO_FIXED, AWAITING_APPROVAL, DETECTED, DIAGNOSED, REJECTED, RESOLVED, transition
from app.validation.engine import RuleOutcome, ValidationFailure

# Reveal chains currently terminate only because no allowlisted action can
# undo another's precondition - an accident of ALLOWLIST_ACTIONS (app/actions.py),
# not a structural guarantee. This bounds it explicitly: depth 0 is every
# group from a run's initial validate() pass, and a group revealed BY a
# depth-d group's fix is depth d+1. Anything still outstanding once depth
# exceeds the cap escalates without being diagnosed further.
DEFAULT_REVEAL_DEPTH_CAP = 2


def raise_revealed_events(
    db: Session, run: Run, baseline: Baseline | None, revealed: list[RuleOutcome]
) -> list[tuple[CorrelatedGroup, list[ValidationEvent]]]:
    """Part 0, corrected: a rule that was not_applicable before a fix and
    failed after was REVEALED, not caused - the fix that revealed it stays
    committed, and this is what turns each reveal into its own fresh
    validation_event/CorrelatedGroup pair, ready for process_group exactly
    like any other detected failure. Never folded into the fix's own
    verification result, and never silently dropped."""
    pairs: list[tuple[CorrelatedGroup, list[ValidationEvent]]] = []
    for rule_outcome in revealed:
        event = ValidationEvent(
            run_id=run.id,
            rule_failed=rule_outcome.rule_id,
            state=DETECTED,
            column_name=rule_outcome.column,
            detail_json=rule_outcome.detail,
            against_provisional_baseline=baseline.is_provisional if baseline is not None else False,
        )
        db.add(event)
        group = CorrelatedGroup(
            members=[ValidationFailure(rule_failed=rule_outcome.rule_id, column=rule_outcome.column, detail=rule_outcome.detail)]
        )
        pairs.append((group, [event]))
    return pairs


@dataclass
class GroupProcessingOutcome:
    contract: DataContract
    fix_chain_entry: dict | None = None
    escalated: bool = False
    # Additional (group, events) pairs to process the same way - a fix
    # applied for this group revealed a previously not_applicable rule.
    revealed: list[tuple[CorrelatedGroup, list[ValidationEvent]]] = field(default_factory=list)


NO_LLM_CONFIGURED = "escalated_no_llm"


def _no_llm_outcome() -> DiagnosisOutcome:
    """The shape process_group produces when there is no diagnostic agent to
    call at all - construction failed (missing API key, unknown provider) at
    dependency-resolution time, not a call that failed at runtime. Deliberately
    the SAME shape evaluate_gate already treats as escalate-unconditionally
    (diagnosis_json has an "error" key) - a 429 or a malformed LLM response
    escalates today via that exact check; "no LLM configured" is not a new
    gate behaviour, just a new reason to land on the one that already exists."""
    return DiagnosisOutcome(
        diagnosis_json={"error": "no_llm_configured", "detail": "no diagnostic agent is configured for this deployment"},
        risk_level=None,
        source=NO_LLM_CONFIGURED,
    )


def process_group(
    db: Session,
    run: Run,
    contract: DataContract,
    group: CorrelatedGroup,
    events: list[ValidationEvent],
    baseline: Baseline,
    diagnostic_agent: DiagnosticAgent | None,
    confidence_threshold: float,
) -> GroupProcessingOutcome:
    """Diagnose -> gate -> (apply-and-verify or escalate) for one group.
    Always transitions detected -> diagnosed first, even when diagnosis
    itself failed - a failure record is still a diagnosis outcome, never a
    skipped state. diagnostic_agent=None (no LLM configured anywhere in this
    deployment) is handled the same way: every group escalates, nothing
    raises."""
    # The run's PII classification travels to the egress boundary with the
    # contract. Without it the diagnosis sample would leave unmasked.
    privacy = PrivacyClassification.from_dict(run.privacy_classification)
    outcome = (
        diagnostic_agent.diagnose_group(group, baseline.profile_json, contract, privacy=privacy)
        if diagnostic_agent is not None
        else _no_llm_outcome()
    )
    for event in events:
        transition(event.state, DIAGNOSED)
        event.state = DIAGNOSED
        event.diagnosis_json = outcome.diagnosis_json
        event.risk_level = outcome.risk_level
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="diagnosis",
            input_summary=f"group_size={len(group.members)} correlation_rule={group.correlation_rule}",
            output_summary=json.dumps(
                {
                    "source": outcome.source,
                    "model_name": outcome.model_name,
                    "temperature": outcome.temperature,
                    "diagnosis": outcome.diagnosis_json,
                }
            ),
            confidence_score=outcome.diagnosis_json.get("confidence"),
        )
    )

    gate_decision = evaluate_gate(group, outcome.diagnosis_json, confidence_threshold)
    gate_trace = {
        "decision": gate_decision.decision,
        "action": gate_decision.action.value if gate_decision.action else None,
        "reasons": gate_decision.reasons,
    }

    result = GroupProcessingOutcome(contract=contract)

    if gate_decision.decision == AUTO_APPLY:
        spec = build_fix_spec(group, gate_decision.action)
        repair_result = attempt_fix(contract, group, gate_decision.action, spec, baseline.profile_json)
        contract.data = repair_result.new_df
        result.contract = contract
        if repair_result.verified:
            result.fix_chain_entry = {"action": gate_decision.action.value, "spec": spec}
            for event in events:
                transition(event.state, AUTO_FIXED)
                event.state = AUTO_FIXED
                event.action_taken = "auto_fixed"
                event.reversal_json = repair_result.reversal_record
                event.gate_reasons_json = gate_decision.reasons
            if repair_result.revealed_failures:
                result.revealed = raise_revealed_events(db, run, baseline, repair_result.revealed_failures)
        else:
            result.escalated = True
            for event in events:
                transition(event.state, AWAITING_APPROVAL)
                event.state = AWAITING_APPROVAL
                event.action_taken = "auto_fix_reverted"
                event.reversal_json = repair_result.reversal_record
                event.gate_reasons_json = [*gate_decision.reasons, f"post-condition failed: {repair_result.error}"]
        gate_trace["repair_verified"] = repair_result.verified
        gate_trace["repair_error"] = repair_result.error
    else:
        result.escalated = True
        for event in events:
            transition(event.state, AWAITING_APPROVAL)
            event.state = AWAITING_APPROVAL
            event.gate_reasons_json = gate_decision.reasons

    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="gate",
            input_summary=f"group_size={len(group.members)} confidence_threshold={confidence_threshold}",
            output_summary=json.dumps(gate_trace),
        )
    )

    return result


@dataclass
class QueueOutcome:
    contract: DataContract
    fix_chain: list[dict] = field(default_factory=list)
    escalated: bool = False
    max_depth_reached: int = 0
    revealed_count: int = 0


def process_queue(
    db: Session,
    run: Run,
    contract: DataContract,
    initial: list[tuple[CorrelatedGroup, list[ValidationEvent]]],
    baseline: Baseline,
    diagnostic_agent: DiagnosticAgent | None,
    confidence_threshold: float,
    reveal_depth_cap: int = DEFAULT_REVEAL_DEPTH_CAP,
    start_depth: int = 0,
) -> QueueOutcome:
    """Drains the group-processing queue breadth-first, bounding the reveal
    chain at `reveal_depth_cap`. Depth `start_depth` (0 for a run's initial
    validate() pass; 1 for the run_snapshots.py path via a human APPROVE,
    since the just-approved fix is itself the depth-0 step there) is where
    every seed group starts; a group revealed BY processing a depth-d group
    is depth d+1.

    Anything still outstanding once depth exceeds the cap escalates to
    awaiting_approval WITHOUT being diagnosed - the cap itself is recorded
    as the reason, not a fabricated gate decision. This is what makes
    termination a guarantee rather than an accident of which actions happen
    to be on the allowlist: no matter how a future action might chain into
    revealing another rule, the chain cannot run past `reveal_depth_cap`
    levels deep.
    """
    fix_chain: list[dict] = []
    escalated = False
    max_depth_reached = start_depth
    revealed_count = 0
    queue: list[tuple[CorrelatedGroup, list[ValidationEvent], int]] = [(g, e, start_depth) for g, e in initial]

    while queue:
        group, events, depth = queue.pop(0)
        max_depth_reached = max(max_depth_reached, depth)

        if depth > reveal_depth_cap:
            escalated = True
            reason = f"reveal-depth cap ({reveal_depth_cap}) reached; this failure was not diagnosed further"
            for event in events:
                # Same shape as a failed diagnosis elsewhere in this
                # pipeline (app/diagnosis/agent.py's escalated_* sources):
                # detected -> diagnosed is unconditional, even when the
                # "diagnosis" is a stand-in recording why one never ran, so
                # this never needs a state-machine bypass edge.
                transition(event.state, DIAGNOSED)
                event.state = DIAGNOSED
                event.diagnosis_json = {"error": "reveal_depth_cap_reached", "detail": reason}
                transition(event.state, AWAITING_APPROVAL)
                event.state = AWAITING_APPROVAL
                event.gate_reasons_json = [reason]
            db.add(
                AgentTrace(
                    run_id=run.id,
                    agent_name="gate",
                    input_summary=f"group_size={len(group.members)} depth={depth}",
                    output_summary=json.dumps({"decision": "escalate_depth_cap", "reveal_depth_cap": reveal_depth_cap, "reason": reason}),
                )
            )
            continue

        result = process_group(db, run, contract, group, events, baseline, diagnostic_agent, confidence_threshold)
        contract = result.contract
        if result.fix_chain_entry is not None:
            fix_chain.append(result.fix_chain_entry)
        if result.escalated:
            escalated = True
        if result.revealed:
            revealed_count += len(result.revealed)
            queue.extend((g, e, depth + 1) for g, e in result.revealed)

    run.reveal_depth_reached = max(run.reveal_depth_reached or 0, max_depth_reached)

    return QueueOutcome(
        contract=contract, fix_chain=fix_chain, escalated=escalated, max_depth_reached=max_depth_reached, revealed_count=revealed_count
    )


def reveal_depth_cap_from_env(default: int = DEFAULT_REVEAL_DEPTH_CAP) -> int:
    return int(os.environ.get("REVEAL_DEPTH_CAP", default))


# =============================================================================
# Phase 8: human-resolution decisions for an escalated (AWAITING_APPROVAL)
# validation-event group - approve / reject_fix / reject_data /
# accept_as_baseline.
#
# Moved here (from app/routers/approvals.py, pre-migration) so BOTH the
# graph's await_human node and, for anything the graph does not own
# (baselines, connector warnings, query/model runs), the router can call
# the exact same functions - never two implementations of "what approve
# means" for the same kind of decision.
#
# DELIBERATELY DO NOT decide what runs next (explore/narrate) - that
# decision belongs entirely to the graph's OWN routing
# (app/graph/nodes.py::resolve_node re-checking "is anything still
# awaiting_approval" immediately after one of these applies), not to the
# decision function itself. Pre-migration, _maybe_resume_run conflated
# "apply this decision" with "check whether the whole run can proceed" -
# the graph model is what makes it natural to finally separate them.
#
# PRECONDITION VALIDATION (state==AWAITING_APPROVAL, decision recognized,
# for approve: a valid matrix-permitted suggested_fix.action, an active
# baseline) is the CALLER's job (app/routers/approvals.py's HTTP-facing
# checks, replayed by app/graph/nodes.py::await_human_node against
# CURRENT state before calling any of these - Rule D). These functions
# assume their preconditions hold and raise DecisionError if they
# discover, from the database itself, that they do not - never HTTPException,
# since they have no HTTP context of their own.
# =============================================================================


class DecisionError(Exception):
    """A decision could not be applied as requested. Callers with HTTP
    context (app/routers/approvals.py) translate this to a 4xx; the graph
    node (app/graph/nodes.py) treats it as a reconciliation no-op per Rule D
    rather than a system failure."""


@dataclass
class DecisionOutcome:
    decision: str
    applied: bool | None = None
    new_baseline_id: str | None = None
    error: str | None = None


def apply_reject_fix(db: Session, group_events: list[ValidationEvent], resolved_by: str) -> DecisionOutcome:
    """Data is acceptable as-is; no fix applied; the run proceeds."""
    for e in group_events:
        transition(e.state, REJECTED)
        e.state = REJECTED
        e.action_taken = "rejected_fix_data_acceptable"
        e.resolved_by = resolved_by
        e.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return DecisionOutcome(decision="reject_fix")


def apply_reject_data(db: Session, run: Run, group_events: list[ValidationEvent], resolved_by: str) -> DecisionOutcome:
    """Data is unfit; the run terminates as failed. No fix, no fix_chain
    entry, no further gate activity - this run's output is not to be
    trusted at all. This IS the graph's reject_data-terminates-the-run
    edge; the caller (app/graph/nodes.py::await_human_node) still routes to
    the graph's own terminal "failed" sink afterward, but run.status is set
    here, atomically with the event transitions, so it is never possible
    to observe one without the other."""
    for e in group_events:
        transition(e.state, REJECTED)
        e.state = REJECTED
        e.action_taken = "rejected_data_unfit"
        e.resolved_by = resolved_by
        e.resolved_at = datetime.now(timezone.utc)
    run.status = "failed"
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return DecisionOutcome(decision="reject_data")


def apply_accept_as_baseline(
    db: Session, run: Run, source: DataSource, group_events: list[ValidationEvent], resolved_by: str
) -> DecisionOutcome:
    """The flagged change is legitimate, not corruption: recompute the
    baseline from the current (repaired-so-far) data and supersede the old
    one, so the same shape/distribution stops being flagged on future ingests."""
    old_baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    if old_baseline is None:
        raise DecisionError("no active baseline for this source")

    connector = build_connector(source)
    base_contract = base_contract_for_run(run, source, connector)
    repaired = apply_fix_chain(base_contract.data, run.fix_chain, baseline_profile=old_baseline.profile_json)

    new_profile = BaselineProfiler().profile(repaired)
    try:
        assert_baseline_sane(new_profile)
    except BaselineSanityError as sanity_error:
        raise DecisionError(f"candidate baseline failed sanity floors: {sanity_error.reasons}") from sanity_error

    old_baseline.is_active = False
    new_baseline = Baseline(source_id=source.id, profile_json=new_profile, is_active=True, is_provisional=False)
    db.add(new_baseline)

    for e in group_events:
        transition(e.state, RESOLVED)
        e.state = RESOLVED
        e.action_taken = "accepted_as_new_baseline"
        e.resolved_by = resolved_by
        e.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(new_baseline)
    return DecisionOutcome(decision="accept_as_baseline", new_baseline_id=new_baseline.id)


def apply_approve(
    db: Session,
    run: Run,
    source: DataSource,
    event: ValidationEvent,
    group_events: list[ValidationEvent],
    resolved_by: str,
    diagnostic_agent: DiagnosticAgent | None,
    confidence_threshold: float,
    reveal_depth_cap: int,
) -> DecisionOutcome:
    """Re-derive the repaired frame from source, apply the suggested fix on
    top, and require the SAME post-condition verification an auto-apply
    would have needed - a human clicking approve doesn't bypass the matrix
    or the verify-or-revert loop, it just supplies the missing risk/
    confidence trust the gate withheld."""
    group = CorrelatedGroup(
        members=[ValidationFailure(rule_failed=e.rule_failed, column=e.column_name, detail=e.detail_json) for e in group_events],
        correlation_rule=event.correlation_rule,
    )
    diagnosis = event.diagnosis_json or {}
    suggested = diagnosis.get("suggested_fix") or {}
    try:
        action = FixAction(suggested.get("action"))
    except ValueError:
        raise DecisionError("diagnosis has no valid suggested_fix.action to apply") from None

    if action not in permitted_actions(group):
        raise DecisionError(f"'{action.value}' is not a matrix-permitted action for this rule family")

    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    if baseline is None:
        raise DecisionError("no active baseline for this source; cannot verify a repair")

    connector = build_connector(source)
    base_contract = base_contract_for_run(run, source, connector)
    repaired_prior = apply_fix_chain(base_contract.data, run.fix_chain, baseline_profile=baseline.profile_json)
    contract = DataContract(
        data=repaired_prior,
        source_type=base_contract.source_type,
        source_id=base_contract.source_id,
        connector_metadata=base_contract.connector_metadata,
        detected_encoding=base_contract.detected_encoding,
        encoding_confidence=base_contract.encoding_confidence,
    )

    spec = build_fix_spec(group, action)
    repair_result = attempt_fix(contract, group, action, spec, baseline.profile_json)

    if repair_result.verified:
        run.fix_chain = [*(run.fix_chain or []), {"action": action.value, "spec": spec}]
        for e in group_events:
            transition(e.state, RESOLVED)
            e.state = RESOLVED
            e.action_taken = "approved_and_applied"
            e.reversal_json = repair_result.reversal_record
            e.resolved_by = resolved_by
            e.resolved_at = datetime.now(timezone.utc)

        # A human approval goes through the exact same do-no-harm
        # re-evaluation as an auto-apply, and a fix it approves can reveal
        # a rule the same way - not_applicable before, failed after. The
        # just-approved fix is itself depth 0, so its reveals start at
        # depth 1 - process_queue (which ALREADY drains its own reveal
        # chain internally, breadth-first, up to reveal_depth_cap) bounds
        # the chain from there exactly like the ingest auto-apply path does.
        contract.data = repair_result.new_df
        initial_reveals = raise_revealed_events(db, run, baseline, repair_result.revealed_failures)
        queue_outcome = process_queue(
            db, run, contract, initial_reveals, baseline, diagnostic_agent, confidence_threshold, reveal_depth_cap, start_depth=1
        )
        if queue_outcome.fix_chain:
            run.fix_chain = [*(run.fix_chain or []), *queue_outcome.fix_chain]
        db.commit()
        return DecisionOutcome(decision="approve", applied=True)

    # Approved, but the fix didn't actually resolve the failure it was meant
    # to. awaiting_approval can only legally end at resolved or rejected -
    # this cannot loop back to awaiting_approval, and it plainly isn't
    # resolved, so it lands on rejected with the failure recorded. This is a
    # success of the verification layer, not a system failure: a wrong or
    # optimistic human approval still can't corrupt the data silently.
    for e in group_events:
        transition(e.state, REJECTED)
        e.state = REJECTED
        e.action_taken = "approved_fix_failed_verification"
        e.gate_reasons_json = [*(e.gate_reasons_json or []), f"approved fix did not verify: {repair_result.error}"]
        e.resolved_by = resolved_by
        e.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return DecisionOutcome(decision="approve", applied=False, error=repair_result.error)
