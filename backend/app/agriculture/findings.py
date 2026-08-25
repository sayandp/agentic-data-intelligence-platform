"""The Agriculture Agent's output shapes.

Mirrors app/marketing/findings.py: a closed finding-type set, three
severities, a discriminated payload union, and evidence attached to every
finding. A threshold breach with no denominator behind it is not something a
reader can act on.

NO CAUSAL VOCABULARY. A yield falling while rainfall fell is two measurements
over the same season, and this pack says exactly that.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class AgricultureSeverity(str, Enum):
    WARNING = "warning"          # escalates to a person
    IMPROVEMENT = "improvement"  # informational
    KEY_VALUE = "key_value"      # plain reporting, never a finding about anything


class AgricultureFindingType(str, Enum):
    """Closed set. A rule not named here does not exist."""

    # WARNING
    YIELD_COLLAPSE = "yield_collapse"
    PRODUCTION_DROP = "production_drop"
    RAINFALL_OUTSIDE_RANGE = "rainfall_outside_range"
    AREA_FALL = "area_fall"
    # IMPROVEMENT
    BELOW_MEDIAN_YIELD = "below_median_yield"
    HIGH_YIELD_VARIANCE = "high_yield_variance"
    # KEY_VALUE
    SEASON_TOTALS = "season_totals"


SEVERITY_OF: dict[AgricultureFindingType, AgricultureSeverity] = {
    AgricultureFindingType.YIELD_COLLAPSE: AgricultureSeverity.WARNING,
    AgricultureFindingType.PRODUCTION_DROP: AgricultureSeverity.WARNING,
    AgricultureFindingType.RAINFALL_OUTSIDE_RANGE: AgricultureSeverity.WARNING,
    AgricultureFindingType.AREA_FALL: AgricultureSeverity.WARNING,
    AgricultureFindingType.BELOW_MEDIAN_YIELD: AgricultureSeverity.IMPROVEMENT,
    AgricultureFindingType.HIGH_YIELD_VARIANCE: AgricultureSeverity.IMPROVEMENT,
    AgricultureFindingType.SEASON_TOTALS: AgricultureSeverity.KEY_VALUE,
}


class AgricultureEvidence(BaseModel):
    """What the finding was computed on."""

    row_count: int
    grain: list[str] = Field(default_factory=list)
    observations: int | None = None
    parameters: dict = Field(default_factory=dict)


class ThresholdBreachPayload(BaseModel):
    """The shape every WARNING and IMPROVEMENT shares.

    Four things, always: what was measured, what it came out at, what it was
    compared against, and what that comparison basis actually was.
    """

    finding_type: Literal[
        AgricultureFindingType.YIELD_COLLAPSE,
        AgricultureFindingType.PRODUCTION_DROP,
        AgricultureFindingType.RAINFALL_OUTSIDE_RANGE,
        AgricultureFindingType.AREA_FALL,
        AgricultureFindingType.BELOW_MEDIAN_YIELD,
        AgricultureFindingType.HIGH_YIELD_VARIANCE,
    ]
    severity: AgricultureSeverity
    metric: str
    observed: float
    threshold: float
    #: Never blank: a threshold with no stated basis is a magic number by
    #: another route.
    compared_against: str
    #: The district / crop / season this concerns.
    scope: str | None = None
    #: Set when the metric could not be computed for part of the data, with
    #: the reason - a district with zero area has no yield, and that is
    #: reported rather than emitted as infinity.
    undefined_note: str | None = None


class SeasonTotalsPayload(BaseModel):
    """The KEY VALUES. Reported plainly, never a finding about anything.

    Every field is optional because it depends on which roles the source
    actually had; a missing area column means no total area, and the absence
    is visible rather than rendered as zero.
    """

    finding_type: Literal[AgricultureFindingType.SEASON_TOTALS] = AgricultureFindingType.SEASON_TOTALS
    severity: Literal[AgricultureSeverity.KEY_VALUE] = AgricultureSeverity.KEY_VALUE
    total_area: float | None = None
    total_production: float | None = None
    average_yield: float | None = None
    yield_basis: str | None = None
    top_crops: list[dict] = Field(default_factory=list)
    rainfall_mean: float | None = None
    rainfall_min: float | None = None
    rainfall_max: float | None = None
    districts: int | None = None
    crops: int | None = None
    seasons: list[str] = Field(default_factory=list)
    #: Why any of the above is None, keyed by field name.
    undefined: dict[str, str] = Field(default_factory=dict)


AgriculturePayload = Annotated[
    Union[ThresholdBreachPayload, SeasonTotalsPayload],
    Field(discriminator="finding_type"),
]


class AgricultureFinding(BaseModel):
    #: Assigned by the engine after every rule runs, exactly like the
    #: marketing and exploration ids, and citable the same way.
    id: str = ""
    finding_type: AgricultureFindingType
    severity: AgricultureSeverity
    columns: list[str]
    payload: AgriculturePayload
    evidence: AgricultureEvidence


class DerivedMetric(BaseModel):
    """How one derived number was arrived at."""

    metric: str
    formula: str
    source_columns: list[str]
    undefined_row_count: int = 0
    undefined_reason: str | None = None


class PreprocessingReport(BaseModel):
    """Every transformation applied before a rule saw the data."""

    rows_in: int
    rows_out: int
    grain: list[str] = Field(default_factory=list)
    aggregations: dict[str, str] = Field(default_factory=dict)
    normalisations: list[dict] = Field(default_factory=list)
    derived: list[DerivedMetric] = Field(default_factory=list)


class AgricultureFindings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    applicable: bool
    not_applicable_reason: str | None = None
    resolved_roles: dict = Field(default_factory=dict)
    missing_roles: list[str] = Field(default_factory=list)
    preprocessing: PreprocessingReport | None = None
    findings: list[AgricultureFinding] = Field(default_factory=list)
    skipped_rules: list[dict] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    charts: list[dict] = Field(default_factory=list)
