"""The Business Analytics Agent's output contract.

Same shape family as app/exploration/findings.py on purpose: closed enums,
an evidence block on every finding, stable ids the Narrative Agent can cite.
A GroundedClaim must be able to reference one of these exactly as it
references an exploration finding, with no special-casing at the grounding
layer.

NO CAUSAL LANGUAGE ANYWHERE IN THIS MODULE - not in field names, not in enum
values, not in payload keys. A segment CONTRIBUTES a share of value; it does
not "drive" it. A cohort's retention DECLINES; nothing "causes" it to. This
is the same defence exploration uses: the Narrative Agent can only inherit
vocabulary this schema actually contains, so causal phrasing is prevented
structurally rather than asked for politely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# Bump when a change here is not backward-compatible for a reader of an old
# business_analyses row. Additive optional fields do not require a bump.
CURRENT_SCHEMA_VERSION = 1


class AnalysisFindingType(str, Enum):
    CONCENTRATION_BAND = "concentration_band"    # ABC/Pareto band membership + share
    CONCENTRATION_CURVE = "concentration_curve"  # the cumulative contribution curve
    SEGMENT_PROFILE = "segment_profile"          # RFM named segment, or a cluster
    RETENTION_MATRIX = "retention_matrix"        # cohort triangle
    REPEAT_BEHAVIOUR = "repeat_behaviour"        # repeat rate, gaps, inactivity flag
    ASSOCIATION_RULE = "association_rule"        # basket rule with support/confidence/lift
    LIFETIME_VALUE = "lifetime_value"            # historical, descriptive CLV
    NON_CONTRIBUTING_ENTITIES = "non_contributing_entities"  # net <= 0 over the period


class AnalysisEvidence(BaseModel):
    """What the finding was computed on. A share with no denominator is not
    something a later agent can safely present as fact."""

    sample_size: int                    # rows the analysis consumed
    entity_count: int | None = None     # distinct grouped entities, when grouped
    total_value: float | None = None    # denominator behind any share
    #: Everything needed to reproduce the number exactly.
    parameters: dict = Field(default_factory=dict)


class ConcentrationBandPayload(BaseModel):
    """One ABC band. `value_share` is a share OF THE TOTAL, never a claim
    about what produced it."""

    finding_type: Literal[AnalysisFindingType.CONCENTRATION_BAND] = AnalysisFindingType.CONCENTRATION_BAND
    band: str                       # "A" | "B" | "C"
    entity_count: int
    entity_share: float             # fraction of entities in this band
    value_total: float
    value_share: float              # fraction of total value contributed
    cumulative_value_share: float   # cumulative through the end of this band
    top_entities: list[str] = Field(default_factory=list)


class ConcentrationCurvePayload(BaseModel):
    """The Pareto curve itself, already sorted descending by value.

    Capped: a curve over 50k entities is not a chart anyone reads, and the
    cap is recorded in evidence.parameters so a reader knows it happened.
    """

    finding_type: Literal[AnalysisFindingType.CONCENTRATION_CURVE] = AnalysisFindingType.CONCENTRATION_CURVE
    entity_rank: list[int]
    cumulative_entity_share: list[float]
    cumulative_value_share: list[float]
    #: The classic headline: what share of value the top 20% contribute.
    top_20_percent_value_share: float
    #: How many entities that share actually covers, and what fraction of
    #: all entities they are. On a small table the top 20% cannot land on a
    #: whole entity, so the real denominator is stated rather than implied
    #: by the field name - "top 20%" over 5 entities is really the top 1.
    top_20_percent_entity_count: int | None = None
    top_20_percent_entity_share: float | None = None
    #: The floor `top_20_percent_value_share` was tested against.
    concentration_floor: float | None = None
    #: True when the share falls BELOW the floor - the premise this method
    #: is named for does not hold on this data, so the A/B/C split is a
    #: weaker statement than the name suggests. None (not False) on a row
    #: written before this check existed: unknown, never a quiet "no".
    concentration_is_weak: bool | None = None
    #: The sentence a reader sees. Always states the actual figure, whether
    #: or not the floor was cleared, so the shape is read rather than
    #: inferred from the absence of a warning.
    concentration_note: str | None = None


class SegmentProfilePayload(BaseModel):
    """One named RFM segment or one k-means cluster.

    `value_share` is a share OF THE TOTAL - what this segment ACCOUNTS FOR.
    The schema carries no vocabulary for any stronger relationship, and the
    module docstring explains why.

    (Worded without naming the forbidden verbs on purpose: Pydantic copies a
    class docstring into the JSON schema's `description`, so a docstring
    that spelled them out would put them into the very artefact the ban
    covers - and would trip the test that enforces it.)
    """

    finding_type: Literal[AnalysisFindingType.SEGMENT_PROFILE] = AnalysisFindingType.SEGMENT_PROFILE
    segment: str
    #: "rfm_rule" when the segment came from the documented rule table,
    #: "kmeans" when it came from clustering. Never blended.
    method: str
    entity_count: int
    entity_share: float
    value_total: float
    value_share: float
    #: Mean of each feature within the segment, in the feature's own units.
    centre: dict[str, float] = Field(default_factory=dict)
    #: Populated for kmeans only.
    silhouette: float | None = None


class RetentionMatrixPayload(BaseModel):
    """The cohort triangle. `retained_share[i][j]` is the share of cohort i
    still active j periods after acquisition; index 0 is always 1.0."""

    finding_type: Literal[AnalysisFindingType.RETENTION_MATRIX] = AnalysisFindingType.RETENTION_MATRIX
    granularity: str                      # "month" | "week" | "day"
    cohort_labels: list[str]
    cohort_sizes: list[int]
    periods_since_acquisition: list[int]
    retained_share: list[list[float | None]]
    #: Mean across cohorts at each offset - the headline retention curve.
    mean_retained_share: list[float | None]


class RepeatBehaviourPayload(BaseModel):
    """Repeat-purchase behaviour and an inactivity flag.

    The inactivity window is NEVER inferred. It is supplied or defaulted,
    and always stated in `inactivity_window_days` so a reader knows exactly
    what "inactive" meant here.
    """

    finding_type: Literal[AnalysisFindingType.REPEAT_BEHAVIOUR] = AnalysisFindingType.REPEAT_BEHAVIOUR
    entity_count: int
    repeat_entity_count: int
    repeat_rate: float
    inactivity_window_days: int
    inactive_entity_count: int
    inactive_share: float
    #: Days between consecutive events, across all entities with >= 2 events.
    gap_days_median: float | None = None
    gap_days_p25: float | None = None
    gap_days_p75: float | None = None
    observation_end: str | None = None


class AssociationRulePayload(BaseModel):
    """One basket rule. Lift > 1 means the pair co-occurs MORE OFTEN than
    independence would give - an association, never a claim that buying the
    antecedent brings about the consequent."""

    finding_type: Literal[AnalysisFindingType.ASSOCIATION_RULE] = AnalysisFindingType.ASSOCIATION_RULE
    antecedent: list[str]
    consequent: list[str]
    support: float
    confidence: float
    lift: float
    transaction_count: int


class LifetimeValuePayload(BaseModel):
    """HISTORICAL, descriptive lifetime value - observed to date, never a
    forecast. Named `historical_` throughout so no caller can mistake it for
    a prediction."""

    finding_type: Literal[AnalysisFindingType.LIFETIME_VALUE] = AnalysisFindingType.LIFETIME_VALUE
    segment: str
    entity_count: int
    average_order_value: float
    purchase_frequency: float           # orders per entity over the window
    observed_lifespan_days: float
    historical_value_per_entity: float
    #: "revenue" unless a margin rate was supplied. Recorded because a
    #: revenue-based figure must never be read as profit.
    value_basis: str
    margin_rate: float | None = None


class NonContributingEntitiesPayload(BaseModel):
    """Entities whose value over the period nets to zero or below.

    A real and ordinary case once returns and refunds are admitted into a
    monetary column: an entity that returned everything it bought nets to
    zero, one issued a credit nets below it.

    These are held OUT of the A/B/C bands and the concentration curve, and
    reported here instead. Two reasons, both about not misleading a reader:
    a cumulative share is only well-defined over non-negative parts (mixing
    signs in a descending cumulative sum sends the curve above 100% and back
    down), and an entity that returned everything is not a small buyer -
    filing it in band C beside genuinely small ones would say something
    false about both.
    """

    finding_type: Literal[AnalysisFindingType.NON_CONTRIBUTING_ENTITIES] = AnalysisFindingType.NON_CONTRIBUTING_ENTITIES
    entity_count: int
    zero_net_count: int          # netted to exactly 0
    negative_net_count: int      # netted below 0
    #: Sum of these entities' nets. Zero or below by construction.
    net_value_total: float
    #: What the ranked total would have been with these included, so a
    #: reader can reconcile the two numbers rather than wonder.
    combined_value_total: float
    examples: list[str] = Field(default_factory=list)
    note: str = ""


AnalysisFindingPayload = Annotated[
    Union[
        ConcentrationBandPayload,
        ConcentrationCurvePayload,
        NonContributingEntitiesPayload,
        SegmentProfilePayload,
        RetentionMatrixPayload,
        RepeatBehaviourPayload,
        AssociationRulePayload,
        LifetimeValuePayload,
    ],
    Field(discriminator="finding_type"),
]


class AnalysisFinding(BaseModel):
    #: Assigned by the engine after all analyses run, as
    #: f"{analysis}-{finding_type}-{position}". Stable, and citable by a
    #: GroundedClaim exactly like an exploration finding id.
    id: str = ""
    analysis: str
    finding_type: AnalysisFindingType
    columns: list[str]
    payload: AnalysisFindingPayload
    evidence: AnalysisEvidence


class BusinessAnalysisResult(BaseModel):
    """One analysis's full output, successful or not."""

    analysis: str
    ran: bool
    findings: list[AnalysisFinding] = Field(default_factory=list)
    #: Populated only when ran=False. The precise unmet requirement, in the
    #: same voice as the applicability report.
    not_run_reason: str | None = None
    #: Every knob the analysis used, echoed back. A result whose thresholds
    #: are invisible is not reproducible.
    parameters: dict = Field(default_factory=dict)


class BusinessAnalyticsFindings(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    #: Detector output, so a reader can see which column filled which role.
    detected_roles: dict = Field(default_factory=dict)
    #: Every analysis, applicable or not - the refusals are first-class.
    applicability: list[dict] = Field(default_factory=list)
    results: list[BusinessAnalysisResult] = Field(default_factory=list)
    #: How a per-row "value" was arrived at - the monetary column as-is, or
    #: monetary x quantity when the column is a unit price. Reported at the
    #: top level because every summed figure below depends on it, and a
    #: revenue total and a unit-price total look equally plausible alone.
    #: None on a row written before this existed.
    value_definition: dict | None = None
    #: Set when the monetary column reads as a unit price but no quantity
    #: cleared the confidence floor - the analyses ran on the unit price and
    #: this is the question a human should answer.
    quantity_confirmation: dict | None = None

    def all_findings(self) -> list[AnalysisFinding]:
        return [f for r in self.results for f in r.findings]
