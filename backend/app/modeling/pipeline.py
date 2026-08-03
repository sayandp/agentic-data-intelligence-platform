"""Part 1/6/7: orchestrates the Modeling Agent end to end - resolve the
run, classify intent (or accept an explicit target_column and skip the LLM
entirely - Part 7's template/degrade rule), route retrieval questions to
the Phase 6 Query Agent, deterministically select task type and features,
train, gate against the baseline, and persist a ModelRun. Every path
through predict() ends in a ModelRun (or, for a retrieval-routed question,
a QueryRun) row - never an exception and never a second training attempt
against the same request with a hint about why the first one didn't
qualify.

Check order matters, same reasoning as app/query/pipeline.py: the baseline
gate and score-floor/imbalance checks always run AFTER training completes,
never before, because those are the only escalation reasons with a real
model behind them a human can later approve (app/routers/approvals.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.factory import build_connector
from app.exploration.findings import ExplorationFindings
from app.modeling.agent import ModelingAgent
from app.modeling.automl import TrainingOutcome
from app.modeling.config import ModelingConfig
from app.modeling.models import EscalationReason, ModelAnswerStatus
from app.models import Baseline, DataSource, ExplorationFinding, ModelRun, QueryRun, Run
from app.narrative.quality import render_quality_context_summary
from app.query.agent import QueryAgent
from app.query.pipeline import _quality_context_for_run, ask_question, resolve_run
from app.repair import repaired_contract_for_run
from app.state_machine import AWAITING_APPROVAL, REJECTED, RESOLVED, transition


def _load_findings(db: Session, run: Run) -> ExplorationFindings | None:
    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    if record is None:
        return None
    return ExplorationFindings.model_validate(record.findings_json)


def _persist(
    db: Session,
    run: Run,
    source: DataSource,
    question: str | None,
    quality_summary: str,
    *,
    status: ModelAnswerStatus,
    target_column: str | None = None,
    task_type=None,
    outcome: TrainingOutcome | None = None,
    split_strategy=None,
    escalation_reason: str | None = None,
    escalation_detail: str | None = None,
    awaiting_approval: bool = False,
    seed: int | None = None,
    existing: ModelRun | None = None,
) -> ModelRun:
    """`existing`: update THIS row in place rather than creating a new one -
    the RESOLVE-HANG-style fix (dashboard UX pass Part 1) creates a
    placeholder ModelRun synchronously so POST /predict can return its id
    immediately, before training ever runs (app/routers/predict.py); every
    terminal path through the predict graph (app/graph/predict_graph.py)
    must finish writing to THAT SAME row, never a second one the caller's
    id wouldn't match."""
    record = existing if existing is not None else ModelRun(source_id=source.id, run_id=run.id)
    record.question = question
    record.target_column = target_column
    record.task_type = task_type.value if task_type is not None else None
    record.model_family = outcome.winner_family.value if outcome is not None else None
    record.hyperparameters_json = outcome.winner_hyperparameters if outcome is not None else None
    record.seed = seed
    record.candidate_scores_json = [c.model_dump(mode="json") for c in outcome.candidate_scores] if outcome is not None else None
    record.baseline_scores_json = [b.model_dump(mode="json") for b in outcome.baseline_scores] if outcome is not None else None
    record.excluded_features_json = None
    record.split_strategy = split_strategy.value if split_strategy is not None else None
    record.row_count_trained_on = outcome.row_count_trained_on if outcome is not None else None
    record.class_distribution_json = (
        outcome.class_distribution.model_dump(mode="json") if outcome is not None and outcome.class_distribution else None
    )
    record.out_of_sample_metric = outcome.metric if outcome is not None else None
    record.out_of_sample_score = outcome.out_of_sample_score if outcome is not None else None
    record.prediction_interval_json = (
        outcome.prediction_interval.model_dump(mode="json") if outcome is not None and outcome.prediction_interval else None
    )
    record.feature_associations_json = [f.model_dump(mode="json") for f in outcome.feature_associations] if outcome is not None else None
    record.forecast_series_json = outcome.forecast_series.model_dump(mode="json") if outcome is not None and outcome.forecast_series else None
    record.state = AWAITING_APPROVAL if awaiting_approval else ("answered" if status == ModelAnswerStatus.ANSWERED else REJECTED)
    record.escalation_reason = escalation_reason
    record.escalation_detail = escalation_detail
    record.quality_context_summary = quality_summary
    if existing is None:
        db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _persist_with_excluded(
    db: Session,
    run: Run,
    source: DataSource,
    question: str | None,
    quality_summary: str,
    *,
    status: ModelAnswerStatus,
    target_column: str,
    task_type,
    outcome: TrainingOutcome | None,
    split_strategy,
    excluded_features: list,
    escalation_reason: str | None = None,
    escalation_detail: str | None = None,
    awaiting_approval: bool = False,
    seed: int | None = None,
    existing: ModelRun | None = None,
) -> ModelRun:
    record = _persist(
        db, run, source, question, quality_summary,
        status=status, target_column=target_column, task_type=task_type, outcome=outcome, split_strategy=split_strategy,
        escalation_reason=escalation_reason, escalation_detail=escalation_detail, awaiting_approval=awaiting_approval, seed=seed,
        existing=existing,
    )
    record.excluded_features_json = [e.model_dump(mode="json") for e in excluded_features]
    db.commit()
    db.refresh(record)
    return record


def predict(
    db: Session,
    modeling_agent: ModelingAgent | None,
    query_agent: QueryAgent | None,
    source_id: str | None = None,
    run_id: str | None = None,
    question: str | None = None,
    target_column: str | None = None,
    config: ModelingConfig | None = None,
    existing_model_run: ModelRun | None = None,
) -> ModelRun | QueryRun:
    """The single entry point app/routers/predict.py calls. Raises
    ValueError only for a bad source_id/run_id (the router turns that into
    a 404) - every LLM/leakage/split/baseline failure past that point is
    captured as an escalated ModelRun, never an exception. Returns a
    QueryRun instead of a ModelRun when intent routing determines the
    question is retrieval, not prediction (Part 1) - delegated to the
    existing Phase 6 Query Agent rather than rebuilding that path here.

    `existing_model_run`: the RESOLVE-HANG-style fix (dashboard UX pass
    Part 1) - app/routers/predict.py creates this placeholder row
    synchronously and returns its id immediately, before this (potentially
    slow: CV folds fitting several model families) function ever runs, via
    BackgroundTasks. Every terminal path through the predict graph updates
    THIS SAME row in place (threaded through as initial graph state) rather
    than creating a second one the caller's id wouldn't match - see
    app/modeling/pipeline.py::_persist. Retrieval-routing is the one path
    that produces a QueryRun instead: the caller (background task wrapper)
    is responsible for marking the now-orphaned placeholder appropriately.

    resolve_run/contract-building below is unconditional setup, not a
    decision point, so it stays here rather than becoming a graph node -
    everything that actually BRANCHES (classify -> train -> gate, or an
    early escalation/retrieval-routing at any of those points) lives in
    app/graph/predict_graph.py (Phase 8 Part 1)."""
    from app.graph.predict_graph import get_predict_graph

    config = config or ModelingConfig()
    run = resolve_run(db, source_id, run_id)
    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    connector = build_connector(source)
    contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
    quality_summary = render_quality_context_summary(_quality_context_for_run(db, run))
    schema = contract.column_types

    final_state = get_predict_graph().invoke(
        {
            "db": db,
            "modeling_agent": modeling_agent,
            "run": run,
            "source": source,
            "question": question,
            "target_column": target_column,
            "config": config,
            "quality_summary": quality_summary,
            "schema": schema,
            "contract": contract,
            "existing_model_run": existing_model_run,
        }
    )
    if final_state.get("is_retrieval"):
        return ask_question(db, query_agent, source_id=source_id, run_id=run_id, question=question or "")
    return final_state["result"]


def _beats_baseline(training: TrainingOutcome, config: ModelingConfig) -> bool:
    if not training.baseline_scores:
        return True
    if training.metric == "f1_weighted":
        best_baseline = max(b.out_of_sample_score for b in training.baseline_scores)
        return training.out_of_sample_score > best_baseline * (1 + config.baseline_margin)
    # rmse (and any other lower-is-better metric)
    best_baseline = min(b.out_of_sample_score for b in training.baseline_scores)
    return training.out_of_sample_score < best_baseline * (1 - config.baseline_margin)


def _finish_forecast(db, run, source, question, quality_summary, target_column, task_type, split_strategy, training, excluded, config, existing=None):
    if not _beats_baseline(training, config):
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.NO_MODEL_BEATS_BASELINE.value,
            escalation_detail="no candidate model outperformed its baseline by the required margin",
            awaiting_approval=True, existing=existing,
        )
    return _persist_with_excluded(
        db, run, source, question, quality_summary,
        status=ModelAnswerStatus.ANSWERED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
        excluded_features=excluded, seed=config.seed, existing=existing,
    )


def _finish_regression(db, run, source, question, quality_summary, target_column, task_type, split_strategy, training, excluded, config, existing=None):
    if not _beats_baseline(training, config):
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.NO_MODEL_BEATS_BASELINE.value,
            escalation_detail="no candidate model outperformed its baseline by the required margin",
            awaiting_approval=True, existing=existing,
        )
    if config.score_floor_regression is not None and training.out_of_sample_score > config.score_floor_regression:
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.BELOW_SCORE_FLOOR.value,
            escalation_detail=f"out-of-sample rmse {training.out_of_sample_score:.4f} exceeds the configured floor {config.score_floor_regression}",
            awaiting_approval=True, existing=existing,
        )
    return _persist_with_excluded(
        db, run, source, question, quality_summary,
        status=ModelAnswerStatus.ANSWERED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
        excluded_features=excluded, seed=config.seed, existing=existing,
    )


def _finish_classification(db, run, source, question, quality_summary, target_column, task_type, split_strategy, training, excluded, config, existing=None):
    # Severe imbalance is checked FIRST and takes priority over the score-
    # based gates: a high F1 on a severely imbalanced target is exactly the
    # misleading number Part 6 requires reporting distribution over,
    # regardless of whether it happens to also clear the other gates.
    if training.class_distribution is not None and training.class_distribution.majority_class_share > config.class_imbalance_majority_share:
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.SEVERE_CLASS_IMBALANCE.value,
            escalation_detail=(
                f"majority class share {training.class_distribution.majority_class_share:.1%} exceeds "
                f"{config.class_imbalance_majority_share:.1%} - f1 is not a reliable signal on this distribution"
            ),
            awaiting_approval=True, existing=existing,
        )
    if not _beats_baseline(training, config):
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.NO_MODEL_BEATS_BASELINE.value,
            escalation_detail="no candidate model outperformed its baseline by the required margin",
            awaiting_approval=True, existing=existing,
        )
    if config.score_floor_classification is not None and training.out_of_sample_score < config.score_floor_classification:
        return _persist_with_excluded(
            db, run, source, question, quality_summary,
            status=ModelAnswerStatus.ESCALATED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
            excluded_features=excluded, seed=config.seed,
            escalation_reason=EscalationReason.BELOW_SCORE_FLOOR.value,
            escalation_detail=f"out-of-sample f1 {training.out_of_sample_score:.4f} is below the configured floor {config.score_floor_classification}",
            awaiting_approval=True, existing=existing,
        )
    return _persist_with_excluded(
        db, run, source, question, quality_summary,
        status=ModelAnswerStatus.ANSWERED, target_column=target_column, task_type=task_type, outcome=training, split_strategy=split_strategy,
        excluded_features=excluded, seed=config.seed, existing=existing,
    )


def resolve_escalated_model_approval(db: Session, model_run: ModelRun, resolved_by: str) -> ModelRun:
    """The `approve` decision for an escalated ModelRun (app/routers/
    approvals.py) - restricted by the caller to
    app/modeling/models.py::APPROVABLE_ESCALATION_REASONS. Unlike Query's
    approval path, there is no code artifact to re-validate/re-execute: the
    training already ran, its full result is already persisted, and
    approving simply accepts that already-computed result despite the
    caveat - the model is not silently retrained on approval."""
    transition(model_run.state, RESOLVED)
    model_run.state = RESOLVED
    model_run.resolved_by = resolved_by
    model_run.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(model_run)
    return model_run


def reject_escalated_model(db: Session, model_run: ModelRun, resolved_by: str) -> ModelRun:
    """The `reject_fix` decision - acknowledges the escalation without
    accepting the model. Valid for every escalation reason."""
    transition(model_run.state, REJECTED)
    model_run.state = REJECTED
    model_run.resolved_by = resolved_by
    model_run.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(model_run)
    return model_run
