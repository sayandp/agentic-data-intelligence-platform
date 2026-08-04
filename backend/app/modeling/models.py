"""Part 1/2/3/5/6/7: the Modeling Agent's structural contracts.

The LLM's ONLY job in this phase is classifying a question's intent
(retrieval/prediction/unanswerable) and, for prediction, naming the target
column it thinks the question is about - never which model, which
features, which split, or which metric. Every one of those is chosen
deterministically, from data shape alone, by app/modeling/task_selection.py,
app/modeling/leakage.py, app/modeling/splitting.py, and
app/modeling/automl.py. A target_column the LLM names is never trusted on
its own - app/modeling/pipeline.py confirms it names a real, usable column
in the live schema before anything downstream runs (same pattern as Phase
6's unknown-column check).

NO CAUSAL LANGUAGE ANYWHERE IN THIS MODULE, same rule as
app/exploration/findings.py: feature "importance" is association, not
cause, and neither the field names nor the enum values give a later agent
any causal vocabulary to inherit ("association_strength", never
"importance" or "impact" or "driver").
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IntentKind(str, Enum):
    RETRIEVAL = "retrieval"
    PREDICTION = "prediction"
    UNANSWERABLE = "unanswerable"


class IntentClassification(BaseModel):
    """The Modeling Agent's LLMClient.complete() response_schema - Part 1's
    single LLM call. target_column is only meaningful when
    intent == PREDICTION, and is never trusted until it is confirmed to name
    a real, usable-dtype column in the live schema."""

    intent: IntentKind
    target_column: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class IntentOutcome(BaseModel):
    """Mirrors app/query/models.py::GenerationOutcome's shape."""

    model_config = {"arbitrary_types_allowed": True}

    classification: IntentClassification | None
    source: Literal["llm", "cache", "escalated_parse_failure", "escalated_quota_exhausted", "escalated_unavailable"]
    model_name: str | None = None
    temperature: float | None = None


class TaskType(str, Enum):
    FORECAST = "forecast"
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    UNSUPPORTED = "unsupported"


class ModelFamily(str, Enum):
    # forecast
    PROPHET = "prophet"
    ARIMA = "arima"
    # classification
    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST_CLASSIFIER = "random_forest_classifier"
    # regression
    LINEAR_REGRESSION = "linear_regression"
    RANDOM_FOREST_REGRESSOR = "random_forest_regressor"
    # trivial baselines - a real ModelFamily value in candidate/baseline
    # score records, never conflated with a "trained" family
    NAIVE = "naive"
    SEASONAL_NAIVE = "seasonal_naive"
    MAJORITY_CLASS = "majority_class"
    MEAN = "mean"


class SplitStrategy(str, Enum):
    FORWARD_CHAINING = "forward_chaining"  # TimeSeriesSplit - forecast only, structurally no shuffle option
    STRATIFIED_KFOLD = "stratified_kfold"  # classification only
    KFOLD = "kfold"  # regression only


class AggregationMethod(str, Enum):
    SUM = "sum"
    COUNT = "count"


class EscalationReason(str, Enum):
    """Closed vocabulary for why a model was NOT reported - mirrors
    app/query/models.py::EscalationReason's reasoning exactly: the reason
    recorded is always exactly one of the documented triggers, never a
    free-text explanation standing in for one."""

    LLM_UNAVAILABLE = "llm_unavailable"
    UNANSWERABLE = "unanswerable"
    TARGET_NOT_FOUND = "target_not_found"
    UNSUPPORTED_TASK_TYPE = "unsupported_task_type"
    INSUFFICIENT_ROWS = "insufficient_rows"
    NO_MODEL_BEATS_BASELINE = "no_model_beats_baseline"
    BELOW_SCORE_FLOOR = "below_score_floor"
    SEVERE_CLASS_IMBALANCE = "severe_class_imbalance"


# Reasons that leave behind a genuinely trained, scored model - one a human
# might reasonably decide to accept anyway despite the caveat - mirrors
# app/query/models.py::APPROVABLE_ESCALATION_REASONS. Every other reason has
# no model behind it at all: nothing to approve, only to acknowledge.
APPROVABLE_ESCALATION_REASONS = frozenset({EscalationReason.BELOW_SCORE_FLOOR, EscalationReason.SEVERE_CLASS_IMBALANCE})


class ModelAnswerStatus(str, Enum):
    ANSWERED = "answered"
    ESCALATED = "escalated"


class ExcludedFeature(BaseModel):
    """Part 3 - one row per feature dropped before training, and why. A
    record, not a metric: this is what makes leakage prevention auditable
    rather than merely claimed."""

    column: str
    reason: str


class CandidateScore(BaseModel):
    """Part 2 - one row per candidate model family actually fit and scored,
    winner or not; the comparison table is itself a report artifact.
    out_of_sample_score is ALWAYS a held-out score, never a training score
    (Part 6)."""

    model_family: ModelFamily
    metric: str
    out_of_sample_score: float
    hyperparameters: dict = Field(default_factory=dict)


class BaselineScore(BaseModel):
    """Part 5 - the trivial baseline(s) every candidate is measured
    against, always reported alongside the model's whether it wins or not."""

    model_family: ModelFamily
    metric: str
    out_of_sample_score: float


class ClassDistribution(BaseModel):
    """Part 6 - reported whenever class imbalance is a concern, instead of
    a high F1 presented without this context."""

    class_counts: dict[str, int]
    majority_class_share: float


class PredictionInterval(BaseModel):
    """Model-family-supplied uncertainty measure - Part 6's 'a prediction
    interval or equivalent uncertainty measure where the model family
    supports one'. Null bounds mean the fitted family does not supply one."""

    lower: float | None = None
    upper: float | None = None
    confidence_level: float | None = None


class FeatureAssociation(BaseModel):
    """Part 6 - association, never cause; `association_strength` is
    deliberately not named "importance" or "impact" or "driver"."""

    column: str
    association_strength: float


class ForecastPoint(BaseModel):
    """One point on the Predict page's forecast chart (frontend
    PredictPage.tsx) - `lower`/`upper` are null for a historical actual
    (there is no interval on an observed value) and for any forecast point
    from a family that supplies no uncertainty measure."""

    date: str
    value: float
    lower: float | None = None
    upper: float | None = None


class ForecastSeries(BaseModel):
    """Chart-ready data for a FORECAST task only, computed deterministically
    alongside training - same rule app/narrative/charts.py's chart selection
    already follows: the LLM never sees or picks this, the shape is decided
    by the data/model output alone."""

    historical: list[ForecastPoint] = Field(default_factory=list)
    forecast: list[ForecastPoint] = Field(default_factory=list)


class ModelResult(BaseModel):
    """The full assembled response - what POST /predict returns, and what
    app/models.py::ModelRun persists. quality_context_summary is always
    populated and, per Phases 5/6/7, rendered before the numbers."""

    status: ModelAnswerStatus
    quality_context_summary: str
    question: str | None = None
    target_column: str | None = None
    task_type: TaskType | None = None
    model_family: ModelFamily | None = None
    hyperparameters: dict = Field(default_factory=dict)
    seed: int | None = None
    candidate_scores: list[CandidateScore] = Field(default_factory=list)
    baseline_scores: list[BaselineScore] = Field(default_factory=list)
    excluded_features: list[ExcludedFeature] = Field(default_factory=list)
    split_strategy: SplitStrategy | None = None
    row_count_trained_on: int | None = None
    class_distribution: ClassDistribution | None = None
    out_of_sample_metric: str | None = None
    out_of_sample_score: float | None = None
    prediction_interval: PredictionInterval | None = None
    feature_associations: list[FeatureAssociation] = Field(default_factory=list)
    forecast_series: ForecastSeries | None = None
    escalation_reason: EscalationReason | None = None
    escalation_detail: str | None = None
