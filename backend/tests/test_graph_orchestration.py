"""Phase 8 Part 1/2: tests specific to the graph itself, not covered by
preserving pre-migration behaviour (that's tests/test_migration_golden_capture.py's
job). Here: each conditional edge asserted BY NAME (which edge_taken value
a node's own trace recorded, not just the eventual outcome), reject_data
terminating the graph with nothing downstream, resumability across a real
process boundary, Rule B (no node failure ever leaves a run non-terminal,
parametrized over the node list so a future node is covered automatically),
Rule D (domain state - never the checkpoint's memory of it - wins on
resume), and GET /audit/{run_id} being sufficient on its own to answer
"why did this run produce this output".
"""

from __future__ import annotations

import pytest
from langgraph.types import Command

from app.db import SessionLocal
from app.diagnosis.agent import DiagnosticAgent
from app.graph import build as graph_build
from app.graph.build import get_graph, reset_graph
from app.graph.nodes import NODE_NAMES
from app.models import AgentTrace, QueryRun, Run, ValidationEvent
from app.state_machine import REJECTED
from tests.corruption import CorruptionSuite
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache, diagnosis_override
from tests.golden_scenarios import (
    DIAGNOSIS_BY_CORRUPTION,
    _clean_df,
    _create_source,
    _resolve_id_for_run,
    _write_csv,
    ingest_and_wait,
    resolve_and_wait,
)


def _traces_for(run_id: str) -> list[AgentTrace]:
    with SessionLocal() as db:
        return db.query(AgentTrace).filter(AgentTrace.run_id == run_id).order_by(AgentTrace.timestamp).all()


def _run_status(run_id: str) -> str:
    with SessionLocal() as db:
        return db.get(Run, run_id).status


def _establish_baseline_then_escalate(client, tmp_path, corruption_name="inject_nulls", seed=1) -> dict:
    """The default fake diagnostic agent (conftest.py's `client` fixture)
    always returns an ESCALATE/HIGH/confidence-0.0 diagnosis, so any
    corruption reliably escalates without needing a diagnosis_override -
    exactly what most of these tests need to reach resolve/await_human."""
    base_df = _clean_df()
    corrupted_df, _ = CorruptionSuite().apply(base_df, corruption_name, seed=seed)
    path = tmp_path / f"{corruption_name}.csv"
    _write_csv(path, base_df)
    source_id = _create_source(client, path)
    ingest_and_wait(client, source_id)
    _write_csv(path, corrupted_df)
    return ingest_and_wait(client, source_id)


# ---------------------------------------------------------------------------
# Conditional edges, asserted by the edge_taken value each node's own trace
# recorded - not just the eventual run outcome.
# ---------------------------------------------------------------------------


def test_validate_routes_straight_to_explore_when_no_failures(client, tmp_path):
    path = tmp_path / "clean.csv"
    _write_csv(path, _clean_df())
    source_id = _create_source(client, path)
    ingest_and_wait(client, source_id)
    second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"
    validate_trace = next(t for t in _traces_for(second["run_id"]) if t.agent_name == "validation")
    assert validate_trace.edge_taken == "explore"


def test_validate_routes_to_resolve_when_failures_exist(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path)
    assert escalated["status"] == "awaiting_approval"
    validate_trace = next(t for t in _traces_for(escalated["run_id"]) if t.agent_name == "validation")
    assert validate_trace.edge_taken == "resolve"


def test_resolve_routes_to_await_human_when_pending(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path)
    assert escalated["status"] == "awaiting_approval"
    resolve_trace = next(t for t in _traces_for(escalated["run_id"]) if t.agent_name == "resolve")
    assert resolve_trace.edge_taken == "await_human"


def test_resolve_routes_to_explore_once_nothing_left_pending(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path)
    resolve_id = _resolve_id_for_run(client, escalated["run_id"])
    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "tess"})
    assert resp.status_code == 200
    assert _run_status(escalated["run_id"]) == "completed"

    resolve_traces = [t for t in _traces_for(escalated["run_id"]) if t.agent_name == "resolve"]
    assert resolve_traces[-1].edge_taken == "explore"


def test_await_human_resumes_to_resolve_on_resolution(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path)
    resolve_id = _resolve_id_for_run(client, escalated["run_id"])
    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "tess"})
    assert resp.status_code == 200

    await_human_trace = next(t for t in _traces_for(escalated["run_id"]) if t.agent_name == "await_human")
    assert await_human_trace.edge_taken == "resolve"


def test_reject_data_terminates_nothing_downstream_runs(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path, corruption_name="drop_column", seed=2)
    assert escalated["status"] == "awaiting_approval"
    run_id = escalated["run_id"]

    resolve_id = _resolve_id_for_run(client, run_id)
    result = resolve_and_wait(client, resolve_id, "reject_data", "uma")
    assert result["run_status"] == "failed"

    assert _run_status(run_id) == "failed"
    node_names = [t.agent_name for t in _traces_for(run_id)]
    assert "exploration" not in node_names
    assert "narrative" not in node_names

    with SessionLocal() as db:
        from app.models import ExplorationFinding, Report

        assert db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one_or_none() is None
        assert db.query(Report).filter(Report.run_id == run_id).one_or_none() is None


# ---------------------------------------------------------------------------
# Resumability across a genuine process boundary.
# ---------------------------------------------------------------------------


def test_resume_after_simulated_process_restart(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path, corruption_name="inject_nulls", seed=5)
    run_id = escalated["run_id"]

    # Simulate the process ending and a new one starting: drop the cached
    # compiled graph and close the checkpointer's underlying sqlite
    # connection (exactly what app/graph/build.py::reset_graph documents
    # doing). The next get_graph() call - made from inside the approvals
    # router below, not by this test - must reopen the SAME on-disk
    # checkpoint file from nothing but what was persisted to it; nothing
    # about this run may depend on anything still held in memory.
    reset_graph()

    resolve_id = _resolve_id_for_run(client, run_id)
    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "vik"})
    assert resp.status_code == 200
    assert _run_status(run_id) == "completed"


# ---------------------------------------------------------------------------
# Rule B: every node's failure ends the run at a terminal status, never
# "running". Parametrized over NODE_NAMES so a node added later is covered
# automatically.
# ---------------------------------------------------------------------------


def _raise_node(state, config):
    raise RuntimeError("deliberate node failure injected by test_node_failure_never_leaves_run_non_terminal")


def _trigger_node(client, tmp_path, node_name: str) -> str:
    """Engineers an ingest sequence that reaches `node_name` with every
    EARLIER node on the path left un-patched, so only the targeted node's
    own failure is under test."""
    if node_name in ("ingest", "explore", "narrate", "summarise"):
        path = tmp_path / f"{node_name}.csv"
        _write_csv(path, _clean_df())
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)
    elif node_name == "validate":
        path = tmp_path / "validate.csv"
        _write_csv(path, _clean_df())
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)  # baseline, unpatched (validate_node never runs here)
        ingest_and_wait(client, source_id)  # second ingest reaches (patched) validate_node
    else:  # resolve, await_human
        base_df = _clean_df()
        corrupted_df, _ = CorruptionSuite().apply(base_df, "inject_nulls", seed=13)
        path = tmp_path / f"{node_name}.csv"
        _write_csv(path, base_df)
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)
        _write_csv(path, corrupted_df)
        ingest_and_wait(client, source_id)

    with SessionLocal() as db:
        run = db.query(Run).filter(Run.source_id == source_id).order_by(Run.started_at.desc()).first()
        return run.id


@pytest.mark.parametrize("node_name", NODE_NAMES)
def test_node_failure_never_leaves_run_non_terminal(client, tmp_path, node_name, monkeypatch):
    monkeypatch.setitem(graph_build.NODE_FUNCS, node_name, _raise_node)
    reset_graph()

    run_id = _trigger_node(client, tmp_path, node_name)
    status = _run_status(run_id)

    if node_name in ("narrate", "summarise"):
        # explore_node (left un-patched) already committed run.status =
        # "completed" - independently, before narrate_node ever raised.
        # Rule B's guarantee ("never left running") still holds; a later
        # stage's failure legitimately does not retroactively invalidate an
        # earlier stage's already-committed, independently-terminal
        # success (narration has always been a degrade-not-fail concern -
        # see app/narrative/pipeline.py). See the Phase 8 report: this is a
        # deliberate reading of Rule B, called out for review, not a
        # silent loosening of the assertion.
        #
        # `summarise` (Part 2) is the same reading for the same reason: the
        # Session Summary is a degrade-not-fail concern, and a run whose
        # analysis succeeded must not be reported as failed because its
        # plain-language summary could not be written.
        assert status == "completed"
    else:
        assert status == "failed"
    assert status != "running"


# ---------------------------------------------------------------------------
# Rule D: domain state wins over what the checkpoint remembers.
# ---------------------------------------------------------------------------


def test_reconcile_on_resume_when_domain_state_changed_underneath_checkpoint(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path, corruption_name="inject_nulls", seed=21)
    run_id = escalated["run_id"]
    resolve_id = _resolve_id_for_run(client, run_id)

    # Resolve the event DIRECTLY in the database - bypassing the graph (and
    # the approvals router's own state check, which would otherwise refuse
    # a stale decision before ever reaching the graph). This simulates
    # domain state changing underneath a checkpoint by some other path -
    # exactly the situation Rule D exists for.
    with SessionLocal() as db:
        event = db.get(ValidationEvent, resolve_id)
        group_events = (
            db.query(ValidationEvent).filter(ValidationEvent.correlation_group_id == event.correlation_group_id).all()
            if event.correlation_group_id
            else [event]
        )
        for e in group_events:
            e.state = REJECTED
            e.action_taken = "rejected_fix_data_acceptable"
            e.resolved_by = "direct-db-edit"
        db.commit()

    # Resume the graph directly (what app/routers/approvals.py does
    # internally) with a decision that is now STALE relative to domain
    # state - the event this decision targets is no longer awaiting_approval.
    graph = get_graph()
    diagnostic_agent = DiagnosticAgent(llm_client=FakeLLMClient(), cache=InMemoryDiagnosisCache(), sleep=lambda _s: None)
    config = {"configurable": {"thread_id": run_id, "diagnostic_agent": diagnostic_agent, "narrative_agent": None}}
    graph.invoke(Command(resume={"resolve_id": resolve_id, "decision": "approve", "resolved_by": "stale-resumer"}), config=config)

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        assert run.status == "completed"
        event = db.get(ValidationEvent, resolve_id)
        # The direct DB edit's outcome stands - the stale "approve" was a
        # no-op, never re-applied on top of it.
        assert event.state == REJECTED
        assert event.resolved_by == "direct-db-edit"

    reconcile_trace = next(t for t in _traces_for(run_id) if t.agent_name == "await_human" and t.edge_taken == "resolve")
    assert "reconciled" in (reconcile_trace.output_summary or "")


# ---------------------------------------------------------------------------
# GET /audit/{run_id} must be sufficient, alone, to answer "why did this
# run produce this output".
# ---------------------------------------------------------------------------


def test_audit_endpoint_explains_an_auto_fixed_run(client, tmp_path):
    base_df = _clean_df()
    corrupted_df, _ = CorruptionSuite().apply(base_df, "rename_column", seed=31, column="city", new_name="town")
    path = tmp_path / "audit_autofix.csv"
    _write_csv(path, base_df)
    source_id = _create_source(client, path)
    ingest_and_wait(client, source_id)
    _write_csv(path, corrupted_df)
    with diagnosis_override(DIAGNOSIS_BY_CORRUPTION["rename_column"]):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"

    audit = client.get(f"/audit/{second['run_id']}").json()
    assert audit["run_status"] == "completed"

    edges = {t["node"]: t["edge_taken"] for t in audit["trace"] if t["edge_taken"]}
    assert edges["ingestion"] == "validate"
    assert edges["validation"] == "resolve"
    assert edges["resolve"] == "explore"

    diagnosis_trace = next(t for t in audit["trace"] if t["node"] == "diagnosis")
    assert diagnosis_trace["output"]["diagnosis"]["cause_category"] == "rename"
    gate_trace = next(t for t in audit["trace"] if t["node"] == "gate")
    assert gate_trace["output"]["decision"] == "auto_apply"
    assert any(e["action_taken"] == "auto_fixed" for e in audit["validation_events"])


def test_audit_endpoint_explains_a_human_resolved_escalation_and_covers_query_runs(client, tmp_path):
    escalated = _establish_baseline_then_escalate(client, tmp_path, corruption_name="drop_column", seed=41)
    run_id = escalated["run_id"]
    resolve_id = _resolve_id_for_run(client, run_id)
    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "wren"})
    assert resp.status_code == 200

    # A query answered against this run's data - Part 0's condition on
    # keeping query/model approvals outside the graph: GET /audit/{run_id}
    # must still cover them, since they're exactly the paths most likely to
    # be examined by someone asking "why".
    with SessionLocal() as db:
        db.add(
            QueryRun(
                source_id=db.get(Run, run_id).source_id,
                run_id=run_id,
                question="what is the total amount?",
                query_kind="pandas",
                generated_code="df['amount'].sum()",
                state="answered",
            )
        )
        db.commit()

    audit = client.get(f"/audit/{run_id}").json()
    assert audit["run_status"] == "completed"

    escalated_event = next(e for e in audit["validation_events"] if e["action_taken"] == "rejected_fix_data_acceptable")
    assert escalated_event["resolved_by"] == "wren"
    assert escalated_event["gate_reasons"]

    await_human_trace = next(t for t in audit["trace"] if t["node"] == "await_human")
    assert await_human_trace["output"]["decision"] == "reject_fix"
    assert await_human_trace["output"]["resolved_by"] == "wren"

    assert len(audit["query_runs"]) == 1
    assert audit["query_runs"][0]["generated_code"] == "df['amount'].sum()"


def test_audit_unknown_run_is_404(client):
    assert client.get("/audit/does-not-exist").status_code == 404
