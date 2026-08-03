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
    import app.modeling.pipeline as modeling_pipeline

    calls = []
    monkeypatch.setattr(modeling_pipeline, "_persist", lambda *a, **k: calls.append(k) or _FakeResult())

    class _Classification:
        intent = IntentKind.RETRIEVAL
        target_column = None

    class _Outcome:
        classification = _Classification()

    class _Agent:
        def classify_intent(self, *a, **k):
            return _Outcome()

    state = {
        "db": None, "modeling_agent": _Agent(), "run": _FakeRun(), "source": _FakeSource(),
        "question": "what will next month look like", "target_column": None, "config": None,
        "quality_summary": "", "schema": {"amount": "float"}, "contract": _FakeContract(),
    }
    result = classify_node(state, {})
    assert result["route"] == "done"
    assert result["is_retrieval"] is True
    assert calls == []  # a retrieval-routed question never persists a ModelRun
