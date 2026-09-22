"""Human-in-the-loop approval surface.

Part 1 needs this for provisional-baseline confirmation (trust-on-first-use
mitigation). Part 5 extends the same GET /approvals/pending and
POST /approvals/{id}/resolve endpoints to also cover awaiting_approval
validation_events, produced by the Diagnostic Agent + gate.

RESOLUTION VOCABULARY - four distinct decisions, not a binary approve/reject:

  approve            apply the matrix-permitted suggested fix, verify, commit
  reject_fix         do not apply a fix; the data is acceptable as-is; run proceeds
  reject_data        the data is unfit; the run terminates as failed; nothing
                      downstream (no fix_chain entry, no further gate activity)
  accept_as_baseline the change is legitimate, not corruption; supersede the
                      active baseline with the current (repaired-so-far) data
                      so the same "corruption" doesn't re-fire on the next ingest

The same four decisions apply to provisional-baseline confirmation, where only
`approve` and `reject_data` are meaningful (there is no fix to reject, and a
baseline candidate accepting itself as a baseline is not a coherent action).

PHASE 8: a validation-event resolution now RESUMES THE GRAPH
(app/graph/build.py) rather than applying the decision directly - the graph
owns what happens next (resolve re-checked, explore/narrate or another
await_human pause). Baseline confirmation, connector-warning acknowledgement,
and query/model-run resolution are NOT part of the graph (approved scope,
Phase 8 Part 0): each is "re-validate and re-run from scratch" or a bare
acknowledgement, never a resumed position, so they stay exactly as they were
- plain database mutations, no checkpoint involved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.baseline_sanity import BaselineSanityError, assert_baseline_sane
from app.correlation import CorrelatedGroup
from app.db import get_db
from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.dependency import get_diagnostic_agent
from app.diagnosis.models import FixAction
from app.gate import permitted_actions
from app.graph.build import invoke_in_background
from app.modeling.models import APPROVABLE_ESCALATION_REASONS as APPROVABLE_MODEL_ESCALATION_REASONS
from app.modeling.pipeline import reject_escalated_model, resolve_escalated_model_approval
from app.models import Baseline, DataSource, ModelRun, QueryRun, Run, ValidationEvent
from app.narrative.agent import NarrativeAgent
from app.narrative.dependency import get_narrative_agent
from app.summary.dependency import get_summary_agent
from app.profiling import BaselineProfiler
from app.query.models import APPROVABLE_ESCALATION_REASONS, EscalationReason
from app.query.pipeline import reject_escalated_query, resolve_escalated_query_approval
from app.state_machine import AWAITING_APPROVAL, DETECTED, RESOLVED, transition
from app.validation.engine import CONNECTOR_WARNING, ValidationFailure

router = APIRouter(prefix="/approvals", tags=["approvals"])

EVENT_DECISIONS = {"approve", "reject_fix", "reject_data", "accept_as_baseline"}
BASELINE_DECISIONS = {"approve", "reject_data"}
QUERY_DECISIONS = {"approve", "reject_fix"}
MODEL_DECISIONS = {"approve", "reject_fix"}
# Connector warnings (Phase 7.5 Part 3) are informational by construction -
# never diagnosed, never gated, nothing to approve or reject. The only
# meaningful decision is acknowledging that a human has seen it; see
# app/state_machine.py's DETECTED -> RESOLVED edge, added for exactly this.
CONNECTOR_WARNING_DECISIONS = {"acknowledge"}

# Dashboard UX pass, Part 2: how many past items each "Recently resolved"
# expander shows, per section - a bounded read, not a full history browser.
RECENTLY_RESOLVED_LIMIT = 10


class ApprovalResolution(BaseModel):
    decision: Literal["approve", "reject_fix", "reject_data", "accept_as_baseline", "acknowledge"]
    resolved_by: str


def _serialize_baseline(baseline: Baseline) -> dict:
    return {
        "id": baseline.id,
        "source_id": baseline.source_id,
        "row_count": baseline.profile_json.get("row_count"),
        "created_at": baseline.created_at.isoformat(),
    }


def _serialize_event_group(events: list[ValidationEvent], run_numbers: dict[str, int | None]) -> dict:
    first = events[0]
    return {
        "resolve_id": first.id,  # any member id resolves the whole group together
        "correlation_group_id": first.correlation_group_id,
        "run_id": first.run_id,
        "run_number": run_numbers.get(first.run_id),
        "rules_failed": [e.rule_failed for e in events],
        "columns": [e.column_name for e in events],
        "diagnosis": first.diagnosis_json,
        "risk_level": first.risk_level,
        "gate_reasons": first.gate_reasons_json,
        "action_taken": first.action_taken,
        "created_at": first.created_at.isoformat(),
    }


def _serialize_query_run(q: QueryRun, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": q.id,
        "source_id": q.source_id,
        "run_id": q.run_id,
        "run_number": run_numbers.get(q.run_id),
        "question": q.question,
        "query_kind": q.query_kind,
        "code": q.generated_code,
        "columns_referenced": q.columns_referenced_json or [],
        "assumptions": q.assumptions_json or [],
        "confidence": q.confidence,
        "escalation_reason": q.escalation_reason,
        "escalation_detail": q.escalation_detail,
        "approvable": q.escalation_reason in {r.value for r in APPROVABLE_ESCALATION_REASONS},
        "created_at": q.created_at.isoformat(),
    }


def _serialize_model_run(m: ModelRun, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": m.id,
        "source_id": m.source_id,
        "run_id": m.run_id,
        "run_number": run_numbers.get(m.run_id),
        "question": m.question,
        "target_column": m.target_column,
        "task_type": m.task_type,
        "model_family": m.model_family,
        "candidate_scores": m.candidate_scores_json or [],
        "baseline_scores": m.baseline_scores_json or [],
        "excluded_features": m.excluded_features_json or [],
        "out_of_sample_metric": m.out_of_sample_metric,
        "out_of_sample_score": m.out_of_sample_score,
        "class_distribution": m.class_distribution_json,
        "escalation_reason": m.escalation_reason,
        "escalation_detail": m.escalation_detail,
        "approvable": m.escalation_reason in {r.value for r in APPROVABLE_MODEL_ESCALATION_REASONS},
        "created_at": m.created_at.isoformat(),
    }


def _serialize_connector_warning(e: ValidationEvent, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "run_number": run_numbers.get(e.run_id),
        "rule_failed": e.rule_failed,
        "message": (e.detail_json or {}).get("message"),
        "created_at": e.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Dashboard UX pass, Part 2: "Recently resolved" - a read over data that
# already exists (resolved_by/resolved_at, or the analogous state/is_active
# fields), never new backend state. Each serializer below mirrors its
# pending-list counterpart's shape but adds the decision actually taken,
# who took it, and when - the three things a "what happened here" history
# needs that a pending item doesn't have yet.
# ---------------------------------------------------------------------------


def _serialize_resolved_baseline(baseline: Baseline) -> dict:
    # Baseline has no separate "decision" field - _resolve_baseline's own
    # two branches are exactly is_active True (confirmed) or False
    # (rejected), so the decision is fully recoverable from state already
    # on the row, not a new column.
    return {
        "id": baseline.id,
        "source_id": baseline.source_id,
        "row_count": baseline.profile_json.get("row_count"),
        "decision": "approve" if baseline.is_active else "reject_data",
        "resolved_by": baseline.resolved_by,
        "resolved_at": baseline.resolved_at.isoformat() if baseline.resolved_at else None,
    }


def _serialize_resolved_event(e: ValidationEvent, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "run_number": run_numbers.get(e.run_id),
        "rule_failed": e.rule_failed,
        "column": e.column_name,
        "decision": e.action_taken,
        "resolved_by": e.resolved_by,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


def _serialize_resolved_connector_warning(e: ValidationEvent, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "run_number": run_numbers.get(e.run_id),
        "rule_failed": e.rule_failed,
        "message": (e.detail_json or {}).get("message"),
        "decision": e.action_taken,
        "resolved_by": e.resolved_by,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


def _serialize_resolved_query_run(q: QueryRun, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": q.id,
        "run_id": q.run_id,
        "run_number": run_numbers.get(q.run_id),
        "question": q.question,
        # QueryRun has no action_taken field of its own (only ValidationEvent
        # does) - QUERY_DECISIONS is exactly {approve, reject_fix}, a 1:1 map
        # with the only two terminal states this reaches, RESOLVED/REJECTED.
        "decision": "approve" if q.state == RESOLVED else "reject_fix",
        "resolved_by": q.resolved_by,
        "resolved_at": q.resolved_at.isoformat() if q.resolved_at else None,
    }


def _serialize_resolved_model_run(m: ModelRun, run_numbers: dict[str, int | None]) -> dict:
    return {
        "id": m.id,
        "run_id": m.run_id,
        "run_number": run_numbers.get(m.run_id),
        "target_column": m.target_column,
        "question": m.question,
        # Same reasoning as _serialize_resolved_query_run: MODEL_DECISIONS is
        # exactly {approve, reject_fix}, a 1:1 map with RESOLVED/REJECTED.
        "decision": "approve" if m.state == RESOLVED else "reject_fix",
        "resolved_by": m.resolved_by,
        "resolved_at": m.resolved_at.isoformat() if m.resolved_at else None,
    }


@router.get("/pending")
def list_pending(db: Session = Depends(get_db)):
    provisional_baselines = (
        db.query(Baseline)
        .filter(Baseline.is_provisional.is_(True), Baseline.is_active.is_(True))
        .order_by(Baseline.created_at)
        .all()
    )

    pending_events = (
        db.query(ValidationEvent)
        .filter(ValidationEvent.state == AWAITING_APPROVAL)
        .order_by(ValidationEvent.created_at)
        .all()
    )
    groups: dict[str, list[ValidationEvent]] = {}
    order: list[str] = []
    for event in pending_events:
        key = event.correlation_group_id or event.id
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(event)

    pending_queries = (
        db.query(QueryRun)
        .filter(QueryRun.state == AWAITING_APPROVAL)
        .order_by(QueryRun.created_at)
        .all()
    )

    pending_models = (
        db.query(ModelRun)
        .filter(ModelRun.state == AWAITING_APPROVAL)
        .order_by(ModelRun.created_at)
        .all()
    )

    # Connector warnings never reach AWAITING_APPROVAL (they're never
    # diagnosed or gated) - they sit at DETECTED until acknowledged, so
    # this is a separate query, not a filter over pending_events above.
    pending_connector_warnings = (
        db.query(ValidationEvent)
        .filter(ValidationEvent.state == DETECTED, ValidationEvent.rule_failed.like(f"{CONNECTOR_WARNING}:%"))
        .order_by(ValidationEvent.created_at)
        .all()
    )

    # --- Dashboard UX pass, Part 2: recently resolved, so an empty pending
    # list can be told apart from "the system has genuinely never done
    # anything" (see each section's self-explaining empty state on the
    # frontend). Filtered on resolved_by/resolved_at rather than state alone
    # so an auto-fix (never a human decision, never shown as "pending" here
    # either) never shows up as if a human had resolved it. ---
    resolved_baselines = (
        db.query(Baseline).filter(Baseline.resolved_at.isnot(None)).order_by(Baseline.resolved_at.desc()).limit(RECENTLY_RESOLVED_LIMIT).all()
    )
    resolved_events = (
        db.query(ValidationEvent)
        .filter(ValidationEvent.resolved_by.isnot(None), ~ValidationEvent.rule_failed.like(f"{CONNECTOR_WARNING}:%"))
        .order_by(ValidationEvent.resolved_at.desc())
        .limit(RECENTLY_RESOLVED_LIMIT)
        .all()
    )
    resolved_connector_warnings = (
        db.query(ValidationEvent)
        .filter(ValidationEvent.rule_failed.like(f"{CONNECTOR_WARNING}:%"), ValidationEvent.state == RESOLVED)
        .order_by(ValidationEvent.resolved_at.desc())
        .limit(RECENTLY_RESOLVED_LIMIT)
        .all()
    )
    resolved_queries = (
        db.query(QueryRun).filter(QueryRun.resolved_at.isnot(None)).order_by(QueryRun.resolved_at.desc()).limit(RECENTLY_RESOLVED_LIMIT).all()
    )
    resolved_models = (
        db.query(ModelRun).filter(ModelRun.resolved_at.isnot(None)).order_by(ModelRun.resolved_at.desc()).limit(RECENTLY_RESOLVED_LIMIT).all()
    )

    # One shared run_number lookup for every item (pending + resolved) that
    # carries a run_id - a single query, not one per item.
    run_ids = {
        *(e.run_id for e in pending_events),
        *(q.run_id for q in pending_queries),
        *(m.run_id for m in pending_models),
        *(e.run_id for e in pending_connector_warnings),
        *(e.run_id for e in resolved_events),
        *(e.run_id for e in resolved_connector_warnings),
        *(q.run_id for q in resolved_queries),
        *(m.run_id for m in resolved_models),
    }
    run_numbers = {r.id: r.run_number for r in db.query(Run).filter(Run.id.in_(run_ids)).all()} if run_ids else {}

    # "All clear - N items resolved across M runs" / "Nothing has required
    # review yet": counts EVERY resolved item across every type (not just
    # the last 10 shown above) and every distinct run any of them touched.
    # Baselines have no run_id (see the Baseline model) - they count toward
    # total_resolved but can't contribute to distinct_runs.
    total_resolved = (
        db.query(Baseline).filter(Baseline.resolved_at.isnot(None)).count()
        + db.query(ValidationEvent).filter(ValidationEvent.resolved_by.isnot(None)).count()
        + db.query(QueryRun).filter(QueryRun.resolved_at.isnot(None)).count()
        + db.query(ModelRun).filter(ModelRun.resolved_at.isnot(None)).count()
    )
    distinct_run_ids = (
        {row[0] for row in db.query(ValidationEvent.run_id).filter(ValidationEvent.resolved_by.isnot(None)).distinct().all()}
        | {row[0] for row in db.query(QueryRun.run_id).filter(QueryRun.resolved_at.isnot(None)).distinct().all()}
        | {row[0] for row in db.query(ModelRun.run_id).filter(ModelRun.resolved_at.isnot(None)).distinct().all()}
    )

    return {
        "provisional_baselines": [_serialize_baseline(b) for b in provisional_baselines],
        "validation_events": [_serialize_event_group(groups[key], run_numbers) for key in order],
        "escalated_queries": [_serialize_query_run(q, run_numbers) for q in pending_queries],
        "escalated_models": [_serialize_model_run(m, run_numbers) for m in pending_models],
        "connector_warnings": [_serialize_connector_warning(e, run_numbers) for e in pending_connector_warnings],
        "recently_resolved": {
            "provisional_baselines": [_serialize_resolved_baseline(b) for b in resolved_baselines],
            "validation_events": [_serialize_resolved_event(e, run_numbers) for e in resolved_events],
            "connector_warnings": [_serialize_resolved_connector_warning(e, run_numbers) for e in resolved_connector_warnings],
            "queries": [_serialize_resolved_query_run(q, run_numbers) for q in resolved_queries],
            "models": [_serialize_resolved_model_run(m, run_numbers) for m in resolved_models],
        },
        "summary": {
            "total_resolved": total_resolved,
            "distinct_runs": len(distinct_run_ids),
        },
    }


def _group_events(event: ValidationEvent, db: Session) -> list[ValidationEvent]:
    if event.correlation_group_id:
        return (
            db.query(ValidationEvent)
            .filter(ValidationEvent.correlation_group_id == event.correlation_group_id)
            .all()
        )
    return [event]


def _resolve_validation_event(
    event: ValidationEvent,
    payload: ApprovalResolution,
    db: Session,
    diagnostic_agent: DiagnosticAgent | None,
    narrative_agent: NarrativeAgent | None,
    background_tasks: BackgroundTasks,
    summary_agent=None,
) -> dict:
    """Phase 8: applying a decision to an escalated validation-event group
    now means RESUMING THE GRAPH at its await_human checkpoint - the graph
    decides what happens next (re-check resolve, explore/narrate, another
    await_human pause), never this function. Preconditions that would make
    a decision structurally invalid are still checked HERE, before the
    graph is ever touched, so an invalid request never reaches (or
    perturbs) a checkpointed run - if it doesn't hold, this raises
    HTTPException directly and the graph is not resumed at all.

    RESOLVE-HANG FIX (same disease as POST /ingest, same cure): resuming
    the graph can run narrate_node's LLM chain exactly like a fresh ingest
    can (explore/narrate happen on the resumed path too, whenever the
    decision doesn't terminate the run) - graph.invoke() held THIS request
    open for however long that took, for the identical reason POST /ingest
    did. Every one of the four decisions goes through this same
    graph.invoke() call (reject_data included - it just routes to the
    terminal "failed" sink faster once inside await_human_node), so all
    four get the same fix uniformly: schedule the resume as a background
    task and return immediately with status="resolving"; GET
    /ingest/{run_id}/status?resolve_id=<event.id> (find_resolution_response
    in app/routers/ingest.py) is what the frontend/caller polls for the
    SAME response shape this used to return synchronously."""
    if event.state != AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="event is not awaiting approval")
    if payload.decision not in EVENT_DECISIONS:
        raise HTTPException(status_code=422, detail=f"unrecognized decision '{payload.decision}'")

    group_events = _group_events(event, db)

    if payload.decision == "approve":
        group = CorrelatedGroup(
            members=[ValidationFailure(rule_failed=e.rule_failed, column=e.column_name, detail=e.detail_json) for e in group_events],
            correlation_rule=event.correlation_rule,
        )
        suggested = (event.diagnosis_json or {}).get("suggested_fix") or {}
        try:
            action = FixAction(suggested.get("action"))
        except ValueError:
            raise HTTPException(status_code=422, detail="diagnosis has no valid suggested_fix.action to apply") from None
        if action not in permitted_actions(group):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{action.value}' is not a matrix-permitted action for this rule family - "
                    "there is no automated fix this system can apply; consider 'accept_as_baseline' "
                    "if this is a legitimate change, or correct the data out of band and re-ingest"
                ),
            )
        run = db.get(Run, event.run_id)
        source = db.get(DataSource, run.source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        if baseline is None:
            raise HTTPException(status_code=409, detail="no active baseline for this source; cannot verify a repair")
    elif payload.decision == "accept_as_baseline":
        run = db.get(Run, event.run_id)
        source = db.get(DataSource, run.source_id)
        if db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none() is None:
            raise HTTPException(status_code=409, detail="no active baseline for this source")

    resume_payload = {"resolve_id": event.id, "decision": payload.decision, "resolved_by": payload.resolved_by}
    config = {
        "configurable": {
            "thread_id": event.run_id,
            "diagnostic_agent": diagnostic_agent,
            "narrative_agent": narrative_agent,
            # A RESUMED run reaches summarise too. Without this the summary
            # would silently be the template on every escalated run, which
            # is a degradation nothing would report.
            "summary_agent": summary_agent,
        }
    }
    background_tasks.add_task(invoke_in_background, event.run_id, Command(resume=resume_payload), config)

    return {"id": event.id, "type": "validation_event", "decision": payload.decision, "status": "resolving", "run_id": event.run_id}


def _resolve_connector_warning(event: ValidationEvent, payload: ApprovalResolution, db: Session) -> dict:
    if event.state != DETECTED:
        raise HTTPException(status_code=409, detail="connector warning is not pending acknowledgement")
    if payload.decision not in CONNECTOR_WARNING_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.decision}' is not valid for a connector warning; use one of {sorted(CONNECTOR_WARNING_DECISIONS)}",
        )
    transition(event.state, RESOLVED)
    event.state = RESOLVED
    event.action_taken = "acknowledged"
    event.resolved_by = payload.resolved_by
    event.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": event.id, "type": "connector_warning", "decision": "acknowledge"}


def _resolve_query_run(query_run: QueryRun, payload: ApprovalResolution, db: Session) -> dict:
    if query_run.state != AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="query is not awaiting approval")
    if payload.decision not in QUERY_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.decision}' is not valid for an escalated query; use one of {sorted(QUERY_DECISIONS)}",
        )

    if payload.decision == "reject_fix":
        reject_escalated_query(db, query_run, payload.resolved_by)
        return {"id": query_run.id, "type": "query_run", "decision": "reject_fix"}

    # approve: restricted to the one escalation reason that leaves behind
    # code which already passed static validation and simply fell under the
    # confidence threshold - see app/query/models.py::APPROVABLE_ESCALATION_REASONS.
    if query_run.escalation_reason != EscalationReason.LOW_CONFIDENCE.value:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'approve' is not valid for escalation_reason={query_run.escalation_reason!r} - only a "
                f"{EscalationReason.LOW_CONFIDENCE.value!r} escalation has validated-safe code to run; "
                "use 'reject_fix' to acknowledge and dismiss this one"
            ),
        )
    resolved = resolve_escalated_query_approval(db, query_run, payload.resolved_by)
    return {
        "id": resolved.id,
        "type": "query_run",
        "decision": "approve",
        "state": resolved.state,
        "result": resolved.result_json,
    }


def _resolve_model_run(model_run: ModelRun, payload: ApprovalResolution, db: Session) -> dict:
    if model_run.state != AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="model is not awaiting approval")
    if payload.decision not in MODEL_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.decision}' is not valid for an escalated model; use one of {sorted(MODEL_DECISIONS)}",
        )

    if payload.decision == "reject_fix":
        reject_escalated_model(db, model_run, payload.resolved_by)
        return {"id": model_run.id, "type": "model_run", "decision": "reject_fix"}

    # approve: restricted to escalation reasons that leave behind a fully
    # trained, scored model - see app/modeling/models.py::APPROVABLE_ESCALATION_REASONS.
    if model_run.escalation_reason not in {r.value for r in APPROVABLE_MODEL_ESCALATION_REASONS}:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'approve' is not valid for escalation_reason={model_run.escalation_reason!r} - only "
                f"{sorted(r.value for r in APPROVABLE_MODEL_ESCALATION_REASONS)} leave behind a trained model to accept; "
                "use 'reject_fix' to acknowledge and dismiss this one"
            ),
        )
    resolved = resolve_escalated_model_approval(db, model_run, payload.resolved_by)
    return {"id": resolved.id, "type": "model_run", "decision": "approve", "state": resolved.state}


def _resolve_baseline(baseline: Baseline, payload: ApprovalResolution, db: Session) -> dict:
    if payload.decision not in BASELINE_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.decision}' is not valid for a baseline; use one of {sorted(BASELINE_DECISIONS)}",
        )
    if not baseline.is_provisional:
        raise HTTPException(status_code=409, detail="baseline is not awaiting confirmation")

    baseline.resolved_by = payload.resolved_by
    baseline.resolved_at = datetime.now(timezone.utc)
    if payload.decision == "approve":
        baseline.is_provisional = False
    else:
        # reject_data: this baseline never becomes authoritative. Deactivating
        # it (rather than deleting) leaves it in the audit trail while
        # clearing the way for the next successful ingest to attempt a fresh
        # baseline.
        baseline.is_provisional = False
        baseline.is_active = False
    db.commit()
    db.refresh(baseline)
    return {"id": baseline.id, "type": "baseline", "decision": payload.decision}


@router.post("/{item_id}/resolve")
def resolve(
    item_id: str,
    payload: ApprovalResolution,
    background_tasks: BackgroundTasks,
    # scope="function": this session closes when the endpoint returns, BEFORE the
    # background task runs. With the default scope FastAPI keeps it open until the
    # background work finishes - and after a post-commit read it holds a pooled
    # connection for the entire graph run, model calls included.
    db: Session = Depends(get_db, scope="function"),
    diagnostic_agent: DiagnosticAgent | None = Depends(get_diagnostic_agent),
    narrative_agent: NarrativeAgent | None = Depends(get_narrative_agent),
    summary_agent=Depends(get_summary_agent),
):
    baseline = db.get(Baseline, item_id)
    if baseline is not None:
        return _resolve_baseline(baseline, payload, db)

    event = db.get(ValidationEvent, item_id)
    if event is not None:
        if event.rule_failed.startswith(f"{CONNECTOR_WARNING}:"):
            return _resolve_connector_warning(event, payload, db)
        return _resolve_validation_event(
                event, payload, db, diagnostic_agent, narrative_agent, background_tasks, summary_agent
            )

    query_run = db.get(QueryRun, item_id)
    if query_run is not None:
        return _resolve_query_run(query_run, payload, db)

    model_run = db.get(ModelRun, item_id)
    if model_run is not None:
        return _resolve_model_run(model_run, payload, db)

    raise HTTPException(status_code=404, detail="no pending approval with that id")
