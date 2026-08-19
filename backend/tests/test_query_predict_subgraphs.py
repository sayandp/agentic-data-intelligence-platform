"""Phase 8 Part 0/1: a light check that app/graph/query_graph.py and
app/graph/predict_graph.py are genuine branching graphs, not decoration
around an already-linear function. tests/test_query_agent.py and
tests/test_modeling_pipeline.py already exercise every branch's OUTCOME
exhaustively (escalation_reason, result shape, etc) end to end through
POST /ask and POST /predict - unchanged by this migration, still green.
This file adds only what those didn't already cover: the graph's own
`route` value at each node, confirming the branching is real and traced,
not merely a coincidence of the persisted result.
"""

from __future__ import annotations

import pandas as pd

from app.graph.predict_graph import classify_node
from app.graph.query_graph import generate_node
from app.modeling.models import IntentKind


class _FakeRun:
    id = "run-1"
    # The run carries its semantic roles for every agent that needs column
    # context (app/semantic_roles.py). None here: this double stands in for
    # a run with no roles detected, which the graph must still handle.
    semantic_roles = None
    # The egress boundary reads this to decide what to mask. None means
    # "nothing classified", which redacts nothing - the pre-privacy behaviour.
    privacy_classification = None


class _FakeSource:
    id = "source-1"


class _FakeContract:
    data = pd.DataFrame({"amount": [1.0, 2.0]})


class _FakeResult:
    pass


def test_query_graph_generate_routes_done_when_no_llm_available(monkeypatch):
    # query_agent=None is the one branch generate_node can decide entirely
    # on its own, with no LLM/DB-dependent generation call needed.
    import app.query.pipeline as pipeline

    calls = []
    monkeypatch.setattr(pipeline, "_persist", lambda *a, **k: calls.append(k) or _FakeResult())

    state = {
        "db": None, "query_agent": None, "run": _FakeRun(), "source": _FakeSource(),
        "question": "irrelevant", "quality_summary": "", "schema": {}, "contract": _FakeContract(),
        "query_kind": None,
    }
    result = generate_node(state, {})
    assert result["route"] == "done"
    assert calls[0]["escalation_reason"] == "llm_unavailable"


def test_predict_graph_classify_routes_retrieval_without_persisting_anything(monkeypatch):
    import app.graph.predict_graph as predict_graph
    import app.modeling.pipeline as modeling_pipeline

    calls = []
    monkeypatch.setattr(modeling_pipeline, "_persist", lambda *a, **k: calls.append(k) or _FakeResult())

    class _Classification:
        intent = IntentKind.RETRIEVAL
        target_column = None

    class _Outcome:
        classification = _Classification()
        source = "llm"

    class _Client:
        model_name = "fake-model"

    class _Agent:
        # The node reads the client to record WHICH provider the sample went
        # to, so the double carries one - a stand-in that cannot answer that
        # question is not standing in for the real agent.
        llm_client = _Client()

        def classify_intent(self, *a, **k):
            return _Outcome()

    # The egress record is captured rather than written: this test has no DB
    # (db=None, deliberately) and persist_egress is not None-tolerant, because
    # a persist that silently does nothing when handed no session is exactly
    # the silent fallback this codebase keeps removing.
    egress = []
    monkeypatch.setattr(predict_graph, "persist_egress", lambda db, run_id, records: egress.extend(records))

    state = {
        "db": None, "modeling_agent": _Agent(), "run": _FakeRun(), "source": _FakeSource(),
        "question": "what will next month look like", "target_column": None, "config": None,
        "quality_summary": "", "schema": {"amount": "float"}, "contract": _FakeContract(),
    }
    result = classify_node(state, {})
    assert result["route"] == "done"
    assert result["is_retrieval"] is True
    assert calls == []  # a retrieval-routed question never persists a ModelRun
    # But the sample DID go to a model to find that out, and that is recorded.
    # "We only routed it" is not the same as "nothing left the machine".
    assert len(egress) == 1
    assert egress[0].agent == "modeling"
    # The CLIENT class, not the agent wrapper - two agents sharing a
    # provider must read as the same disclosure, and vice versa.
    assert egress[0].provider == "_Client"
