"""Phase 8: a thin graph entry point.

Every sequencing decision that used to live here (connector fetch,
baseline establish/reject, validate, correlate, diagnose/gate/apply,
explore, narrate) now lives in app/graph/ - this router's only job is to
create the Run row, start the graph, and serialize whatever the database
shows once the graph either completes or pauses at await_human. See
tests/test_migration_golden_capture.py / tests/test_graph_matches_golden.py
for the proof that this preserves the pre-migration response contract.

The response's `metadata` field is computed FRESH here, after the graph
returns, via repaired_contract_for_run - always the CURRENT true state
(post any auto-fix), never sourced from a stored trace. This is a
deliberate split from the "ingestion" AgentTrace the ingest node itself
writes, which records what ingestion SAW (the raw fetch) - the audit log
and the HTTP response answer different questions, and conflating them
would mean the response either shows stale (pre-fix) data or the trace
stops meaning "what ingestion observed".

POST-INGEST-HANG FIX: the graph used to run INLINE, synchronously, inside
this endpoint - graph.invoke() doesn't return until narrate_node's LLM
calls (up to 8 across two retry-with-backoff stages: generate_claims,
generate_prose, one repair-once retry each) finish too, even though the
run itself is already marked "completed" (app/graph/nodes.py::explore_node
sets that BEFORE narrate_node ever runs). Measured directly: a single
POST /ingest ranged from ~0.3s to 60s+ (client-timeout-dependent) to what
looked like an indefinite hang, entirely inside this one HTTP request,
while the backend's event loop stayed fully responsive to every OTHER
request the whole time - a browser fetch() has no default timeout, so the
UI just sat on "Ingesting..." with no way to tell a slow LLM call from a
truly dead request. Fix: POST /ingest now creates the Run row and returns
`{run_id, status: "running"}` immediately; the graph runs via
BackgroundTasks (app/graph/build.py::invoke_in_background, shared with
approvals.py's resolve fix below), and GET /ingest/{run_id}/status
(reusing _serialize_run_response unchanged) is what the frontend polls
until the run leaves "running" - never one HTTP request held open for the
graph's full duration.

SAME FIX, SECOND LOCATION: POST /approvals/{id}/resolve had the identical
bug - resolving an escalated validation event resumes this SAME graph
(app/routers/approvals.py::_resolve_validation_event), so it could hold
ITS request open for narrate_node's LLM chain too. GET .../status?
resolve_id=<event_id> (find_resolution_response below) is that fix's poll
target, reusing this endpoint rather than adding a second one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.connectors.factory import build_connector
from app.db import get_db
from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.dependency import get_diagnostic_agent
from app.graph.build import invoke_in_background
from app.id_lookup import resolve_run
from app.models import AgentTrace, Baseline, DataSource, ExplorationFinding, Report, Run, ValidationEvent
from app.narrative.agent import NarrativeAgent
from app.narrative.dependency import get_narrative_agent
from app.summary.dependency import get_summary_agent
from app.repair import repaired_contract_for_run

router = APIRouter(prefix="/ingest", tags=["ingest"])

__all__ = ["router", "get_diagnostic_agent", "get_narrative_agent", "get_summary_agent"]


def next_run_number(db: Session) -> int:
    """Assigned in Python, inside the same transaction as the Run insert -
    not a DB-native AUTOINCREMENT/SEQUENCE, because `id` (a UUID) is already
    this table's real primary key and SQLite only aliases rowid-autoincrement
    onto an INTEGER PRIMARY KEY column; a second, independently-incrementing
    column has no portable native mechanism to lean on across both SQLite
    and Postgres from one shared model definition. This app is single-writer
    (a local dev/demo Uvicorn process) so a MAX+1 read is not actually racy
    in practice - the unique index on run_number (app/db.py::init_db) is the
    real correctness guarantee; a genuine race would surface as an
    IntegrityError on commit, never a silently duplicated number."""
    current_max = db.query(func.max(Run.run_number)).scalar()
    return (current_max or 0) + 1


def _serialize_run_response(db: Session, run_id: str) -> dict:
    run = db.get(Run, run_id)
    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    exploration = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one_or_none()
    report = db.query(Report).filter(Report.run_id == run_id).one_or_none()
    validation_failure_count = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).count()

    # Cached at completion (app/graph/nodes.py::explore_node). Rebuilding
    # the repaired frame here just to describe it was O(source size) - 30.9s
    # on a 94MB CSV, on an endpoint the UI POLLS and which the run picker
    # calls for its column hints. A run from before the column existed has
    # no cache, so it falls back to the old path rather than losing the
    # fields entirely.
    if run.contract_metadata:
        metadata = run.contract_metadata
    else:
        connector = build_connector(source)
        contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
        metadata = contract.metadata()

    baseline_rejected_trace = (
        db.query(AgentTrace).filter(AgentTrace.run_id == run_id, AgentTrace.agent_name == "baseline_profiler").one_or_none()
    )
    baseline_rejected_reasons = json.loads(baseline_rejected_trace.output_summary).get("baseline_rejected") if baseline_rejected_trace else None

    return {
        "run_id": run.id,
        "run_number": run.run_number,
        "status": run.status,
        "metadata": metadata,
        # Which columns hold personal data, which were masked before any
        # third-party call, and which await a human decision. Reported on the
        # run itself so the Privacy section needs no extra request.
        "privacy": run.privacy_classification,
        "validation_failure_count": validation_failure_count,
        "baseline": (
            {"id": baseline.id, "is_active": baseline.is_active, "is_provisional": baseline.is_provisional}
            if baseline is not None
            else None
        ),
        "baseline_rejected_reasons": baseline_rejected_reasons,
        "findings": (
            {"available": True, "run_id": run.id, "url": f"/findings/{run.id}"}
            if exploration is not None
            else {"available": False, "run_id": run.id, "url": None}
        ),
        "report": (
            {"available": True, "run_id": run.id, "url": f"/reports/{run.id}", "generation_mode": report.generation_mode}
            if report is not None
            else {"available": False, "run_id": run.id, "url": None, "generation_mode": None}
        ),
    }


def _run_graph_in_background(
    run_id: str,
    source_id: str,
    diagnostic_agent: DiagnosticAgent | None,
    narrative_agent: NarrativeAgent | None,
    summary_agent=None,
) -> None:
    """diagnostic_agent/narrative_agent are resolved via FastAPI's
    Depends()/dependency_overrides at request time (BEFORE this function
    ever runs) and passed in already-constructed - neither depends on the
    request's own db session or anything else request-scoped, so nothing
    about deferring their USE to a background task changes what a test's
    app.dependency_overrides[get_diagnostic_agent] override controls."""
    config = {
        "configurable": {
            "thread_id": run_id,
            "diagnostic_agent": diagnostic_agent,
            "narrative_agent": narrative_agent,
            "summary_agent": summary_agent,
        }
    }
    invoke_in_background(run_id, {"run_id": run_id, "source_id": source_id}, config)


@router.post("/{source_id}")
def ingest(
    source_id: str,
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
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    run = Run(source_id=source.id, status="running", started_at=datetime.now(timezone.utc))
    run.run_number = next_run_number(db)
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id

    background_tasks.add_task(_run_graph_in_background, run_id, source_id, diagnostic_agent, narrative_agent, summary_agent)

    return {"run_id": run_id, "run_number": run.run_number, "status": run.status}


def find_resolution_response(db: Session, run_id: str, event_id: str) -> dict | None:
    """Poll target for POST /approvals/{id}/resolve's own background
    resume (app/routers/approvals.py::_resolve_validation_event) - that
    endpoint has the identical bug POST /ingest did (graph.invoke()
    inline, holding the request open for narrate_node's LLM chain), fixed
    the same way. Searches this run's await_human traces for the ONE that
    processed event_id SPECIFICALLY - not just "most recent" the way the
    old synchronous code could get away with, since resolving now happens
    via a background task and multiple validation events on the same run
    can each have their own resolve in flight concurrently. Returns None
    while the background resume hasn't reached await_human_node for this
    decision yet (still resolving); once found, reconstructs EXACTLY the
    response POST /approvals/{id}/resolve used to return synchronously
    (see app/graph/nodes.py::await_human_node for the trace shapes this
    reads: a normal resolution has resolve_id/decision/applied/error/
    new_baseline_id in output_summary; a Rule-D no-op has {"skipped":...};
    a DecisionError has {"error":...} with no resolve_id at all - both of
    the latter two mean nothing was applied for THIS event, same as the
    old code's "already resolved" branch)."""
    traces = (
        db.query(AgentTrace)
        .filter(AgentTrace.run_id == run_id, AgentTrace.agent_name == "await_human")
        .order_by(AgentTrace.timestamp.desc())
        .all()
    )
    marker_unquoted = f"resolve_id={event_id}"
    marker_repr = f"resolve_id={event_id!r}"
    for trace in traces:
        input_summary = trace.input_summary or ""
        if marker_unquoted not in input_summary and marker_repr not in input_summary:
            continue
        details = json.loads(trace.output_summary)
        if details.get("resolve_id") != event_id:
            return {
                "id": event_id,
                "type": "validation_event",
                "applied": False,
                "note": details.get("skipped") or details.get("error") or "not applied",
            }
        decision = details.get("decision")
        response = {"id": event_id, "type": "validation_event", "decision": decision}
        if decision == "reject_data":
            response["run_status"] = db.get(Run, run_id).status
        elif decision == "accept_as_baseline":
            response["new_baseline_id"] = details.get("new_baseline_id")
        elif decision == "approve":
            response["applied"] = details.get("applied")
            if not details.get("applied"):
                response["error"] = details.get("error")
        return response
    return None


@router.get("/{run_id}/status")
def ingest_status(run_id: str, resolve_id: str | None = None, db: Session = Depends(get_db)):
    """Poll target for the run POST /ingest just kicked off in the
    background. While "running", returns just {run_id, status} - the rest
    of _serialize_run_response's fields (metadata, baseline, ...) require
    re-fetching the source's connector data, which isn't safe to attempt
    yet (still mid-flight) and is actively wrong to attempt for a run that
    failed at the connector-fetch step itself (the same failure would just
    happen again). Once the run has left "running", returns EXACTLY what
    POST /ingest used to return synchronously - this is a poll target, not
    a new contract, so every existing caller of the old shape keeps working
    unchanged once it's actually terminal.

    ?resolve_id=<validation_event id> additionally includes a "resolution"
    field - the same reuse-over-a-second-endpoint choice this fix made for
    POST /ingest applies here too, since a resolve is just another kind of
    graph invocation against this same run_id."""
    run = resolve_run(db, run_id)
    run_id = run.id
    if run.status in ("running", "failed"):
        body = {"run_id": run.id, "run_number": run.run_number, "status": run.status}
    else:
        body = _serialize_run_response(db, run_id)
    if resolve_id is not None:
        body["resolution"] = find_resolution_response(db, run_id, resolve_id)
    return body
