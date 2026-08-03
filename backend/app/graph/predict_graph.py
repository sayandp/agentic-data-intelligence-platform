"""Phase 8 Part 0's approved, deliberately narrow scope for a predict
subgraph - same reasoning as app/graph/query_graph.py. Three nodes,
matching the real phase boundaries app/modeling/pipeline.py::predict
already had:

    classify -> {retrieval, train, done}
    train    -> {gate, done}
    gate     -> done

`classify` resolves (or LLM-classifies) the target column and routes a
retrieval-intent question straight to the existing Query Agent rather than
rebuilding that path; `train` performs task-type selection, the per-task
row/feature sufficiency checks, and the actual training call; `gate` runs
the post-training baseline/floor/imbalance checks
(app/modeling/pipeline.py's `_finish_*` functions, unchanged). Every check
inside each node keeps its original order and logic exactly - only the
node boundaries are new.

No checkpointer, same reasoning as the query subgraph: approving an
escalated model is "accept the already-computed result" (app/modeling/
pipeline.py::resolve_escalated_model_approval), never a resumed
invocation - nothing here is ever suspended, so there is nothing that
would need to survive a process restart.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.modeling.leakage import select_features
from app.modeling.models import EscalationReason, IntentKind, ModelAnswerStatus, TaskType
from app.modeling.splitting import split_strategy_for_task
from app.modeling.task_selection import build_time_series, choose_aggregation, infer_forecast_frequency, select_task_type


class PredictState(TypedDict, total=False):
    db: Any
    modeling_agent: Any
    run: Any
    source: Any
    question: str | None
    target_column: str | None
    config: Any
    quality_summary: str
    schema: dict
    contract: Any
    resolved_target: str | None
    task_type: Any
    datetime_column: str | None
    split_strategy: Any
    findings: Any
    excluded: list
    training: Any
    gate_kind: str
    is_retrieval: bool
    result: Any
    route: str
    existing_model_run: Any


MAX_SAMPLE_ROWS = 5


def classify_node(state: PredictState, config) -> dict:
    from app.modeling.pipeline import _persist

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    schema, contract = state["schema"], state["contract"]
    modeling_agent = state["modeling_agent"]
    existing = state.get("existing_model_run")

    resolved_target = state.get("target_column")
    if resolved_target is None:
        if modeling_agent is None:
            result = _persist(
                db, run, source, question, quality_summary,
                status=ModelAnswerStatus.ESCALATED,
                escalation_reason=EscalationReason.LLM_UNAVAILABLE.value,
                escalation_detail="no LLM is configured/reachable for intent classification - ask again once one is available, or name target_column explicitly",
                existing=existing,
            )
            return {"result": result, "route": "done"}

        sample_rows = contract.data.head(MAX_SAMPLE_ROWS).to_dict(orient="records")
        outcome = modeling_agent.classify_intent(question or "", schema, sample_rows)
        if outcome.classification is None:
            result = _persist(
                db, run, source, question, quality_summary,
                status=ModelAnswerStatus.ESCALATED,
                escalation_reason=EscalationReason.LLM_UNAVAILABLE.value,
                escalation_detail=f"intent classification failed: {outcome.source}",
                existing=existing,
            )
            return {"result": result, "route": "done"}
        classification = outcome.classification

        if classification.intent == IntentKind.UNANSWERABLE:
            result = _persist(
                db, run, source, question, quality_summary,
                status=ModelAnswerStatus.ESCALATED,
                escalation_reason=EscalationReason.UNANSWERABLE.value,
                escalation_detail="the model reported this question cannot be answered from the given schema",
                awaiting_approval=True, existing=existing,
            )
            return {"result": result, "route": "done"}

        if classification.intent == IntentKind.RETRIEVAL:
            return {"is_retrieval": True, "route": "done"}

        resolved_target = classification.target_column

    if not resolved_target or resolved_target not in schema:
        result = _persist(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED,
            escalation_reason=EscalationReason.TARGET_NOT_FOUND.value,
            escalation_detail=f"target_column {resolved_target!r} is absent from the live schema",
            awaiting_approval=True, existing=existing,
        )
        return {"result": result, "route": "done"}

    return {"resolved_target": resolved_target, "route": "train"}


def train_node(state: PredictState, config) -> dict:
    from app.modeling.automl import evaluate_classification, evaluate_forecast, evaluate_regression
    from app.modeling.pipeline import _load_findings, _persist, _persist_with_excluded

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    contract, cfg = state["contract"], state["config"]
    resolved_target = state["resolved_target"]
    existing = state.get("existing_model_run")

    df = contract.data
    task_type, datetime_column = select_task_type(df, resolved_target, cfg)

    if task_type == TaskType.UNSUPPORTED:
        result = _persist(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=resolved_target,
            escalation_reason=EscalationReason.UNSUPPORTED_TASK_TYPE.value,
            escalation_detail=f"column {resolved_target!r} does not fit a supported task shape (forecast/classification/regression) from data shape alone",
            awaiting_approval=True, existing=existing,
        )
        return {"result": result, "route": "done"}

    split_strategy = split_strategy_for_task(task_type)
    findings = _load_findings(db, run)

    if task_type == TaskType.FORECAST:
        df = df.assign(**{datetime_column: pd.to_datetime(df[datetime_column], errors="coerce")})
        excluded = select_features(df, resolved_target, datetime_column, findings, cfg)[1]
        aggregation = choose_aggregation(df, resolved_target)
        series = build_time_series(df, resolved_target, datetime_column, aggregation, cfg)
        freq = infer_forecast_frequency(df[datetime_column], cfg)
        if len(series) < cfg.min_periods_for_forecast:
            result = _persist_with_excluded(
                db, run, source, question, quality_summary,
                status=ModelAnswerStatus.ESCALATED, target_column=resolved_target, task_type=task_type, outcome=None, split_strategy=split_strategy,
                excluded_features=excluded,
                escalation_reason=EscalationReason.INSUFFICIENT_ROWS.value,
                escalation_detail=(
                    f"only {len(series)} aggregated period(s) available after resampling to {freq!r}; "
                    f"forecasting requires at least {cfg.min_periods_for_forecast}"
                ),
                awaiting_approval=True, existing=existing,
            )
            return {"result": result, "route": "done"}
        training = evaluate_forecast(series, freq, cfg)
        return {
            "training": training, "excluded": excluded, "split_strategy": split_strategy, "task_type": task_type,
            "resolved_target": resolved_target, "gate_kind": "forecast", "route": "gate",
        }

    feature_columns, excluded = select_features(df, resolved_target, None, findings, cfg)
    working_row_count = df[resolved_target].notna().sum()
    if working_row_count < cfg.min_rows:
        result = _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=resolved_target, task_type=task_type, outcome=None, split_strategy=split_strategy,
            excluded_features=excluded,
            escalation_reason=EscalationReason.INSUFFICIENT_ROWS.value,
            escalation_detail=f"only {working_row_count} row(s) available after cleaning; the minimum is {cfg.min_rows}",
            awaiting_approval=True, existing=existing,
        )
        return {"result": result, "route": "done"}
    if not feature_columns:
        result = _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=resolved_target, task_type=task_type, outcome=None, split_strategy=split_strategy,
            excluded_features=excluded,
            escalation_reason=EscalationReason.UNSUPPORTED_TASK_TYPE.value,
            escalation_detail="every candidate feature was excluded by leakage prevention; nothing usable remains to train on",
            awaiting_approval=True, existing=existing,
        )
        return {"result": result, "route": "done"}

    if task_type == TaskType.CLASSIFICATION:
        training = evaluate_classification(df, feature_columns, resolved_target, cfg)
        gate_kind = "classification"
    else:
        training = evaluate_regression(df, feature_columns, resolved_target, cfg)
        gate_kind = "regression"

    return {
        "training": training, "excluded": excluded, "split_strategy": split_strategy, "task_type": task_type,
        "resolved_target": resolved_target, "gate_kind": gate_kind, "route": "gate",
    }


def gate_node(state: PredictState, config) -> dict:
    from app.modeling.pipeline import _finish_classification, _finish_forecast, _finish_regression

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    cfg = state["config"]
    args = (
        db, run, source, question, quality_summary,
        state["resolved_target"], state["task_type"], state["split_strategy"], state["training"], state["excluded"], cfg,
    )
    dispatch = {"forecast": _finish_forecast, "classification": _finish_classification, "regression": _finish_regression}
    result = dispatch[state["gate_kind"]](*args, existing=state.get("existing_model_run"))
    return {"result": result, "route": "done"}


def _route(state: PredictState) -> str:
    return state["route"]


def build_predict_graph():
    graph = StateGraph(PredictState)
    graph.add_node("classify", classify_node)
    graph.add_node("train", train_node)
    graph.add_node("gate", gate_node)
    graph.set_entry_point("classify")
    graph.add_conditional_edges("classify", _route, {"train": "train", "done": END})
    graph.add_conditional_edges("train", _route, {"gate": "gate", "done": END})
    graph.add_edge("gate", END)
    return graph.compile()


_compiled_predict_graph = None


def get_predict_graph():
    global _compiled_predict_graph
    if _compiled_predict_graph is None:
        _compiled_predict_graph = build_predict_graph()
    return _compiled_predict_graph
