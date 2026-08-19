"""Phase 7.5 Part 3 introduced a run-scoped audit trail; Phase 8 Part 2
supersedes it with GET /audit/{run_id} below as the CANONICAL surface -
the one place able to answer "why did this run produce this output"
without reading the code, per the Phase 8 spec.

GET /runs/{run_id}/audit-trail is kept below, UNCHANGED, as a deprecated
alias: it costs nothing to keep and the Phase 7.5 tests that already cover
it keep passing. It only ever showed ValidationEvent rows, never the
AgentTrace timeline, the edge the graph took at each branch, or query/model
approvals - callers that need any of that must use GET /audit/{run_id}.
The frontend (frontend/) uses ONLY the canonical endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import AgentTrace, Baseline, DataSource, EgressEvent, ExplorationFinding, ModelRun, QueryRun, Report, Run, ValidationEvent

router = APIRouter(tags=["audit"])


def _serialize_event(event: ValidationEvent) -> dict:
    return {
        "id": event.id,
        "rule_failed": event.rule_failed,
        "column": event.column_name,
        "detail": event.detail_json,
        "state": event.state,
        "action_taken": event.action_taken,
        "resolved_by": event.resolved_by,
        "created_at": event.created_at.isoformat(),
    }


@router.get("/runs/{run_id}/audit-trail")
def get_audit_trail(run_id: str, db: Session = Depends(get_db)):
    """DEPRECATED - see GET /audit/{run_id}. Kept as an alias only."""
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).order_by(ValidationEvent.created_at).all()
    return {
        "run_id": run_id,
        "run_status": run.status,
        "events": [_serialize_event(e) for e in events],
    }


def _parse_summary(raw: str | None):
    """AgentTrace.input_summary/output_summary are stored as either a plain
    string or a json.dumps(...) blob, depending on which node wrote them
    (app/graph/nodes.py, app/resolution.py). Decoding here means a caller of
    this endpoint never needs to know which - it always gets back whatever
    structure was actually recorded, string or object, never a string it has
    to re-parse itself."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _serialize_trace(trace: AgentTrace) -> dict:
    return {
        "id": trace.id,
        "node": trace.agent_name,
        "input": _parse_summary(trace.input_summary),
        "output": _parse_summary(trace.output_summary),
        "confidence": trace.confidence_score,
        "edge_taken": trace.edge_taken,
        "timestamp": trace.timestamp.isoformat(),
    }


def _serialize_validation_event(event: ValidationEvent) -> dict:
    return {
        "id": event.id,
        "rule_failed": event.rule_failed,
        "column": event.column_name,
        "detail": event.detail_json,
        "state": event.state,
        "against_provisional_baseline": event.against_provisional_baseline,
        "correlation_group_id": event.correlation_group_id,
        "correlation_rule": event.correlation_rule,
        "diagnosis": event.diagnosis_json,
        "risk_level": event.risk_level,
        "action_taken": event.action_taken,
        "reversal": event.reversal_json,
        "gate_reasons": event.gate_reasons_json,
        "resolved_by": event.resolved_by,
        "created_at": event.created_at.isoformat(),
    }


def _serialize_query_run(q: QueryRun) -> dict:
    return {
        "id": q.id,
        "question": q.question,
        "query_kind": q.query_kind,
        "generated_code": q.generated_code,
        "columns_referenced": q.columns_referenced_json,
        "assumptions": q.assumptions_json,
        "confidence": q.confidence,
        "state": q.state,
        "escalation_reason": q.escalation_reason,
        "escalation_detail": q.escalation_detail,
        "result": q.result_json,
        "truncated": q.truncated,
        "row_count": q.row_count,
        "quality_context_summary": q.quality_context_summary,
        "resolved_by": q.resolved_by,
        "resolved_at": q.resolved_at.isoformat() if q.resolved_at else None,
        "created_at": q.created_at.isoformat(),
    }


def _serialize_model_run(m: ModelRun) -> dict:
    return {
        "id": m.id,
        "question": m.question,
        "target_column": m.target_column,
        "task_type": m.task_type,
        "model_family": m.model_family,
        "candidate_scores": m.candidate_scores_json,
        "baseline_scores": m.baseline_scores_json,
        "excluded_features": m.excluded_features_json,
        "out_of_sample_metric": m.out_of_sample_metric,
        "out_of_sample_score": m.out_of_sample_score,
        "state": m.state,
        "escalation_reason": m.escalation_reason,
        "escalation_detail": m.escalation_detail,
        "quality_context_summary": m.quality_context_summary,
        "resolved_by": m.resolved_by,
        "resolved_at": m.resolved_at.isoformat() if m.resolved_at else None,
        "created_at": m.created_at.isoformat(),
    }


def _serialize_egress(event: EgressEvent) -> dict:
    """Shape only. There is no payload field to serialise, by design - see
    app/models.py::EgressEvent."""
    return {
        "id": event.id,
        "agent": event.agent,
        "provider": event.provider,
        "model": event.model,
        "policy": event.policy,
        "columns": event.columns_json,
        "row_count": event.row_count,
        "unit": event.unit,
        "redacted_columns": event.redacted_columns_json,
        "masked_value_counts": event.masked_value_counts_json,
        "created_at": event.created_at.isoformat(),
    }


@router.get("/audit/{run_id}")
def get_audit(run_id: str, db: Session = Depends(get_db)):
    """The canonical Phase 8 audit surface. Everything that happened for
    this run - which nodes ran, which edge the graph took out of each one,
    every diagnosis/gate decision, every validation event's full lifecycle
    (including a reverted auto-fix, visible via action_taken="auto_fix_reverted"
    and the gate trace that follows it), who resolved each escalation and
    how, the baseline in force, and every query/model run answered against
    this run's data (Part 0's condition on keeping those approvals outside
    the graph: this endpoint must still cover them), and every outbound call
    this run made to a third-party model - assembled from already-committed
    rows, never re-derived or guessed.

    `egress` answers a different question from the rest: not "why did this run
    produce this output" but "what left this machine while producing it". It
    carries shape only and never the payload, for the reason given on
    app/models.py::EgressEvent. `trace` is ordered
    by timestamp and is the answer to "why did this run produce this
    output"; `validation_events` and the query/model lists fill in the
    per-decision detail a trace's summary alone doesn't carry (gate_reasons,
    generated_code, etc).
    """
    run = resolve_run(db, run_id)
    run_id = run.id

    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    traces = db.query(AgentTrace).filter(AgentTrace.run_id == run_id).order_by(AgentTrace.timestamp).all()
    events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).order_by(ValidationEvent.created_at).all()
    query_runs = db.query(QueryRun).filter(QueryRun.run_id == run_id).order_by(QueryRun.created_at).all()
    model_runs = db.query(ModelRun).filter(ModelRun.run_id == run_id).order_by(ModelRun.created_at).all()
    exploration = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one_or_none()
    report = db.query(Report).filter(Report.run_id == run_id).one_or_none()
    egress = db.query(EgressEvent).filter(EgressEvent.run_id == run_id).order_by(EgressEvent.created_at).all()

    return {
        "run_id": run.id,
        "run_number": run.run_number,
        "source_id": run.source_id,
        "run_status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "fix_chain": run.fix_chain,
        "reveal_depth_reached": run.reveal_depth_reached,
        "baseline": (
            {"id": baseline.id, "is_active": baseline.is_active, "is_provisional": baseline.is_provisional}
            if baseline is not None
            else None
        ),
        "trace": [_serialize_trace(t) for t in traces],
        # What this run sent to a third-party model. Empty is a real answer:
        # a run whose diagnoses all came from cache disclosed nothing.
        "egress": [_serialize_egress(e) for e in egress],
        "validation_events": [_serialize_validation_event(e) for e in events],
        "exploration": (
            {"id": exploration.id, "schema_version": exploration.schema_version, "generated_at": exploration.generated_at.isoformat()}
            if exploration is not None
            else None
        ),
        "report": (
            {
                "id": report.id,
                "generation_mode": report.generation_mode,
                "post_check_results": report.post_check_results,
                "delivered_at": report.delivered_at.isoformat() if report.delivered_at else None,
            }
            if report is not None
            else None
        ),
        "query_runs": [_serialize_query_run(q) for q in query_runs],
        "model_runs": [_serialize_model_run(m) for m in model_runs],
    }
