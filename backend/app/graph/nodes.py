"""Graph nodes for the ingest pipeline:

    ingest -> validate -> resolve -> await_human -> resolve -> explore -> narrate

with a terminal `failed` sink reachable from every node (RULE B) and from
`reject_data` specifically (the graph spec's own "reject_data terminates
the graph; nothing downstream runs").

RULE D (reconcile on resume): every node re-reads Run/ValidationEvent/
Baseline state FRESH from the database at the start of its own execution -
never trusting anything the checkpoint remembers about what "should" be
true. `await_human_node` in particular re-checks that the event named in
its resume payload is STILL awaiting_approval before touching it; if
domain state changed underneath the checkpoint (resolved through another
path, or the run already failed), that reality wins and the decision
becomes a no-op rather than being blindly re-applied.

RULE B (every graph exit sets a terminal status): `safe_node` in
app/graph/build.py wraps every node registered here - a node raising for
any reason is caught in exactly one place and the run is marked "failed"
with a trace recording why, never left at "running". Nothing in this
module needs its own try/except for that reason; it is structural, applied
once, not a convention each node has to remember.

None of these nodes hold a DataFrame in the state dict they return (RULE
C) - each reconstructs the working frame on demand via
app/repair.py::repaired_contract_for_run/base_contract_for_run, which read
Run.fix_chain and Run.snapshot_path - both authoritative, both already
committed to the database by the time any node runs.
"""

from __future__ import annotations

import json
import os
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.baseline_sanity import BaselineSanityError, assert_baseline_sane
from app.connectors.factory import build_connector
from app.correlation import CorrelatedGroup, correlate_events
from app.db import SessionLocal
from app.analytics.pipeline import confirmed_roles_for_source, run_business_analytics_for_run
from app.exploration.pipeline import run_exploration_for_run
from app.semantic_roles import detect_for_run, roles_document
from app.gate import DEFAULT_CONFIDENCE_THRESHOLD
from app.graph.state import IngestState
from app.models import AgentTrace, Baseline, DataSource, Run, ValidationEvent
from app.narrative.pipeline import run_narrative_for_run
from app.profiling import BaselineProfiler
from app.repair import repaired_contract_for_run
from app.resolution import (
    DecisionError,
    apply_accept_as_baseline,
    apply_approve,
    apply_reject_data,
    apply_reject_fix,
    process_queue,
    reveal_depth_cap_from_env,
)
from app.run_snapshots import save_snapshot
from app.state_machine import AWAITING_APPROVAL, DETECTED
from app.validation.engine import ValidationEngine, ValidationFailure

NODE_NAMES = ["ingest", "validate", "resolve", "await_human", "explore", "narrate"]


def _confidence_threshold() -> float:
    return float(os.environ.get("GATE_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))


def _group_detected_events(events: list[ValidationEvent]) -> list[tuple[CorrelatedGroup, list[ValidationEvent]]]:
    """Groups a flat list of DETECTED ValidationEvent rows by
    correlation_group_id, reconstructing the CorrelatedGroup shape
    process_queue expects - the inverse of how app/routers/ingest.py wrote
    them in the first place."""
    groups: dict[str, list[ValidationEvent]] = {}
    order: list[str] = []
    for event in events:
        key = event.correlation_group_id or event.id
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(event)

    result = []
    for key in order:
        group_events = groups[key]
        correlation_rule = group_events[0].correlation_rule
        group = CorrelatedGroup(
            members=[ValidationFailure(rule_failed=e.rule_failed, column=e.column_name, detail=e.detail_json) for e in group_events],
            correlation_rule=correlation_rule,
        )
        result.append((group, group_events))
    return result


def ingest_node(state: IngestState, config: RunnableConfig) -> dict:
    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        source = db.get(DataSource, run.source_id)

        connector = build_connector(source)
        contract = connector.fetch()
        # Cached HERE as well as after repair, because a run that escalates
        # never reaches explore_node - and an escalated run is exactly the
        # one a human sits polling. Without this, every poll of a paused run
        # rebuilt the frame (37s on a 94MB source). explore_node overwrites
        # it with the post-repair metadata when the run gets that far, so a
        # completed run still describes its repaired frame.
        run.contract_metadata = contract.metadata()

        active_baseline = (
            db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        )
        had_baseline_before = active_baseline is not None

        connector_warnings = ValidationEngine.check_connector_warnings(contract)
        for warning in connector_warnings:
            db.add(
                ValidationEvent(
                    run_id=run.id,
                    rule_failed=warning.rule_failed,
                    column_name=warning.column,
                    detail_json=warning.detail,
                    against_provisional_baseline=False,
                )
            )

        if not had_baseline_before:
            profile = BaselineProfiler().profile(contract.data)
            try:
                assert_baseline_sane(profile)
                active_baseline = Baseline(source_id=source.id, profile_json=profile, is_active=True, is_provisional=True)
                db.add(active_baseline)
                db.flush()
            except BaselineSanityError as sanity_error:
                db.add(
                    AgentTrace(
                        run_id=run.id,
                        agent_name="baseline_profiler",
                        input_summary=f"source_id={source.id} row_count={contract.row_count}",
                        output_summary=json.dumps({"baseline_rejected": sanity_error.reasons}),
                    )
                )

        route = "validate" if had_baseline_before else "explore"
        db.add(
            AgentTrace(
                run_id=run.id,
                agent_name="ingestion",
                input_summary=f"source_id={source.id} type={source.type}",
                output_summary=json.dumps(contract.metadata()),
                confidence_score=1.0,
                edge_taken=route,
            )
        )
        db.commit()

    return {"route": route}


def validate_node(state: IngestState, config: RunnableConfig) -> dict:
    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        source = db.get(DataSource, run.source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        connector = build_connector(source)
        contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)

        # Pre-migration always assigned run.fix_chain = fix_chain (a []-
        # initialized list) unconditionally in this branch, whether or not
        # any failure was ever found - only a run with no baseline yet
        # (routed straight to explore from ingest_node, never entering this
        # node at all) legitimately leaves fix_chain at None. Setting it
        # here, unconditionally, on entry, is what preserves that contract:
        # a fix_chain of [] means "validation ran, nothing to fix", never
        # "validation ran, nothing to fix, so we forgot to record that".
        run.fix_chain = []

        failures = ValidationEngine().validate(contract, baseline.profile_json)
        groups = correlate_events(failures, baseline.profile_json, contract)

        for group in groups:
            group_id = str(uuid.uuid4()) if group.is_correlated else None
            for failure in group.members:
                db.add(
                    ValidationEvent(
                        run_id=run.id,
                        rule_failed=failure.rule_failed,
                        state=DETECTED,
                        column_name=failure.column,
                        detail_json=failure.detail,
                        against_provisional_baseline=baseline.is_provisional,
                        correlation_group_id=group_id,
                        correlation_rule=group.correlation_rule,
                    )
                )

        route = "resolve" if groups else "explore"
        db.add(
            AgentTrace(
                run_id=run.id,
                agent_name="validation",
                input_summary=f"baseline_id={baseline.id} row_count={contract.row_count}",
                output_summary=json.dumps([f.rule_failed for f in failures]),
                edge_taken=route,
            )
        )
        db.add(
            AgentTrace(
                run_id=run.id,
                agent_name="correlation",
                input_summary=f"event_count={len(failures)}",
                output_summary=json.dumps(
                    [{"correlation_rule": g.correlation_rule, "columns": [m.column for m in g.members]} for g in groups if g.is_correlated]
                ),
            )
        )
        db.commit()

    return {"route": route}


def resolve_node(state: IngestState, config: RunnableConfig) -> dict:
    diagnostic_agent = config["configurable"]["diagnostic_agent"]

    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])

        # Rule D: re-read fresh. A reject_data applied at await_human's last
        # resume already set this - resolve_node re-entering afterward must
        # route to the terminal sink, not attempt to continue processing.
        if run.status == "failed":
            db.add(AgentTrace(run_id=run.id, agent_name="resolve", input_summary="run already failed", output_summary="{}", edge_taken="failed"))
            db.commit()
            return {"route": "failed"}

        source = db.get(DataSource, run.source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        connector = build_connector(source)

        detected_events = (
            db.query(ValidationEvent)
            .filter(ValidationEvent.run_id == run.id, ValidationEvent.state == DETECTED)
            .order_by(ValidationEvent.created_at)
            .all()
        )
        if detected_events:
            contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
            groups_and_events = _group_detected_events(detected_events)
            queue_outcome = process_queue(
                db, run, contract, groups_and_events, baseline, diagnostic_agent, _confidence_threshold(), reveal_depth_cap_from_env()
            )
            run.fix_chain = [*(run.fix_chain or []), *queue_outcome.fix_chain]
            # The cache describes the frame, and the frame just changed. A
            # run that escalates here sits at awaiting_approval being polled
            # and would otherwise serve metadata describing the PRE-fix
            # frame until it eventually reached explore_node - or forever,
            # if it never does. Free to refresh: process_queue already
            # carries the post-fix contract, so nothing is rebuilt.
            if queue_outcome.fix_chain:
                run.contract_metadata = queue_outcome.contract.metadata()
            db.flush()

        still_pending = (
            db.query(ValidationEvent).filter(ValidationEvent.run_id == run.id, ValidationEvent.state == AWAITING_APPROVAL).count()
        )
        route = "await_human" if still_pending > 0 else "explore"
        if route == "await_human":
            run.status = "awaiting_approval"
            if not connector.source_immutable and not run.snapshot_path:
                # Matches app/run_snapshots.py's documented contract exactly:
                # a mutable source (SQL, API) only ever gets snapshotted once
                # a run is actually known to be pausing at awaiting_approval -
                # never unconditionally at ingest time. A human resolving this
                # hours later must replay against the RAW frame the
                # Diagnostic Agent actually saw, not a live re-fetch; but an
                # ingest that ends up auto-fixing everything (no pause) has
                # no such risk and must never pay this cost or lose
                # connector_metadata (declared_schema, pagination bookkeeping)
                # to a snapshot round-trip that only persists the DataFrame.
                raw_contract = connector.fetch()
                run.snapshot_path = save_snapshot(run.id, raw_contract.data.copy(deep=True))
        db.add(
            AgentTrace(
                run_id=run.id,
                agent_name="resolve",
                input_summary=f"detected_groups={len(detected_events)}",
                output_summary=json.dumps({"still_pending": still_pending}),
                edge_taken=route,
            )
        )
        db.commit()

    return {"route": route}


def await_human_node(state: IngestState, config: RunnableConfig) -> dict:
    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        pending = (
            db.query(ValidationEvent)
            .filter(ValidationEvent.run_id == run.id, ValidationEvent.state == AWAITING_APPROVAL)
            .order_by(ValidationEvent.created_at)
            .all()
        )
        summary = [{"id": e.id, "rule_failed": e.rule_failed, "correlation_group_id": e.correlation_group_id} for e in pending]

    # Exactly ONE interrupt() call per node execution - never looped. See
    # the Phase 8 report: a loop of multiple interrupt() calls within one
    # node replays earlier resume values against whatever the CURRENT
    # queue head happens to be on re-execution, silently misapplying a
    # human's decision to the wrong event. The graph's own
    # resolve <-> await_human edge is what provides the loop instead - a
    # fresh node execution per decision, never a replay ambiguity.
    decision_payload = interrupt({"run_id": state["run_id"], "pending": summary})

    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        event = db.get(ValidationEvent, decision_payload.get("resolve_id"))

        if event is None or event.state != AWAITING_APPROVAL:
            # Rule D: domain state disagrees with what this checkpoint
            # expected (resolved through another path already, e.g.
            # directly in the database) - reality wins. No-op, not a retry.
            db.add(
                AgentTrace(
                    run_id=run.id,
                    agent_name="await_human",
                    input_summary=f"resolve_id={decision_payload.get('resolve_id')!r}",
                    output_summary=json.dumps({"skipped": "event no longer awaiting_approval - reconciled with current domain state"}),
                    edge_taken="resolve",
                )
            )
            db.commit()
            return {"route": "resolve"}

        group_events = (
            db.query(ValidationEvent).filter(ValidationEvent.correlation_group_id == event.correlation_group_id).all()
            if event.correlation_group_id
            else [event]
        )
        decision = decision_payload["decision"]
        resolved_by = decision_payload["resolved_by"]
        source = db.get(DataSource, run.source_id)

        try:
            if decision == "reject_fix":
                outcome = apply_reject_fix(db, group_events, resolved_by)
            elif decision == "reject_data":
                outcome = apply_reject_data(db, run, group_events, resolved_by)
            elif decision == "accept_as_baseline":
                outcome = apply_accept_as_baseline(db, run, source, group_events, resolved_by)
            elif decision == "approve":
                outcome = apply_approve(
                    db, run, source, event, group_events, resolved_by,
                    config["configurable"]["diagnostic_agent"], _confidence_threshold(), reveal_depth_cap_from_env(),
                )
            else:
                raise DecisionError(f"unrecognized decision {decision!r}")
        except DecisionError as exc:
            db.add(
                AgentTrace(
                    run_id=run.id, agent_name="await_human",
                    input_summary=f"resolve_id={event.id} decision={decision}",
                    output_summary=json.dumps({"error": str(exc)}), edge_taken="resolve",
                )
            )
            db.commit()
            return {"route": "resolve"}

        route = "failed" if decision == "reject_data" else "resolve"
        db.add(
            AgentTrace(
                run_id=run.id, agent_name="await_human",
                input_summary=f"resolve_id={event.id} decision={decision}",
                output_summary=json.dumps(
                    {
                        "resolve_id": event.id,
                        "decision": outcome.decision,
                        "applied": outcome.applied,
                        "error": outcome.error,
                        "new_baseline_id": outcome.new_baseline_id,
                        "resolved_by": resolved_by,
                    }
                ),
                edge_taken=route,
            )
        )
        db.commit()

    return {"route": route}


def explore_node(state: IngestState, config: RunnableConfig) -> dict:
    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        source = db.get(DataSource, run.source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()

        run.status = "completed"
        from datetime import datetime, timezone

        run.completed_at = datetime.now(timezone.utc)
        db.commit()

        connector = build_connector(source)
        contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
        # Cache it here, where the frame is already in hand. Every later
        # reader (the status poll, the run picker's column hints) then gets
        # it for the cost of a row read instead of rebuilding the frame.
        run.contract_metadata = contract.metadata()

        # Semantic roles, detected ONCE here and persisted on the run, so
        # exploration, query, modeling and narrative all read the same
        # answer instead of only the analytics agent knowing which column is
        # an identifier. Confirmed roles for this SOURCE are layered in, so
        # a person's answer carries across every agent, not just analytics.
        roles_detection = detect_for_run(contract.data, confirmed_roles_for_source(db, run.source_id))
        run.semantic_roles = roles_document(roles_detection)

        exploration_record = run_exploration_for_run(
            db,
            run,
            contract.data,
            baseline_is_provisional=(baseline.is_provisional if baseline else False),
            roles=run.semantic_roles,
        )
        # Business analytics runs AFTER exploration, on the same REPAIRED
        # frame, only for a completed run. Wrapped because it is additive
        # reporting: a failure here must never fail an ingest that already
        # produced valid exploration findings and is about to produce a
        # report. The failure is recorded on the analytics side.
        try:
            run_business_analytics_for_run(db, run, contract.data)
        except Exception as exc:  # noqa: BLE001 - additive reporting never fails a run
            print(f"[analytics] business analytics failed for run {run.id}: {type(exc).__name__}: {exc}")
        db.commit()

    return {"route": "narrate" if exploration_record is not None else "done"}


def narrate_node(state: IngestState, config: RunnableConfig) -> dict:
    narrative_agent = config["configurable"].get("narrative_agent")
    with SessionLocal() as db:
        from app.models import ExplorationFinding

        run = db.get(Run, state["run_id"])
        source = db.get(DataSource, run.source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        exploration_record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()

        connector = build_connector(source)
        contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
        if exploration_record is not None:
            run_narrative_for_run(db, run, exploration_record, contract.data, narrative_agent)
        db.commit()

    return {"route": "done"}


def failed_node(state: IngestState, config: RunnableConfig) -> dict:
    """Terminal sink. Idempotent finalize - ensures run.status is "failed"
    even if reached from a path that hasn't already set it (the generic
    degraded-path wrapper, app/graph/build.py::safe_node, sets it before
    routing here; this node's own job is just to make that fact durable and
    traced even if invoked directly)."""
    with SessionLocal() as db:
        run = db.get(Run, state["run_id"])
        if run.status != "failed":
            from datetime import datetime, timezone

            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
        db.commit()
    return {"route": "done"}
