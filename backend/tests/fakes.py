"""Shared test doubles. No test anywhere in this suite may make a live LLM call."""

from __future__ import annotations

from contextlib import contextmanager

from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.cache import DiagnosisCache
from app.diagnosis.dependency import get_diagnostic_agent
from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.llm.base import LLMClient
from app.modeling.cache import ModelingCache
from app.query.cache import QueryCache

DEFAULT_FAKE_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.UNKNOWN,
    likely_cause="fake default: no specific diagnosis configured for this test",
    suggested_fix=SuggestedFix(action=FixAction.ESCALATE),
    risk_level=RiskLevel.HIGH,
    confidence=0.0,
)


class FakeLLMClient(LLMClient):
    """Deterministic LLMClient stand-in.

    `responses` is a queue consumed in order by successive complete() calls;
    each item is a Diagnosis to return, or an Exception instance to raise.
    Once exhausted, `default` is returned (or raised) repeatedly. Every call
    is recorded in `.calls` as (system, user) so tests can inspect exactly
    what was sent - including confirming untrusted sample data never
    escapes the delimited block as something else.
    """

    def __init__(self, responses=None, default: Diagnosis | Exception = DEFAULT_FAKE_DIAGNOSIS, model_name="fake-model"):
        self.temperature = 0.0
        self.model_name = model_name
        self._responses = list(responses or [])
        self._default = default
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, response_schema):
        self.calls.append((system, user))
        item = self._responses.pop(0) if self._responses else self._default
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)


class InMemoryDiagnosisCache(DiagnosisCache):
    """A DiagnosisCache that never touches disk - keeps tests isolated from
    each other and from the real .cache/diagnoses.json used outside tests."""

    def __init__(self):
        self._path = None  # unused, but keeps the parent's attribute shape
        self._data: dict[str, dict] = {}

    def _load(self) -> dict:
        return {}

    def _save(self) -> None:
        pass


class InMemoryQueryCache(QueryCache):
    """A QueryCache that never touches disk - same reasoning as
    InMemoryDiagnosisCache above, for the Query Agent's generation cache."""

    def __init__(self):
        self._path = None
        self._data: dict[str, dict] = {}

    def _load(self) -> dict:
        return {}

    def _save(self) -> None:
        pass


class InMemoryModelingCache(ModelingCache):
    """A ModelingCache that never touches disk - same reasoning as
    InMemoryQueryCache above, for the Modeling Agent's intent-classification
    cache."""

    def __init__(self):
        self._path = None
        self._data: dict[str, dict] = {}

    def _load(self) -> dict:
        return {}

    def _save(self) -> None:
        pass


class _NoMoreResponsesConfigured(Exception):
    """Raised by a narrative_llm_override FakeLLMClient once its queued
    responses run out - a loud failure instead of silently returning a
    wrong-shaped default for whichever stage asked next."""


@contextmanager
def diagnosis_override(default: Diagnosis):
    """Temporarily overrides get_diagnostic_agent with one that always
    returns `default`, for tests that need a SPECIFIC diagnosis rather than
    the `client` fixture's generic FakeLLMClient default.

    Restores whatever override (if any) was active before this context -
    never just pops it - because more than one route now depends on
    get_diagnostic_agent (app/routers/ingest.py's synchronous auto-apply AND
    app/routers/approvals.py's human-approve path, both via
    app/resolution.py for a do-no-harm reveal). Popping unconditionally on
    exit would leave a later call on either route with no override at all,
    falling through to a real LLMClient construction that fails outside a
    configured environment - exactly the failure mode a per-file duplicate
    of this contextmanager hit before it was consolidated here.
    """
    from app.main import app  # local import: avoids a hard import-time dependency on the FastAPI app for callers that don't need it

    previous = app.dependency_overrides.get(get_diagnostic_agent)

    def _override() -> DiagnosticAgent:
        return DiagnosticAgent(llm_client=FakeLLMClient(default=default), cache=InMemoryDiagnosisCache(), sleep=lambda _seconds: None)

    app.dependency_overrides[get_diagnostic_agent] = _override
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_diagnostic_agent] = previous
        else:
            app.dependency_overrides.pop(get_diagnostic_agent, None)


@contextmanager
def narrative_llm_override(responses: list):
    """Temporarily overrides get_narrative_agent with a NarrativeAgent
    wrapping a FakeLLMClient - for tests that need to exercise the actual
    two-stage LLM narrative path rather than the `client` fixture's default
    of no narrative LLM at all (see conftest.py, which exists precisely
    because a diagnosis-shaped fake default fits neither of the Narrative
    Agent's response schemas).

    `responses` is consumed in call order: stage 1's GroundedClaimsResponse
    first, then stage 2's NarrativeProse - and again, in the same order,
    for each regeneration attempt a test wants to exercise. Running out
    raises _NoMoreResponsesConfigured rather than silently returning a
    wrong-shaped default for whichever stage asked next.

    Restores whatever override (if any) was active before this context,
    same reasoning as diagnosis_override above.
    """
    from app.main import app  # local import: avoids a hard import-time dependency on the FastAPI app for callers that don't need it
    from app.narrative.agent import NarrativeAgent
    from app.narrative.dependency import get_narrative_agent

    previous = app.dependency_overrides.get(get_narrative_agent)

    def _override() -> NarrativeAgent:
        llm_client = FakeLLMClient(
            responses=list(responses), default=_NoMoreResponsesConfigured("narrative_llm_override ran out of queued responses"), model_name="fake-narrative-model"
        )
        return NarrativeAgent(llm_client=llm_client, sleep=lambda _seconds: None)

    app.dependency_overrides[get_narrative_agent] = _override
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_narrative_agent] = previous
        else:
            app.dependency_overrides.pop(get_narrative_agent, None)


@contextmanager
def summary_llm_override(responses: list):
    """Temporarily overrides get_summary_agent with a SessionSummaryAgent
    wrapping a FakeLLMClient - for tests that need the actual two-stage LLM
    summary path rather than the `client` fixture's default of no summary LLM.

    `responses` is consumed in call order: stage 1's SummaryClaimsResponse
    first, then stage 2's SessionSummaryProse, and again in that order for
    each regeneration attempt a test exercises. Running out raises rather
    than silently returning a wrong-shaped default.

    Restores whatever override was active before, same as the others."""
    from app.main import app
    from app.summary.agent import SessionSummaryAgent
    from app.summary.dependency import get_summary_agent

    previous = app.dependency_overrides.get(get_summary_agent)

    def _override() -> SessionSummaryAgent:
        llm_client = FakeLLMClient(
            responses=list(responses),
            default=_NoMoreResponsesConfigured("summary_llm_override ran out of queued responses"),
            model_name="fake-summary-model",
        )
        return SessionSummaryAgent(llm_client=llm_client, sleep=lambda _seconds: None)

    app.dependency_overrides[get_summary_agent] = _override
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_summary_agent] = previous
        else:
            app.dependency_overrides.pop(get_summary_agent, None)


@contextmanager
def query_llm_override(responses: list):
    """Temporarily overrides get_query_agent with a QueryAgent wrapping a
    FakeLLMClient - for tests that need to exercise the actual query
    generation path (including hostile-output cases the AST/sqlglot
    validators must catch) rather than the `client` fixture's default of no
    query LLM at all.

    `responses` is consumed in call order, one GeneratedQuery (or Exception)
    per generate() call. An InMemoryQueryCache is used so repeated identical
    questions within one test don't silently short-circuit to a cached
    response from an earlier queued item.
    """
    from app.main import app  # local import: avoids a hard import-time dependency on the FastAPI app for callers that don't need it
    from app.query.agent import QueryAgent
    from app.query.dependency import get_query_agent

    previous = app.dependency_overrides.get(get_query_agent)

    def _override() -> QueryAgent:
        llm_client = FakeLLMClient(
            responses=list(responses), default=_NoMoreResponsesConfigured("query_llm_override ran out of queued responses"), model_name="fake-query-model"
        )
        return QueryAgent(llm_client=llm_client, cache=InMemoryQueryCache(), sleep=lambda _seconds: None)

    app.dependency_overrides[get_query_agent] = _override
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_query_agent] = previous
        else:
            app.dependency_overrides.pop(get_query_agent, None)


@contextmanager
def modeling_llm_override(responses: list):
    """Temporarily overrides get_modeling_agent with a ModelingAgent
    wrapping a FakeLLMClient - for tests that need to exercise the actual
    intent-classification path (Part 1) rather than the `client` fixture's
    default of no modeling LLM at all.

    `responses` is consumed in call order, one IntentClassification (or
    Exception) per classify_intent() call. An InMemoryModelingCache is used
    so repeated identical questions within one test don't silently
    short-circuit to a cached response from an earlier queued item.
    """
    from app.main import app  # local import: avoids a hard import-time dependency on the FastAPI app for callers that don't need it
    from app.modeling.agent import ModelingAgent
    from app.modeling.dependency import get_modeling_agent

    previous = app.dependency_overrides.get(get_modeling_agent)

    def _override() -> ModelingAgent:
        llm_client = FakeLLMClient(
            responses=list(responses), default=_NoMoreResponsesConfigured("modeling_llm_override ran out of queued responses"), model_name="fake-modeling-model"
        )
        return ModelingAgent(llm_client=llm_client, cache=InMemoryModelingCache(), sleep=lambda _seconds: None)

    app.dependency_overrides[get_modeling_agent] = _override
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_modeling_agent] = previous
        else:
            app.dependency_overrides.pop(get_modeling_agent, None)
