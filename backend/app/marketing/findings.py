"""The Marketing Agent's output contract.

Same shape family as app/analytics/findings.py and app/exploration/findings.py
on purpose: closed enums, an evidence block on every finding, stable ids a
GroundedClaim can cite with no special-casing at the grounding layer.

NO CAUSAL LANGUAGE ANYWHERE IN THIS MODULE - not in field names, not in enum
values, not in payload keys. A frequency ABOVE a threshold is reported; it is
never said to "cause" a CTR decline. A creative UNDERPERFORMS the median; it
does not "hurt" anything. Same defence exploration and analytics use: the
Narrative Agent can only inherit vocabulary this schema actually contains.

THREE SEVERITIES, mapped onto what the platform already does:

    WARNING      escalates for a human decision. Never auto-fixed - see the
                 module docstring in app/marketing/rules.py for why ad spend
                 makes that non-negotiable.
    IMPROVEMENT  informational and acknowledgeable. Never blocks a run.
    KEY_VALUE    a reported number, never a finding about one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

#: Bump when a change here is not backward-compatible for a reader of an old
#: marketing_findings row. Additive optional fields do not require a bump.
CURRENT_SCHEMA_VERSION = 1


class MarketingSeverity(str, Enum):
    """The three outcomes, and nothing else."""

    WARNING = "warning"          # escalates to a human
    IMPROVEMENT = "improvement"  # informational, acknowledgeable
    KEY_VALUE = "key_value"      # reported, never a finding


class MarketingFindingType(str, Enum):
    """Closed set. A rule not named here does not exist."""

    # WARNING
    CPA_ABOVE_BASELINE = "cpa_above_baseline"
    FREQUENCY_FATIGUE = "frequency_fatigue"
    CTR_DECLINE = "ctr_decline"
    BUDGET_MISPACING = "budget_mispacing"
    # IMPROVEMENT
    ROAS_BELOW_TARGET = "roas_below_target"
    ADSET_UNDERPERFORMING = "adset_underperforming"
    # KEY_VALUE
    ACCOUNT_TOTALS = "account_totals"


#: Which severity each finding type carries. Declared as data so a rule
#: cannot quietly emit itself at a different severity than the one the UI
#: and the escalation path were built around.
SEVERITY_OF: dict[MarketingFindingType, MarketingSeverity] = {
    MarketingFindingType.CPA_ABOVE_BASELINE: MarketingSeverity.WARNING,
    MarketingFindingType.FREQUENCY_FATIGUE: MarketingSeverity.WARNING,
    MarketingFindingType.CTR_DECLINE: MarketingSeverity.WARNING,
    MarketingFindingType.BUDGET_MISPACING: MarketingSeverity.WARNING,
    MarketingFindingType.ROAS_BELOW_TARGET: MarketingSeverity.IMPROVEMENT,
    MarketingFindingType.ADSET_UNDERPERFORMING: MarketingSeverity.IMPROVEMENT,
    MarketingFindingType.ACCOUNT_TOTALS: MarketingSeverity.KEY_VALUE,
}


class MarketingEvidence(BaseModel):
    """What the finding was computed on. A threshold breach with no
    denominator behind it is not something a reader can act on."""

    row_count: int                          # rows the rule consumed
    entity_count: int | None = None         # distinct ad sets, when grouped
    period_start: str | None = None         # ISO date of the earliest row
    period_end: str | None = None           # ISO date of the latest row
    #: Everything needed to reproduce the number exactly, including the
    #: threshold the rule was configured with.
    parameters: dict = Field(default_factory=dict)


class ThresholdBreachPayload(BaseModel):
    """The shape every WARNING and IMPROVEMENT shares.

    Four things, always, because a warning missing any of them cannot be
    judged: what was measured, what it came out at, what it was compared
    against, and what that comparison basis actually was.
    """

    #: A Literal over exactly the breach types, not the open enum: the
    #: discriminated union needs it, and it also makes it impossible for a
    #: rule to emit ACCOUNT_TOTALS through the breach shape.
    finding_type: Literal[
        MarketingFindingType.CPA_ABOVE_BASELINE,
        MarketingFindingType.FREQUENCY_FATIGUE,
        MarketingFindingType.CTR_DECLINE,
        MarketingFindingType.BUDGET_MISPACING,
        MarketingFindingType.ROAS_BELOW_TARGET,
        MarketingFindingType.ADSET_UNDERPERFORMING,
    ]
    severity: MarketingSeverity
    metric: str                    # "cpa", "frequency", "ctr", "daily_spend", "roas"
    observed: float
    threshold: float
    #: What `threshold` was derived from, in words - "account baseline CPA
    #: over 30 prior days", "configured daily budget target". Never blank:
    #: a threshold with no stated basis is a magic number by another route.
    compared_against: str
    #: The ad set this concerns, when the rule is per-ad-set rather than
    #: account-wide.
    scope: str | None = None
    #: Set when the metric could not be computed for part of the data, with
    #: the reason - an ad set with zero conversions has no CPA, and that is
    #: reported rather than emitted as infinity.
    undefined_note: str | None = None


class AccountTotalsPayload(BaseModel):
    """The KEY VALUES. Reported plainly, never a finding about anything.

    Every one is optional because it depends on which roles the source
    actually had; a missing conversion column means no blended CPA, and the
    absence is visible rather than rendered as zero.
    """

    finding_type: Literal[MarketingFindingType.ACCOUNT_TOTALS] = MarketingFindingType.ACCOUNT_TOTALS
    severity: Literal[MarketingSeverity.KEY_VALUE] = MarketingSeverity.KEY_VALUE
    period_start: str | None = None
    period_end: str | None = None
    total_spend: float | None = None
    total_impressions: int | None = None
    total_clicks: int | None = None
    total_conversions: int | None = None
    total_conversion_value: float | None = None
    blended_cpa: float | None = None
    blended_roas: float | None = None
    blended_ctr: float | None = None
    #: Why any of the above is None, keyed by field name. "no conversions
    #: recorded in this period, so a blended CPA is undefined" is a fact
    #: about the campaign, not a gap in the report.
    undefined: dict[str, str] = Field(default_factory=dict)


MarketingPayload = Annotated[
    Union[ThresholdBreachPayload, AccountTotalsPayload],
    Field(discriminator="finding_type"),
]


class MarketingFinding(BaseModel):
    #: Assigned by the engine after every rule runs, as
    #: f"marketing-{finding_type}-{position}". Stable, and citable exactly
    #: like an exploration or analytics finding id.
    id: str = ""
    finding_type: MarketingFindingType
    severity: MarketingSeverity
    columns: list[str]
    payload: MarketingPayload
    evidence: MarketingEvidence


class DerivedMetric(BaseModel):
    """How one derived number was arrived at.

    The same rule the analytics value basis follows: a figure a reader
    cannot trace back to its inputs is not usable. CPA from a spend column
    divided by a conversions column is a different quantity from a CPA the
    platform exported, and they must not look alike.
    """

    metric: str
    formula: str                    # "spend / conversions"
    source_columns: list[str]
    #: Rows where the denominator was zero, and therefore have no value for
    #: this metric. Never an infinity, never a silent NaN.
    undefined_row_count: int = 0
    undefined_reason: str | None = None


class PreprocessingReport(BaseModel):
    """Every transformation this agent applied, so a reader can separate
    what was in the file from what was computed on top of it."""

    #: The grain rows were aggregated to, stated whether or not it changed
    #: anything.
    grain: str
    rows_in: int
    rows_out: int
    #: Platform summary/total rows removed before any row-level analysis.
    summary_rows_excluded: int = 0
    summary_row_labels_matched: list[str] = Field(default_factory=list)
    #: Columns whose text was normalised, with what was stripped.
    normalisations: list[dict] = Field(default_factory=list)
    derived_metrics: list[DerivedMetric] = Field(default_factory=list)


class MarketingFindings(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    #: Whether this run's data supported the agent at all.
    applicable: bool = False
    #: Present when applicable is False - the precise roles that were
    #: missing, in the same voice as the analytics applicability report.
    not_applicable_reason: str | None = None
    #: Which marketing role each column filled, and which were absent.
    resolved_roles: dict = Field(default_factory=dict)
    missing_roles: list[str] = Field(default_factory=list)
    preprocessing: PreprocessingReport | None = None
    findings: list[MarketingFinding] = Field(default_factory=list)
    #: Rules that could not run, and the requirement each was missing.
    skipped_rules: list[dict] = Field(default_factory=list)
    #: Every threshold in force for this run.
    parameters: dict = Field(default_factory=dict)
    #: Chart specs, produced by the SAME backend path the analytics charts
    #: use (app/analytics/chart_specs.py), so they render identically on
    #: screen and in the deck.
    charts: list[dict] = Field(default_factory=list)

    def by_severity(self, severity: MarketingSeverity) -> list[MarketingFinding]:
        return [f for f in self.findings if f.severity is severity]
