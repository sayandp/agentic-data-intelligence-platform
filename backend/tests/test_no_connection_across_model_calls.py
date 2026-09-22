"""No pooled database connection is held across a model call.

narrate_node used to query, then make its model calls - with rate-limit
backoff - while its session still held a pooled connection. Overlapping runs
drained SQLAlchemy's default pool (5 + 10): a plain status request waited 30s
and returned 500, and an ingest FAILED with `QueuePool limit ... reached`.
Found by the approvals-order E2E spec the first time it seeded its own
fixture on an empty database, instead of skipping.

The fix is structural - read, release, call, then write in a fresh session
that re-checks what it read - and these tests prove it three ways:

  1. LOAD: more simultaneous ingests than the pool holds, against a slow
     model, with the pool's size unchanged. None may fail.
  2. PRECISION: at the moment of EVERY model call, at every one of the five
     call sites, the calling thread holds zero pooled connections. Each site
     must actually be exercised - a check that sees zero calls passes
     vacuously, which is the failure the skipping E2E test was.
  3. THE GAP: a run that changes while the model is answering is not
     overwritten, and the calls that went out are still recorded.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

import app.db as app_db
from app.correlation import CorrelatedGroup
from app.db import SessionLocal, connections_held_by_current_thread, track_connections
from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.llm.base import LLMClient, LLMUnavailableError
from app.models import AgentTrace, Baseline, DataSource, EgressEvent, Report, Run, SessionSummary, ValidationEvent
from app.narrative.agent import NarrativeAgent
from app.profiling import BaselineProfiler
from app.state_machine import AWAITING_APPROVAL, DETECTED
from app.summary.agent import SessionSummaryAgent
from app.validation.engine import ValidationFailure
from tests.fakes import InMemoryDiagnosisCache, InMemoryModelingCache, InMemoryQueryCache
from tests.golden_scenarios import _clean_df, _create_source, _write_csv, ingest_and_wait

SAFE_TYPE_CAST = Diagnosis(
    cause_category=CauseCategory.DTYPE_CHANGE,
    likely_cause="numeric column re-typed as text upstream",
    suggested_fix=SuggestedFix(action=FixAction.SAFE_TYPE_CAST),
    risk_level=RiskLevel.LOW,
    confidence=0.95,
)


class RecordingLLM(LLMClient):
    """A model that notes how many pooled connections the CALLING thread
    holds at the instant it is called, optionally takes its time, and then
    either answers or is unavailable.

    `during_call`, if given, runs inside the call - standing in for whatever
    a person or another process does to the run while the model is thinking.
    """

    model_name = "recording-fake"

    def __init__(self, delay: float = 0.0, answer=None, during_call=None):
        self.delay = delay
        self.answer = answer
        self.during_call = during_call
        self.calls: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def complete(self, system, user, response_schema):
        held = connections_held_by_current_thread()
        with self._lock:
            self.calls.append((response_schema.__name__, held))
        if self.during_call is not None:
            hook, self.during_call = self.during_call, None
            hook()
        if self.delay:
            time.sleep(self.delay)
        if self.answer is not None and isinstance(self.answer, response_schema):
            return self.answer
        raise LLMUnavailableError("recording fake: unavailable by design")

    def held_during_calls(self) -> list[int]:
        return [held for _schema, held in self.calls]


def _no_sleep(_seconds):
    return None


# ---------------------------------------------------------------------------
# 1. LOAD
# ---------------------------------------------------------------------------

CONCURRENT_RUNS = 20  # more than the pool's 5 + 10
MODEL_DELAY_SECONDS = 1.0
POOL_TIMEOUT_SECONDS = 2  # the default is 30; shortened so exhaustion shows in seconds


@pytest.fixture
def production_sized_pool_with_short_timeout():
    """An engine on the same database with the SAME pool size as production,
    and only the wait-for-a-connection timeout shortened - so a held-open
    session fails this test in seconds rather than 30s. Every SessionLocal()
    in the app uses it for the duration of the test."""
    engine = create_engine(
        app_db.engine.url,
        connect_args={"check_same_thread": False} if app_db.engine.url.drivername.startswith("sqlite") else {},
        pool_timeout=POOL_TIMEOUT_SECONDS,
    )
    # Guard the premise: the fix must not work by having a bigger pool.
    assert engine.pool.size() == app_db.engine.pool.size() == 5
    assert engine.pool._max_overflow == app_db.engine.pool._max_overflow == 10
    track_connections(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield engine
    finally:
        SessionLocal.configure(bind=app_db.engine)
        engine.dispose()


def test_more_simultaneous_ingests_than_the_pool_holds_and_none_time_out(
    production_sized_pool_with_short_timeout, tmp_path
):
    from app.graph.build import invoke_in_background
    from app.routers.ingest import next_run_number

    llm = RecordingLLM(delay=MODEL_DELAY_SECONDS)
    config_for = lambda run_id: {  # noqa: E731
        "configurable": {
            "thread_id": run_id,
            "diagnostic_agent": DiagnosticAgent(llm_client=llm, cache=InMemoryDiagnosisCache(), sleep=_no_sleep),
            "narrative_agent": NarrativeAgent(llm_client=llm, sleep=_no_sleep),
            "summary_agent": SessionSummaryAgent(llm_client=llm, sleep=_no_sleep),
        }
    }

    runs: list[tuple[str, str]] = []
    with SessionLocal() as db:
        for i in range(CONCURRENT_RUNS):
            path = tmp_path / f"concurrent_{i}.csv"
            _write_csv(path, _clean_df(40 + i))
            source = DataSource(type="file", connection_config={"path": str(path)})
            db.add(source)
            db.flush()
            run = Run(source_id=source.id, status="running", started_at=pd.Timestamp.utcnow().to_pydatetime())
            run.run_number = next_run_number(db)
            db.add(run)
            db.commit()
            runs.append((run.id, source.id))

    threads = [
        threading.Thread(target=invoke_in_background, args=(run_id, {"run_id": run_id, "source_id": source_id}, config_for(run_id)))
        for run_id, source_id in runs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not any(t.is_alive() for t in threads), "a run never finished"

    run_ids = [run_id for run_id, _ in runs]
    with SessionLocal() as db:
        statuses = {r.id: r.status for r in db.query(Run).filter(Run.id.in_(run_ids)).all()}
        pool_failures = [
            t.output_summary
            for t in db.query(AgentTrace).filter(AgentTrace.run_id.in_(run_ids)).all()
            if "QueuePool" in (t.output_summary or "") or "TimeoutError" in (t.output_summary or "")
        ]
        reports = db.query(Report).filter(Report.run_id.in_(run_ids)).count()
        summaries = db.query(SessionSummary).filter(SessionSummary.run_id.in_(run_ids)).count()

    assert not pool_failures, f"{len(pool_failures)} node(s) failed on the pool: {pool_failures[:2]}"
    assert all(s == "completed" for s in statuses.values()), f"not every run completed: {statuses}"
    assert reports == CONCURRENT_RUNS, f"only {reports} of {CONCURRENT_RUNS} runs got a report"
    assert summaries == CONCURRENT_RUNS, f"only {summaries} of {CONCURRENT_RUNS} runs got a summary"

    # Non-vacuous: the slow model really was called, by every run, from
    # both of the agents that make an ingest write a report.
    schemas = {schema for schema, _ in llm.calls}
    assert len(llm.calls) >= 2 * CONCURRENT_RUNS, f"only {len(llm.calls)} model calls - the load never happened"
    assert "GroundedClaimsResponse" in schemas and "SummaryClaimsResponse" in schemas, schemas
    assert set(llm.held_during_calls()) == {0}, "a model call was made while a pooled connection was held"


# ---------------------------------------------------------------------------
# 2. PRECISION - every call site, zero connections at the moment of the call
# ---------------------------------------------------------------------------


@pytest.fixture
def corrupted_run(tmp_path, monkeypatch):
    """A run whose 'amount' column was re-typed as text AND scaled 1000x -
    the resolution tests' reveal scenario. safe_type_cast fixes the dtype,
    and that reveals the distribution drift it had been hiding, which is
    diagnosed as its own event. So one run exercises a diagnosis AND a
    reveal-diagnosis."""
    clean = pd.DataFrame(
        {"id": range(80), "amount": np.linspace(1.0, 100.0, 80), "city": (["New York", "Los Angeles", "Chicago"] * 27)[:80]}
    )
    corrupted = clean.copy()
    corrupted["amount"] = (clean["amount"] * 1000).astype(str)
    path = tmp_path / "corrupted.csv"
    corrupted.to_csv(path, index=False)

    # A CSV round-trip re-parses the text column as numbers, which would leave
    # nothing to fix and nothing to reveal. These tests are about session
    # handling, not CSV parsing, so the code under test is handed the
    # in-memory frame the resolution tests use.
    from app.contract import DataContract, SourceType

    def frame(*_args, **_kwargs):
        return DataContract(data=corrupted.copy(), source_type=SourceType.FILE, source_id="corrupted")

    monkeypatch.setattr("app.graph.nodes.repaired_contract_for_run", frame)
    monkeypatch.setattr("app.resolution.base_contract_for_run", frame)

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": str(path)})
        db.add(source)
        db.flush()
        db.add(Baseline(source_id=source.id, profile_json=BaselineProfiler().profile(clean), is_active=True, is_provisional=False))
        run = Run(source_id=source.id, status="running")
        db.add(run)
        db.flush()
        event = ValidationEvent(
            run_id=run.id,
            rule_failed="schema_conformance:dtype_mismatch:amount",
            state=DETECTED,
            column_name="amount",
            detail_json={"expected_dtype": "float64"},
            against_provisional_baseline=False,
        )
        db.add(event)
        db.commit()
        return run.id, source.id, event.id


def _resolve(run_id, llm):
    from app.graph.nodes import resolve_node

    agent = DiagnosticAgent(llm_client=llm, cache=InMemoryDiagnosisCache(), sleep=_no_sleep)
    return resolve_node({"run_id": run_id}, {"configurable": {"diagnostic_agent": agent}})


def test_diagnosis_in_resolve_node_holds_no_connection_and_still_writes_everything(corrupted_run):
    run_id, _source_id, event_id = corrupted_run
    llm = RecordingLLM(answer=SAFE_TYPE_CAST)

    outcome = _resolve(run_id, llm)

    assert llm.calls, "resolve_node made no model call - this checks nothing"
    assert set(llm.held_during_calls()) == {0}, llm.calls
    with SessionLocal() as db:
        event = db.get(ValidationEvent, event_id)
        run = db.get(Run, run_id)
        revealed = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id, ValidationEvent.id != event_id).all()
        # Everything the loop wrote into the buffer reached the database.
        assert event.state == "auto_fixed"
        assert run.fix_chain and run.fix_chain[0]["action"] == "safe_type_cast"
        assert revealed and all(e.state == AWAITING_APPROVAL for e in revealed), "the revealed drift must escalate"
        # Written onto the DETACHED run by process_queue, and carried across.
        assert run.reveal_depth_reached == 1
    assert outcome["route"] == "await_human"


def test_the_approval_path_diagnoses_its_reveals_with_no_connection_held(corrupted_run):
    from app.resolution import apply_approve

    run_id, source_id, event_id = corrupted_run
    with SessionLocal() as db:
        event = db.get(ValidationEvent, event_id)
        event.state = AWAITING_APPROVAL
        event.diagnosis_json = SAFE_TYPE_CAST.model_dump(mode="json")
        db.get(Run, run_id).status = "awaiting_approval"
        db.commit()

    llm = RecordingLLM(answer=SAFE_TYPE_CAST)
    agent = DiagnosticAgent(llm_client=llm, cache=InMemoryDiagnosisCache(), sleep=_no_sleep)
    with SessionLocal() as db:
        run, source, event = db.get(Run, run_id), db.get(DataSource, source_id), db.get(ValidationEvent, event_id)
        outcome = apply_approve(db, run, source, event, [event], "tester", agent, 0.5, 3)

    assert outcome.applied
    assert llm.calls, "the approved fix revealed nothing to diagnose - this checks nothing"
    assert set(llm.held_during_calls()) == {0}, llm.calls
    with SessionLocal() as db:
        revealed = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id, ValidationEvent.id != event_id).all()
        assert revealed and all(e.state == AWAITING_APPROVAL for e in revealed)
        assert db.get(ValidationEvent, event_id).state == "resolved"


def test_narrative_summary_query_and_modeling_calls_hold_no_connection(client, tmp_path):
    from app.main import app
    from app.modeling.agent import ModelingAgent
    from app.modeling.dependency import get_modeling_agent
    from app.narrative.dependency import get_narrative_agent
    from app.query.agent import QueryAgent
    from app.query.dependency import get_query_agent
    from app.summary.dependency import get_summary_agent

    llm = RecordingLLM()
    overrides = {
        get_narrative_agent: lambda: NarrativeAgent(llm_client=llm, sleep=_no_sleep),
        get_summary_agent: lambda: SessionSummaryAgent(llm_client=llm, sleep=_no_sleep),
        get_query_agent: lambda: QueryAgent(llm_client=llm, cache=InMemoryQueryCache(), sleep=_no_sleep),
        get_modeling_agent: lambda: ModelingAgent(llm_client=llm, cache=InMemoryModelingCache(), sleep=_no_sleep),
    }
    previous = {dep: app.dependency_overrides.get(dep) for dep in overrides}
    app.dependency_overrides.update(overrides)
    try:
        path = tmp_path / "precision.csv"
        _write_csv(path, _clean_df(60))
        result = ingest_and_wait(client, _create_source(client, path))
        run_id = result["run_id"]
        client.post("/ask", json={"run_id": run_id, "question": "what is the total amount?"})
        client.post("/predict", json={"run_id": run_id, "question": "forecast amount"})
    finally:
        for dep, prior in previous.items():
            if prior is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prior

    by_site = {}
    for schema, held in llm.calls:
        by_site.setdefault(schema, []).append(held)
    for site in ("GroundedClaimsResponse", "SummaryClaimsResponse", "GeneratedQuery", "IntentClassification"):
        assert site in by_site, f"no {site} call was made - that call site was not exercised: {sorted(by_site)}"
    assert all(h == 0 for held in by_site.values() for h in held), by_site


# ---------------------------------------------------------------------------
# 3. THE GAP - the run changes while the model is answering
# ---------------------------------------------------------------------------


def _set_run_status(run_id, status):
    def hook():
        with SessionLocal() as db:
            db.get(Run, run_id).status = status
            db.commit()

    return hook


def test_a_run_rejected_during_diagnosis_is_not_overwritten(corrupted_run):
    """A reject_data on another pending event sets the run "failed" while the
    Diagnostic Agent is still answering. Nothing from that diagnosis may be
    written over it - but the call went out, so its egress row must be."""
    run_id, _source_id, event_id = corrupted_run
    llm = RecordingLLM(answer=SAFE_TYPE_CAST, during_call=_set_run_status(run_id, "failed"))

    outcome = _resolve(run_id, llm)

    assert outcome["route"] == "failed"
    with SessionLocal() as db:
        assert db.get(ValidationEvent, event_id).state == DETECTED, "the diagnosis was written over a failed run"
        assert not db.get(Run, run_id).fix_chain, "a fix was recorded against a run that had been rejected"
        assert db.query(EgressEvent).filter(EgressEvent.run_id == run_id).count() >= 1, "the call that went out was not recorded"
        discard = [t for t in db.query(AgentTrace).filter(AgentTrace.run_id == run_id, AgentTrace.agent_name == "resolve").all()
                   if "discarded" in (t.output_summary or "")]
        assert discard, "a dropped diagnosis must leave a trace saying why"


def test_events_resolved_elsewhere_during_diagnosis_are_not_overwritten(corrupted_run):
    """Another resolution of the same run processes these events first."""
    run_id, _source_id, event_id = corrupted_run

    def someone_else_resolves():
        with SessionLocal() as db:
            db.get(ValidationEvent, event_id).state = "diagnosed"
            db.commit()

    _resolve(run_id, RecordingLLM(answer=SAFE_TYPE_CAST, during_call=someone_else_resolves))

    with SessionLocal() as db:
        assert db.get(ValidationEvent, event_id).state == "diagnosed", "the other resolution's write was overwritten"
        assert not db.get(Run, run_id).fix_chain


def _completed_run_with_findings(client, tmp_path) -> str:
    path = tmp_path / "gap.csv"
    _write_csv(path, _clean_df(60))
    return ingest_and_wait(client, _create_source(client, path))["run_id"]


def test_a_report_written_meanwhile_is_kept_and_this_one_dropped(client, tmp_path):
    from app.models import ExplorationFinding
    from app.narrative.pipeline import generate_for_inputs, load_narrative_inputs, save_narrative_report

    run_id = _completed_run_with_findings(client, tmp_path)
    with SessionLocal() as db:
        db.query(Report).filter(Report.run_id == run_id).delete()
        db.commit()
        run = db.get(Run, run_id)
        inputs = load_narrative_inputs(db, run, db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one())

    report, egress = generate_for_inputs(inputs, _clean_df(60), None)

    with SessionLocal() as db:  # someone else finishes first
        db.add(Report(run_id=run_id, narrative_text="the other writer's report", generation_mode="template"))
        db.commit()

    with SessionLocal() as db:
        save_narrative_report(db, inputs, report, egress)
        db.commit()
        reports = db.query(Report).filter(Report.run_id == run_id).all()
        assert [r.narrative_text for r in reports] == ["the other writer's report"]
        assert db.query(AgentTrace).filter(AgentTrace.run_id == run_id, AgentTrace.edge_taken == "skipped").count() == 1


def test_no_summary_is_written_against_a_run_that_stopped_being_completed(client, tmp_path):
    from app.summary.pipeline import generate_summary, load_summary_inputs, save_summary

    run_id = _completed_run_with_findings(client, tmp_path)
    with SessionLocal() as db:
        db.query(SessionSummary).filter(SessionSummary.run_id == run_id).delete()
        db.commit()
        inputs = load_summary_inputs(db, db.get(Run, run_id))

    result = generate_summary(inputs, None)
    _set_run_status(run_id, "failed")()

    with SessionLocal() as db:
        save_summary(db, inputs, result)
        db.commit()
        assert db.query(SessionSummary).filter(SessionSummary.run_id == run_id).count() == 0


# ---------------------------------------------------------------------------
# The request's own session
# ---------------------------------------------------------------------------


def test_every_endpoint_that_schedules_background_work_closes_its_session_first():
    """FastAPI (0.141 here) exits a request dependency AFTER the request's
    background tasks. So POST /ingest's request session stayed open for the
    whole graph run - and after its post-commit refresh it held a pooled
    connection throughout, model calls included, even once every node had
    been fixed. The precision test above caught it: through the real HTTP
    path, the narrative, summary and modeling calls ran with ONE connection
    held.

    Depends(get_db, scope="function") closes the session when the endpoint
    returns, before the background task. This test is what keeps it that
    way: any endpoint that takes BackgroundTasks and a get_db session must
    declare that session function-scoped.
    """
    import importlib
    import inspect
    import pkgutil

    from fastapi import BackgroundTasks

    import app.routers as routers
    from app.db import get_db

    checked, wrong = [], []
    for module_info in pkgutil.iter_modules(routers.__path__):
        module = importlib.import_module(f"app.routers.{module_info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__:
                continue
            params = inspect.signature(fn).parameters.values()
            if not any(p.annotation in (BackgroundTasks, "BackgroundTasks") for p in params):
                continue
            for p in params:
                if getattr(p.default, "dependency", None) is get_db:
                    checked.append(f"{module_info.name}.{name}")
                    if getattr(p.default, "scope", None) != "function":
                        wrong.append(f"{module_info.name}.{name}")

    assert checked, "found no endpoint scheduling background work - this checks nothing"
    assert not wrong, f"these hold their request session open through background work: {wrong}"
