"""The Exploration Agent's output contract - the second major interface in
this project after DataContract. The Narrative and Query agents (later
phases) consume this directly, so it gets the same treatment: closed enums
throughout, no field a caller has to defensively re-validate.

NO CAUSAL LANGUAGE ANYWHERE IN THIS MODULE. Field names, enum values, and
payload keys describe statistical relationships only - "correlation", never
"driver" or "impact" or "effect" or "cause". This is deliberate and the only
reliable defence against the Narrative Agent (a later phase, built on top of
an LLM) phrasing a correlation causally: it can only inherit vocabulary this
schema actually contains. If a value here reads like a sentence meant for a
human, it doesn't belong in this module - that's the Narrative Agent's job,
not the Exploration Agent's.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.column_kind import ColumnKind  # noqa: F401 - re-exported: every existing `from app.exploration.findings import ColumnKind` still resolves to the same class object

# Bump whenever a change here is not backward-compatible for a consumer
# reading findings_json off an old row (field removed/renamed, enum value
# removed, semantics of an existing field changed). Additive, optional
# fields do not require a bump.
CURRENT_SCHEMA_VERSION = 1


class FindingType(str, Enum):
    SUMMARY_STAT = "summary_stat"
    CORRELATION = "correlation"
    OUTLIER_CLUSTER = "outlier_cluster"
    TREND = "trend"
    DISTRIBUTION_SHAPE = "distribution_shape"
    CARDINALITY_NOTE = "cardinality_note"
    MISSING_PATTERN = "missing_pattern"


# ---------------------------------------------------------------------------
# summary_stat payload - shape depends on the column's kind, discriminated
# on `kind` rather than left to duck-typing. ColumnKind itself now lives in
# app/column_kind.py (Phase 7.5) - shared with app/profiling.py and
# app/modeling/task_selection.py so all three can never disagree about the
# same column's kind - and is re-exported here unchanged.
# ---------------------------------------------------------------------------


class CategoryFrequency(BaseModel):
    value: str
    count: int


class NumericSummaryPayload(BaseModel):
    finding_kind: Literal["summary_stat"] = "summary_stat"
    kind: Literal[ColumnKind.NUMERIC] = ColumnKind.NUMERIC
    count: int
    null_count: int
    null_rate: float
    dtype: str
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    q1: float | None = None
    q3: float | None = None
    skew: float | None = None


class CategoricalSummaryPayload(BaseModel):
    finding_kind: Literal["summary_stat"] = "summary_stat"
    kind: Literal[ColumnKind.CATEGORICAL] = ColumnKind.CATEGORICAL
    count: int
    null_count: int
    null_rate: float
    dtype: str
    cardinality: int
    mode: str | None = None
    top_frequencies: list[CategoryFrequency] = Field(default_factory=list)


class DatetimeSummaryPayload(BaseModel):
    finding_kind: Literal["summary_stat"] = "summary_stat"
    kind: Literal[ColumnKind.DATETIME] = ColumnKind.DATETIME
    count: int
    null_count: int
    null_rate: float
    dtype: str
    min: str | None = None  # ISO 8601
    max: str | None = None  # ISO 8601
    span_days: float | None = None
    inferred_frequency: str | None = None  # a pandas freq alias, None if irregular


SummaryStatPayload = Annotated[
    Union[NumericSummaryPayload, CategoricalSummaryPayload, DatetimeSummaryPayload],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# correlation payload
# ---------------------------------------------------------------------------


class CorrelationMethod(str, Enum):
    PEARSON = "pearson"
    SPEARMAN = "spearman"


class CorrelationPayload(BaseModel):
    finding_kind: Literal["correlation"] = "correlation"
    column_a: str
    column_b: str
    method: CorrelationMethod
    coefficient: float


# ---------------------------------------------------------------------------
# outlier_cluster payload
# ---------------------------------------------------------------------------


class OutlierMethod(str, Enum):
    IQR = "iqr"


class OutlierClusterPayload(BaseModel):
    finding_kind: Literal["outlier_cluster"] = "outlier_cluster"
    column: str
    method: OutlierMethod = OutlierMethod.IQR
    lower_bound: float
    upper_bound: float
    count: int
    example_indices: list[str] = Field(default_factory=list)  # capped, never a full per-row dump


# ---------------------------------------------------------------------------
# trend payload
# ---------------------------------------------------------------------------


class TrendDirection(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"


class TrendPayload(BaseModel):
    finding_kind: Literal["trend"] = "trend"
    numeric_column: str
    datetime_column: str
    slope: float
    direction: TrendDirection
    start: str  # ISO 8601
    end: str  # ISO 8601
    inferred_frequency: str | None = None
    # Seasonality is flagged, never folded into direction/slope - a later
    # agent must not be able to present a seasonal cycle as a monotonic
    # trend just because a TrendPayload exists for the column.
    seasonality_detected: bool = False
    seasonality_period: int | None = None  # lag count at which autocorrelation peaked


# ---------------------------------------------------------------------------
# distribution_shape payload
# ---------------------------------------------------------------------------


class NormalityIndication(str, Enum):
    LIKELY_NORMAL = "likely_normal"
    LIKELY_NON_NORMAL = "likely_non_normal"
    INSUFFICIENT_DATA = "insufficient_data"


class ModalityHint(str, Enum):
    UNIMODAL = "unimodal"
    MULTIMODAL_SUSPECTED = "multimodal_suspected"
    INSUFFICIENT_DATA = "insufficient_data"


class DistributionShapePayload(BaseModel):
    finding_kind: Literal["distribution_shape"] = "distribution_shape"
    column: str
    skew: float | None = None
    normality_indication: NormalityIndication
    modality_hint: ModalityHint


# ---------------------------------------------------------------------------
# cardinality_note payload
# ---------------------------------------------------------------------------


class CardinalityNoteKind(str, Enum):
    HIGH_CARDINALITY = "high_cardinality"
    NEAR_UNIQUE = "near_unique"


class CardinalityNotePayload(BaseModel):
    finding_kind: Literal["cardinality_note"] = "cardinality_note"
    column: str
    cardinality: int
    row_count: int
    unique_ratio: float
    note: CardinalityNoteKind


# ---------------------------------------------------------------------------
# missing_pattern payload
# ---------------------------------------------------------------------------


class MissingPatternRelationship(str, Enum):
    # Both directions hold: null(A) <=> null(B) above the configured threshold.
    CO_OCCURRING = "co_occurring"
    # One direction only: null(narrower_column) implies null(broader_column)
    # above the threshold, but not the reverse.
    ONE_DIRECTIONAL = "one_directional"


class MissingPatternPayload(BaseModel):
    finding_kind: Literal["missing_pattern"] = "missing_pattern"
    columns: list[str]
    relationship: MissingPatternRelationship
    # Set only when relationship == one_directional: the column whose nulls
    # are (near-)fully contained within the other's.
    narrower_column: str | None = None
    broader_column: str | None = None
    support: int  # row count both columns are null on
    confidence: float  # conditional null-rate that established the pattern


FindingPayload = Annotated[
    Union[
        SummaryStatPayload,
        CorrelationPayload,
        OutlierClusterPayload,
        TrendPayload,
        DistributionShapePayload,
        CardinalityNotePayload,
        MissingPatternPayload,
    ],
    Field(discriminator="finding_kind"),
]


# ---------------------------------------------------------------------------
# Evidence, Finding, ExplorationFindings
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """What this finding was actually computed on. Every finding carries
    this - a number with no stated sample size or significance is not
    something a later agent can safely present as fact."""

    sample_size: int
    p_value: float | None = None
    r_squared: float | None = None
    confidence_interval: tuple[float, float] | None = None
    # Set only when the row cap (Part 3) triggered seeded sampling for the
    # analysis this finding came from - reproducibility requires the seed,
    # not just the fact that sampling happened.
    sampling_seed: int | None = None


class Finding(BaseModel):
    # Assigned by ExplorationEngine.run() after every analysis has run, as
    # f"{finding_type}-{position}" in the final (fully deterministic) list
    # order - never by the analysis module that builds the Finding, since no
    # single module knows its own offset into the whole run's output. Empty
    # only in the instant between an analysis module constructing a Finding
    # and the engine's id-assignment pass; never empty on anything that
    # leaves ExplorationEngine.run(). The Narrative Agent (Phase 5) is the
    # reason this exists at all - every GroundedClaim cites one or more of
    # these ids, and that citation has to be a stable, real reference.
    id: str = ""
    finding_type: FindingType
    columns: list[str]
    payload: FindingPayload
    evidence: Evidence


class SkippedEntry(BaseModel):
    """Something the engine deliberately did not compute or report, and why.
    `column` holds a single column name, or a comma-joined pair/group for a
    skip that spans more than one column (e.g. a suppressed correlation)."""

    column: str
    reason: str


class ResolutionKind(str, Enum):
    """Every action_taken value ValidationEvent can actually hold once
    resolved (app/state_machine.py, app/routers/ingest.py,
    app/routers/approvals.py) - reusing those exact strings rather than
    inventing a second taxonomy that would need to be kept in sync by hand."""

    AUTO_FIXED = "auto_fixed"
    AUTO_FIX_REVERTED = "auto_fix_reverted"
    APPROVED_AND_APPLIED = "approved_and_applied"
    APPROVED_FIX_FAILED_VERIFICATION = "approved_fix_failed_verification"
    REJECTED_FIX_DATA_ACCEPTABLE = "rejected_fix_data_acceptable"
    REJECTED_DATA_UNFIT = "rejected_data_unfit"
    ACCEPTED_AS_NEW_BASELINE = "accepted_as_new_baseline"


class DataQualityContext(BaseModel):
    """Carried, not interpreted. Exploration answers "what does this data
    say"; validation answers "can this data be trusted" - this is the seam
    between the two, so a finding computed on data with unresolved-at-fix-
    time issues isn't later presented as unqualified fact. Never re-derives
    or re-reports the validation findings themselves (nulls, drift - those
    stay in validation_events, not here)."""

    total_events: int
    resolution_counts: dict[ResolutionKind, int] = Field(default_factory=dict)
    active_baseline_provisional: bool = False


class ExplorationFindings(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    findings: list[Finding] = Field(default_factory=list)
    skipped: list[SkippedEntry] = Field(default_factory=list)
    data_quality_context: DataQualityContext
