"""Part 7: POST /predict - the Modeling Agent's HTTP surface.

A separate endpoint from POST /ask (app/routers/query.py), not an
extension of it: /ask's request/response shape is Query-specific
(query_kind, code, columns_referenced) and already has 344 passing tests
built against its exact contract; folding prediction in would mean either
overloading that shape with modeling-only fields or branching its response
model on intent after the fact. Keeping /predict separate means each
surface stays a direct match for its own domain, and a retrieval-routed
question (Part 1) is handled by calling the Phase 6 Query Agent's own
ask_question() internally and returning ITS answer shape unchanged, rather
than reshaping it to fit here.

Body is {source_id or run_id, question and/or target_column}: an explicit
target_column trains directly, no LLM required (Part 7's template/degrade
rule - losing the LLM must not lose the modelling capability). A 404 here
means only "no such source/run to even ask against"; every LLM/leakage/
split/baseline outcome - including "no model beat the baseline" - is a
clean 200 with an escalated result, never a 500.

DASHBOARD UX PASS, PART 1 - RESOLVE-HANG-style fix, third instance in this
project (after POST /ingest and POST /approvals/{id}/resolve): model
training can mean fitting several candidate families across multiple CV
folds (forecast: Prophet + ARIMA, each refit per fold, plus a full refit
for the chart series) - genuinely slow, and holding the request open for it
is the identical bug already fixed twice elsewhere. This endpoint now
creates a placeholder ModelRun row synchronously (state="running"), returns
its id immediately, and runs the actual predict() pipeline via
BackgroundTasks - every terminal path through app/graph/predict_graph.py
updates THAT SAME row in place (app/modeling/pipeline.py::_persist's
`existing` param) rather than creating a second one the caller's id
wouldn't match. GET /models/{id} (already existed, for direct lookup) is
now also the poll target - reused rather than a second status endpoint,
the same choice the ingest/resolve fixes made.

Retrieval-routing (a "predict" question that turns out to be a retrieval
one) is the one path that produces a QueryRun instead of filling in the
placeholder - the background wrapper below marks the placeholder
"redirected" with a pointer to the real QueryRun id rather than leaving it
stuck at "running" forever.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import ModelRun, QueryRun
from app.modeling.agent import ModelingAgent
from app.modeling.dependency import get_modeling_agent
from app.modeling.models import ModelAnswerStatus
from app.modeling.pipeline import predict
from app.query.agent import QueryAgent
from app.query.dependency import get_query_agent
from app.query.pipeline import resolve_run

router = APIRouter(tags=["predict"])


class PredictRequest(BaseModel):
    source_id: str | None = None
    run_id: str | None = None
    question: str | None = None
    target_column: str | None = None

    @model_validator(mode="after")
    def _require_targets(self) -> "PredictRequest":
        if not self.source_id and not self.run_id:
            raise ValueError("either source_id or run_id is required")
        if not self.question and not self.target_column:
            raise ValueError("either question or target_column is required")
        return self


def _serialize_model_run(record: ModelRun) -> dict:
    return {
        "id": record.id,
        "routed_to": "prediction",  # a ModelRun row is, by construction, never the retrieval branch (see redirected_query_run_id)
        "status": ModelAnswerStatus.ANSWERED.value if record.state == "answered" else ModelAnswerStatus.ESCALATED.value,
        "question": record.question,
        "quality_context_summary": record.quality_context_summary,
        "target_column": record.target_column,
        "task_type": record.task_type,
        "model_family": record.model_family,
        "hyperparameters": record.hyperparameters_json or {},
        "seed": record.seed,
        "candidate_scores": record.candidate_scores_json or [],
        "baseline_scores": record.baseline_scores_json or [],
        "excluded_features": record.excluded_features_json or [],
        "split_strategy": record.split_strategy,
        "row_count_trained_on": record.row_count_trained_on,
        "class_distribution": record.class_distribution_json,
        "out_of_sample_metric": record.out_of_sample_metric,
        "out_of_sample_score": record.out_of_sample_score,
        "prediction_interval": record.prediction_interval_json,
        "feature_associations": record.feature_associations_json or [],
        "forecast_series": record.forecast_series_json,
        "escalation_reason": record.escalation_reason,
        "escalation_detail": record.escalation_detail,
        "state": record.state,
        "redirected_query_run_id": record.redirected_query_run_id,
    }


def _run_predict_in_background(
    placeholder_id: str,
    source_id: str | None,
    run_id: str | None,
    question: str | None,
    target_column: str | None,
    modeling_agent: ModelingAgent | None,
    query_agent: QueryAgent | None,
) -> None:
    """Runs OUTSIDE the request/response cycle - see this module's
    docstring. modeling_agent/query_agent are resolved via FastAPI's
    Depends()/dependency_overrides at request time (before this function
    ever runs) and passed in already-constructed, same pattern as
    app/routers/ingest.py's background task."""
    with SessionLocal() as db:
        placeholder = db.get(ModelRun, placeholder_id)
        try:
            result = predict(
                db, modeling_agent, query_agent,
                source_id=source_id, run_id=run_id, question=question, target_column=target_column,
                existing_model_run=placeholder,
            )
        except Exception:  # noqa: BLE001 - defense in depth; every documented failure mode is already an escalated ModelRun, never an exception
            placeholder.state = "failed"
            db.commit()
            return

        if isinstance(result, QueryRun):
            # Retrieval-routed: the placeholder was never going to be
            # filled in with a task_type/model - mark it redirected rather
            # than leaving a poller waiting on fields that will never arrive.
            placeholder.state = "redirected"
            placeholder.redirected_query_run_id = result.id
            db.commit()
        # else: result IS the placeholder, already updated in place by _persist.


@router.post("/predict")
def predict_endpoint(
    payload: PredictRequest,
    background_tasks: BackgroundTasks,
    # scope="function": this session closes when the endpoint returns, BEFORE the
    # background task runs. With the default scope FastAPI keeps it open until the
    # background work finishes - and after a post-commit read it holds a pooled
    # connection for the entire graph run, model calls included.
    db: Session = Depends(get_db, scope="function"),
    modeling_agent: ModelingAgent | None = Depends(get_modeling_agent),
    query_agent: QueryAgent | None = Depends(get_query_agent),
):
    try:
        run = resolve_run(db, payload.source_id, payload.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    placeholder = ModelRun(
        source_id=run.source_id, run_id=run.id, question=payload.question, target_column=payload.target_column, state="running"
    )
    db.add(placeholder)
    db.commit()
    db.refresh(placeholder)

    background_tasks.add_task(
        _run_predict_in_background,
        placeholder.id, payload.source_id, payload.run_id, payload.question, payload.target_column, modeling_agent, query_agent,
    )

    return {"id": placeholder.id, "state": "running"}


@router.get("/models/{run_id}")
def get_model(run_id: str, db: Session = Depends(get_db)):
    record = db.get(ModelRun, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no model run with that id")
    return _serialize_model_run(record)
