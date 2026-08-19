import os
import tempfile
from pathlib import Path

import pytest

# Must be set before any `app.*` module is imported, since app/db.py reads
# DATABASE_URL at import time.
_TEST_DB_PATH = Path(tempfile.gettempdir()) / "agentic_platform_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

# Phase 8: the graph's checkpointer is a SEPARATE store from the app DB
# (app/graph/checkpointer.py) - it needs its own isolated, resettable path
# for the same reason DATABASE_URL gets one, or checkpointed positions from
# one test's runs would leak into the next.
_TEST_CHECKPOINT_PATH = Path(tempfile.gettempdir()) / "agentic_platform_test_checkpoints.db"
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
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache  # noqa: E402


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

    app.dependency_overrides[get_diagnostic_agent] = _fake_diagnostic_agent
    app.dependency_overrides[get_narrative_agent] = _no_narrative_agent
    app.dependency_overrides[get_query_agent] = _no_query_agent
    app.dependency_overrides[get_modeling_agent] = _no_modeling_agent
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)
        app.dependency_overrides.pop(get_narrative_agent, None)
        app.dependency_overrides.pop(get_query_agent, None)
        app.dependency_overrides.pop(get_modeling_agent, None)
