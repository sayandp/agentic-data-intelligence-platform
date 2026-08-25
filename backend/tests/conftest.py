import os
import tempfile
from pathlib import Path

import pytest

# PER-PROCESS PATHS. Both stores below used to have one fixed name in the
# system temp directory, shared by every pytest process on the machine. The
# autouse fixture below calls drop_all before EVERY test, so a second pytest
# run - a `--collect-only` alongside a full run, two terminals, a watcher -
# deleted the first one's tables mid-test.
#
# The failure did not look like a collision. It looked like ~40 fixture errors
# and ~10 failures scattered across unrelated files, in a suite that passes
# clean when run alone. It was reported as a product regression three times
# before it was fixed, which is three times more than the fix cost.
#
# The pid is in the FILENAME rather than guarded by a lock or a warning,
# because a name that cannot collide needs no check that must catch one -
# the same reason the redaction boundary is a type rather than a review note.
_TEST_TMP = Path(tempfile.gettempdir())

# Must be set before any `app.*` module is imported, since app/db.py reads
# DATABASE_URL at import time.
_TEST_DB_PATH = _TEST_TMP / f"agentic_platform_test_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

# Phase 8: the graph's checkpointer is a SEPARATE store from the app DB
# (app/graph/checkpointer.py) - it needs its own isolated, resettable path
# for the same reason DATABASE_URL gets one, or checkpointed positions from
# one test's runs would leak into the next.
_TEST_CHECKPOINT_PATH = _TEST_TMP / f"agentic_platform_test_checkpoints_{os.getpid()}.db"
os.environ["GRAPH_CHECKPOINT_PATH"] = _TEST_CHECKPOINT_PATH.as_posix()

from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.diagnosis.agent import DiagnosticAgent  # noqa: E402
from app.graph.build import reset_graph  # noqa: E402
from app.main import app  # noqa: E402
from app.modeling.dependency import get_modeling_agent  # noqa: E402
from app.models import Base  # noqa: E402
from app.narrative.dependency import get_narrative_agent  # noqa: E402
from app.query.dependency import get_query_agent  # noqa: E402
from app.routers.ingest import get_diagnostic_agent  # noqa: E402
from app.summary.dependency import get_summary_agent  # noqa: E402
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _remove_this_process_databases():
    """Delete this process's two stores when the run ends.

    Per-process names fix the collision but would otherwise leave a pair of
    files in temp for every pytest run ever started on the machine. Cleanup is
    best-effort: on Windows a still-open handle makes the unlink fail, and a
    leftover temp file is not worth failing a green suite over."""
    yield
    # Both connections must be closed first. On Windows an open sqlite handle
    # holds an OS-level file lock and the unlink simply fails - which is how
    # the checkpoint store survived the first version of this fixture.
    # engine.dispose() covers the app DB; reset_graph() closes the
    # checkpointer's own raw sqlite3 connection, which the engine knows
    # nothing about.
    engine.dispose()
    reset_graph()
    for path in (_TEST_DB_PATH, _TEST_CHECKPOINT_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_graph()
    if _TEST_CHECKPOINT_PATH.exists():
        _TEST_CHECKPOINT_PATH.unlink()
    yield


@pytest.fixture
def db_session():
    """A session for reading what the app WROTE, in tests that assert on
    stored rows rather than on responses. Separate from the app's own
    request-scoped sessions on purpose: a test that shares a session with the
    code under test can pass on uncommitted state."""
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    # No test in this suite may reach a real LLM provider: every ingest that
    # runs through this client gets a fresh FakeLLMClient (deterministic,
    # in-memory cache) instead of the real GeminiClient the app would
    # otherwise construct via get_llm_client().
    def _fake_diagnostic_agent() -> DiagnosticAgent:
        return DiagnosticAgent(llm_client=FakeLLMClient(), cache=InMemoryDiagnosisCache(), sleep=lambda _seconds: None)

    # Default to no narrative LLM at all (None) rather than a fake client
    # returning a diagnosis-shaped default that doesn't fit either of the
    # Narrative Agent's two response schemas - that would just spend two
    # wasted fake calls per ingest before falling back to the template
    # anyway. None IS a legitimate, well-defined configuration
    # (app/narrative/dependency.py::get_narrative_agent already returns it
    # when no LLM is reachable) and exercises exactly the "no LLM" template
    # path Part 4 requires to always work - tests that specifically need the
    # LLM narrative path use tests.fakes.narrative_llm_override instead.
    def _no_narrative_agent():
        return None

    # Same reasoning as _no_narrative_agent: a diagnosis-shaped fake default
    # fits none of the Query Agent's response schema either, and most tests
    # don't exercise /ask at all. Tests that need the LLM query path use
    # tests.fakes.query_llm_override instead.
    def _no_query_agent():
        return None

    # Same reasoning again: most tests don't exercise POST /predict's LLM
    # intent-routing path at all, and a diagnosis-shaped fake default fits
    # none of the Modeling Agent's response schema either. Tests that need
    # it use tests.fakes.modeling_llm_override instead - and Part 7's
    # template/degrade rule means a request naming target_column explicitly
    # must still train successfully even with this default None in place.
    def _no_modeling_agent():
        return None

    # Same reasoning as _no_narrative_agent, and the same danger: without
    # this override the real get_summary_agent() constructs a real
    # GeminiClient and the suite starts making live API calls. That is
    # exactly what happened when the Session Summary Agent was first wired
    # in - caught by test_a_run_that_called_nothing_records_nothing, which
    # noticed egress rows appearing for a run that should have disclosed
    # nothing. Tests that need the LLM summary path use
    # tests.fakes.summary_llm_override.
    def _no_summary_agent():
        return None

    app.dependency_overrides[get_diagnostic_agent] = _fake_diagnostic_agent
    app.dependency_overrides[get_summary_agent] = _no_summary_agent
    app.dependency_overrides[get_narrative_agent] = _no_narrative_agent
    app.dependency_overrides[get_query_agent] = _no_query_agent
    app.dependency_overrides[get_modeling_agent] = _no_modeling_agent
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)
        app.dependency_overrides.pop(get_summary_agent, None)
        app.dependency_overrides.pop(get_narrative_agent, None)
        app.dependency_overrides.pop(get_query_agent, None)
        app.dependency_overrides.pop(get_modeling_agent, None)
