"""Phase 7 - the Modeling Agent's HTTP surface and orchestration pipeline.

No test in this suite may make a live LLM call - tests.fakes.modeling_llm_override
wraps a FakeLLMClient exactly like query_llm_override/narrative_llm_override do.
Gating-logic tests (baseline-beat, score floor, class imbalance) construct a
TrainingOutcome directly rather than fighting to engineer a real dataset that
lands on an exact numeric threshold - the gating logic itself is real code
under test, only the model-fitting step is bypassed.
"""

from __future__ import annotations

import time

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score

from app.db import SessionLocal
from app.modeling.automl import TrainingOutcome, evaluate_classification
from app.modeling.config import ModelingConfig
from app.modeling.models import (
    BaselineScore,
    CandidateScore,
    ClassDistribution,
    IntentClassification,
    IntentKind,
    ModelFamily,
    SplitStrategy,
    TaskType,
)
from app.modeling.pipeline import _beats_baseline, _finish_classification, _finish_forecast, _finish_regression
from app.models import DataSource, ModelRun, QueryRun, Run
from app.narrative.quality import render_quality_context_summary
from app.query.models import GeneratedQuery, QueryKind
from app.query.pipeline import _quality_context_for_run
from tests.fakes import modeling_llm_override, query_llm_override

PREDICT_POLL_TIMEOUT_SECONDS = 30.0
PREDICT_POLL_INTERVAL_SECONDS = 0.02


def predict_and_wait(client, payload: dict) -> dict:
    """POST /predict now returns {id, state: "running"} immediately -
    training runs via BackgroundTasks (app/routers/predict.py's
    RESOLVE-HANG-style fix: CV/training across several candidate families
    can take real time, and holding one HTTP request open for it repeats
    the exact bug ingest/resolve already had fixed for them). Polls
    GET /models/{id} for the terminal shape this endpoint used to return
    synchronously - mirrors tests/golden_scenarios.py's ingest_and_wait/
    resolve_and_wait."""
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 200, resp.json()
    model_id = resp.json()["id"]
    deadline = time.monotonic() + PREDICT_POLL_TIMEOUT_SECONDS
    while True:
        body = client.get(f"/models/{model_id}").json()
        if body.get("state") != "running":
            return body
        if time.monotonic() > deadline:
            raise TimeoutError(f"predict {model_id} did not complete within {PREDICT_POLL_TIMEOUT_SECONDS}s")
        time.sleep(PREDICT_POLL_INTERVAL_SECONDS)


def _ingest_csv(client, tmp_path, csv_text: str, filename: str = "sample.csv"):
    path = tmp_path / filename
    path.write_text(csv_text, encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(path)}}).json()["id"]
    resp = client.post(f"/ingest/{source_id}")
    body = resp.json()
    assert resp.status_code == 200, body
    return source_id, body["run_id"]


def _classification_csv(n: int = 150) -> str:
    """id (identifier, must be excluded), feature1 (cleanly separates the
    label via a simple threshold - a real classifier should trounce the
    majority baseline; a plain parity/modulo target would NOT do this, since
    neither a linear model nor axis-aligned tree splits on a single
    continuous feature can learn "even/odd" out of sample), label (balanced
    two-class target)."""
    lines = ["id,feature1,label"]
    for i in range(n):
        label = "yes" if i >= n // 2 else "no"
        lines.append(f"row-{i},{i},{label}")
    return "\n".join(lines) + "\n"


def _regression_csv(n: int = 150) -> str:
    """feature1 is informative but deliberately noisy enough to stay well
    under the leakage correlation threshold (0.98) - a clean linear copy of
    the target would (correctly) be dropped as leakage instead of trained
    on."""
    lines = ["id,feature1,amount"]
    for i in range(n):
        noise = (i * 7919) % 97
        lines.append(f"row-{i},{i},{i + noise}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Request validation / 404s.
# ---------------------------------------------------------------------------


def test_predict_requires_source_id_or_run_id(client):
    resp = client.post("/predict", json={"target_column": "amount"})
    assert resp.status_code == 422


def test_predict_requires_question_or_target_column(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    resp = client.post("/predict", json={"run_id": run_id})
    assert resp.status_code == 422


def test_predict_unknown_run_id_is_404(client):
    resp = client.post("/predict", json={"run_id": "does-not-exist", "target_column": "amount"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Part 7: template/degrade rule - explicit target_column trains even with no
# LLM configured at all.
# ---------------------------------------------------------------------------


def test_predict_without_llm_and_no_target_column_escalates_llm_unavailable(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "question": "what will next month's amount be?"})
    assert body["routed_to"] == "prediction"
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "llm_unavailable"


def test_explicit_target_column_trains_without_any_llm(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "amount"})
    assert body["routed_to"] == "prediction"
    assert body["status"] == "answered", body
    assert body["task_type"] == "regression"


# ---------------------------------------------------------------------------
# Part 1: intent routing - deterministic target-exists check never trusts
# the LLM's own report.
# ---------------------------------------------------------------------------


def test_prediction_intent_naming_nonexistent_target_escalates_despite_high_confidence(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    classification = IntentClassification(intent=IntentKind.PREDICTION, target_column="does_not_exist", confidence=0.99)
    with modeling_llm_override([classification]):
        body = predict_and_wait(client, {"run_id": run_id, "question": "predict the thing that isn't real"})
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "target_not_found"


def test_unanswerable_intent_escalates(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    classification = IntentClassification(intent=IntentKind.UNANSWERABLE, target_column=None, confidence=0.9)
    with modeling_llm_override([classification]):
        body = predict_and_wait(client, {"run_id": run_id, "question": "what is the meaning of life?"})
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "unanswerable"
    assert body["state"] == "awaiting_approval"


def test_retrieval_intent_routes_to_the_query_agent(client, tmp_path):
    """Retrieval-routing is the one predict() outcome that produces a
    QueryRun instead of filling in the placeholder ModelRun (see
    app/routers/predict.py's module docstring) - the placeholder is marked
    "redirected" with a pointer to the real QueryRun rather than left stuck
    at "running". There is no GET-by-id for a bare QueryRun today (Ask's own
    contract has never needed one), so this confirms the redirect pointer
    resolves correctly straight from the database rather than inventing a
    new HTTP surface just for this test."""
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    classification = IntentClassification(intent=IntentKind.RETRIEVAL, target_column=None, confidence=0.9)
    generated = GeneratedQuery(query_kind=QueryKind.PANDAS, code="result = len(df)", columns_referenced=[], assumptions=[], confidence=0.9)
    with modeling_llm_override([classification]), query_llm_override([generated]):
        body = predict_and_wait(client, {"run_id": run_id, "question": "how many rows are there?"})
    assert body["state"] == "redirected"
    assert body["redirected_query_run_id"]

    with SessionLocal() as db:
        query_run = db.get(QueryRun, body["redirected_query_run_id"])
        assert query_run is not None
        assert query_run.result_json["value"] == 150


# ---------------------------------------------------------------------------
# Part 6: below-minimum-rows escalates.
# ---------------------------------------------------------------------------


def test_below_minimum_rows_escalates(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv(n=10))
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "amount"})
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "insufficient_rows"
    assert body["state"] == "awaiting_approval"


def test_unsupported_task_type_escalates_for_high_cardinality_target_with_no_time_axis(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "id"})
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "unsupported_task_type"


# ---------------------------------------------------------------------------
# ACCEPTANCE-style: a full classification path, end to end.
# ---------------------------------------------------------------------------


def test_full_classification_answers_end_to_end(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _classification_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "label"})
    assert body["status"] == "answered", body
    assert body["task_type"] == "classification"
    assert body["split_strategy"] == "stratified_kfold"
    assert body["out_of_sample_metric"] == "f1_weighted"
    assert body["out_of_sample_score"] is not None
    assert len(body["candidate_scores"]) == 2  # every candidate reported, not just the winner
    assert len(body["baseline_scores"]) == 1
    assert any(e["column"] == "id" for e in body["excluded_features"])  # the identifier column, dropped and recorded
    assert body["quality_context_summary"]
    assert body["seed"] is not None


def test_full_regression_answers_end_to_end(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "amount"})
    assert body["status"] == "answered", body
    assert body["task_type"] == "regression"
    assert body["split_strategy"] == "kfold"
    assert body["out_of_sample_metric"] == "rmse"
    assert any(e["column"] == "id" for e in body["excluded_features"])


def test_quality_context_rendered_before_the_numbers(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "amount"})
    assert body["quality_context_summary"]
    assert body["status"] == "answered", body


def test_get_model_by_id(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv())
    body = predict_and_wait(client, {"run_id": run_id, "target_column": "amount"})
    model_id = body["id"]
    get_resp = client.get(f"/models/{model_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == model_id
    assert get_resp.json()["task_type"] == "regression"


def test_get_model_unknown_id_is_404(client):
    assert client.get("/models/does-not-exist").status_code == 404


# ---------------------------------------------------------------------------
# Part 7: reproducibility - identical requests reproduce identical results.
# ---------------------------------------------------------------------------


def test_identical_requests_reproduce_identical_results(client, tmp_path):
    _source_id, run_id = _ingest_csv(client, tmp_path, _classification_csv())
    first = predict_and_wait(client, {"run_id": run_id, "target_column": "label"})
    second = predict_and_wait(client, {"run_id": run_id, "target_column": "label"})

    for key in (
        "model_family", "hyperparameters", "seed", "candidate_scores", "baseline_scores",
        "excluded_features", "split_strategy", "row_count_trained_on", "out_of_sample_metric",
        "out_of_sample_score", "feature_associations", "class_distribution",
    ):
        assert first[key] == second[key], key


# ---------------------------------------------------------------------------
# Part 6: out-of-sample performance only - never a training score.
# ---------------------------------------------------------------------------


def test_out_of_sample_score_is_never_a_training_score():
    df = pd.DataFrame({"feature1": list(range(150)), "target": ["yes" if i % 2 == 0 else "no" for i in range(150)]})
    config = ModelingConfig()
    outcome = evaluate_classification(df, ["feature1"], "target", config)

    # A model fit AND scored on the exact same rows can memorize a
    # unique-per-row feature almost perfectly; the reported score must not
    # show that memorized fit - it is a genuinely held-out, cross-validated
    # score.
    X = df[["feature1"]].to_numpy()
    y = df["target"].to_numpy()
    memorized = RandomForestClassifier(random_state=config.seed, n_estimators=100)
    memorized.fit(X, y)
    training_score = f1_score(y, memorized.predict(X), average="weighted")

    assert training_score > 0.95  # sanity check: the memorization this test relies on actually happens
    assert outcome.out_of_sample_score < training_score - 0.2


# ---------------------------------------------------------------------------
# NO CAUSAL LANGUAGE - field names give a later agent nothing to inherit.
# ---------------------------------------------------------------------------


def test_no_causal_vocabulary_in_output_schema():
    from app.modeling.models import FeatureAssociation, ModelResult

    forbidden = {"cause", "causes", "caused", "causal", "driver", "drivers", "impact", "effect"}
    for model_cls in (ModelResult, FeatureAssociation):
        for field_name in model_cls.model_fields:
            lowered = field_name.lower()
            assert not any(word in lowered for word in forbidden), f"{model_cls.__name__}.{field_name}"
    for column in ModelRun.__table__.columns:
        lowered = column.name.lower()
        assert not any(word in lowered for word in forbidden), f"ModelRun.{column.name}"


# ---------------------------------------------------------------------------
# Part 5/6 gating logic, exercised directly (constructed TrainingOutcome) -
# avoids fighting to engineer a real dataset onto an exact numeric threshold
# while still testing the real gating code.
# ---------------------------------------------------------------------------


def _direct_finish(client, tmp_path, finisher, task_type, split_strategy, training, config=None, target_column="target"):
    _source_id, run_id = _ingest_csv(client, tmp_path, _regression_csv(n=5), filename="tiny.csv")
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        source = db.get(DataSource, run.source_id)
        quality_summary = render_quality_context_summary(_quality_context_for_run(db, run))
        config = config or ModelingConfig()
        return finisher(db, run, source, "manual", quality_summary, target_column, task_type, split_strategy, training, [], config)


def test_severe_class_imbalance_escalates_even_though_it_beats_baseline_and_floor(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.RANDOM_FOREST_CLASSIFIER,
        winner_hyperparameters={"n_estimators": 100, "random_state": 0},
        metric="f1_weighted",
        out_of_sample_score=0.95,
        candidate_scores=[CandidateScore(model_family=ModelFamily.RANDOM_FOREST_CLASSIFIER, metric="f1_weighted", out_of_sample_score=0.95)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.90)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 145, "b": 5}, majority_class_share=145 / 150),
    )
    assert _beats_baseline(training, ModelingConfig())  # confirms this scenario really would otherwise pass
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)
    assert record.escalation_reason == "severe_class_imbalance"
    assert record.state == "awaiting_approval"


def test_below_score_floor_escalates(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LOGISTIC_REGRESSION,
        winner_hyperparameters={},
        metric="f1_weighted",
        out_of_sample_score=0.30,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LOGISTIC_REGRESSION, metric="f1_weighted", out_of_sample_score=0.30)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.20)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 90, "b": 60}, majority_class_share=0.6),
    )
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)
    assert record.escalation_reason == "below_score_floor"
    assert record.state == "awaiting_approval"


def test_no_model_beats_baseline_is_not_reported_as_usable(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LOGISTIC_REGRESSION,
        winner_hyperparameters={},
        metric="f1_weighted",
        out_of_sample_score=0.50,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LOGISTIC_REGRESSION, metric="f1_weighted", out_of_sample_score=0.50)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.50)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 75, "b": 75}, majority_class_share=0.5),
    )
    assert not _beats_baseline(training, ModelingConfig())
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)
    assert record.escalation_reason == "no_model_beats_baseline"
    assert record.state == "awaiting_approval"


def test_regression_beats_baseline_reports_answered(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LINEAR_REGRESSION,
        winner_hyperparameters={},
        metric="rmse",
        out_of_sample_score=5.0,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LINEAR_REGRESSION, metric="rmse", out_of_sample_score=5.0)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MEAN, metric="rmse", out_of_sample_score=10.0)],
        row_count_trained_on=150,
    )
    record = _direct_finish(client, tmp_path, _finish_regression, TaskType.REGRESSION, SplitStrategy.KFOLD, training)
    assert record.state == "answered"
    assert record.escalation_reason is None


def test_forecast_beats_baseline_reports_answered(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.PROPHET,
        winner_hyperparameters={},
        metric="rmse",
        out_of_sample_score=2.0,
        candidate_scores=[CandidateScore(model_family=ModelFamily.PROPHET, metric="rmse", out_of_sample_score=2.0)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.NAIVE, metric="rmse", out_of_sample_score=8.0)],
        row_count_trained_on=30,
    )
    record = _direct_finish(client, tmp_path, _finish_forecast, TaskType.FORECAST, SplitStrategy.FORWARD_CHAINING, training)
    assert record.state == "answered"


def test_forecast_losing_to_baseline_escalates(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.ARIMA,
        winner_hyperparameters={"order": [1, 1, 1]},
        metric="rmse",
        out_of_sample_score=9.0,
        candidate_scores=[CandidateScore(model_family=ModelFamily.ARIMA, metric="rmse", out_of_sample_score=9.0)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.NAIVE, metric="rmse", out_of_sample_score=8.0)],
        row_count_trained_on=30,
    )
    record = _direct_finish(client, tmp_path, _finish_forecast, TaskType.FORECAST, SplitStrategy.FORWARD_CHAINING, training)
    assert record.escalation_reason == "no_model_beats_baseline"


# ---------------------------------------------------------------------------
# Part 6: escalations surface through the EXISTING approvals mechanism.
# ---------------------------------------------------------------------------


def test_escalated_model_appears_in_approvals_pending(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.RANDOM_FOREST_CLASSIFIER,
        winner_hyperparameters={"n_estimators": 100, "random_state": 0},
        metric="f1_weighted",
        out_of_sample_score=0.95,
        candidate_scores=[CandidateScore(model_family=ModelFamily.RANDOM_FOREST_CLASSIFIER, metric="f1_weighted", out_of_sample_score=0.95)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.90)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 145, "b": 5}, majority_class_share=145 / 150),
    )
    _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)

    pending = client.get("/approvals/pending").json()
    assert len(pending["escalated_models"]) == 1
    entry = pending["escalated_models"][0]
    assert entry["escalation_reason"] == "severe_class_imbalance"
    assert entry["approvable"] is True


def test_approve_is_rejected_for_non_approvable_model_reasons(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LOGISTIC_REGRESSION,
        winner_hyperparameters={},
        metric="f1_weighted",
        out_of_sample_score=0.50,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LOGISTIC_REGRESSION, metric="f1_weighted", out_of_sample_score=0.50)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.50)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 75, "b": 75}, majority_class_share=0.5),
    )
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)

    resolve_resp = client.post(f"/approvals/{record.id}/resolve", json={"decision": "approve", "resolved_by": "alice"})
    assert resolve_resp.status_code == 422


def test_approve_accepts_a_below_floor_model_despite_the_caveat(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LOGISTIC_REGRESSION,
        winner_hyperparameters={},
        metric="f1_weighted",
        out_of_sample_score=0.30,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LOGISTIC_REGRESSION, metric="f1_weighted", out_of_sample_score=0.30)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.20)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 90, "b": 60}, majority_class_share=0.6),
    )
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)

    resolve_resp = client.post(f"/approvals/{record.id}/resolve", json={"decision": "approve", "resolved_by": "alice"})
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["state"] == "resolved"

    pending = client.get("/approvals/pending").json()
    assert pending["escalated_models"] == []


def test_reject_fix_dismisses_any_escalated_model(client, tmp_path):
    training = TrainingOutcome(
        winner_family=ModelFamily.LOGISTIC_REGRESSION,
        winner_hyperparameters={},
        metric="f1_weighted",
        out_of_sample_score=0.50,
        candidate_scores=[CandidateScore(model_family=ModelFamily.LOGISTIC_REGRESSION, metric="f1_weighted", out_of_sample_score=0.50)],
        baseline_scores=[BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=0.50)],
        row_count_trained_on=150,
        class_distribution=ClassDistribution(class_counts={"a": 75, "b": 75}, majority_class_share=0.5),
    )
    record = _direct_finish(client, tmp_path, _finish_classification, TaskType.CLASSIFICATION, SplitStrategy.STRATIFIED_KFOLD, training)

    resolve_resp = client.post(f"/approvals/{record.id}/resolve", json={"decision": "reject_fix", "resolved_by": "alice"})
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["decision"] == "reject_fix"

    pending = client.get("/approvals/pending").json()
    assert pending["escalated_models"] == []
