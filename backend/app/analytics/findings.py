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
    CONCENTRATION_BAND = "concentration_band"   # ABC/Pareto band membership + share
    CONCENTRATION_CURVE = "concentration_curve"  # the cumulative contribution curve


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


AnalysisFindingPayload = Annotated[
    Union[ConcentrationBandPayload, ConcentrationCurvePayload],
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

    def all_findings(self) -> list[AnalysisFinding]:
        return [f for r in self.results for f in r.findings]
