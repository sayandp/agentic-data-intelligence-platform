"""Every threshold the Marketing Agent uses, in one place.

NO MAGIC NUMBERS ANYWHERE ELSE IN THIS PACKAGE. A rule whose threshold is
buried in its own body cannot be tuned by the person who owns the ad budget,
and cannot be reported alongside the finding it produced - and this agent's
whole output is "observed X against configured threshold Y".

Same shape as app/exploration/config.py and app/narrative/config.py: a
frozen dataclass with documented defaults, passed in rather than read from
module scope, so a test can vary one knob without touching global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analytics.roles import ColumnRole

#: The roles a source MUST have before this agent will run at all.
#:
#: Spend alone. Without it there is no campaign performance to report; with
#: it, plus one of the ad-only signals below, there is.
#:
#: A reporting date is deliberately NOT required. Real exports are often
#: aggregated per ad or per ad set with no date column - Kaggle's Facebook
#: ad-campaign dataset is one row per ad - and refusing those outright loses
#: spend, impressions, clicks, conversions, blended CPA and CTR, and ad-set
#: comparison, all of which need no date at all. The four time-based rules
#: each skip with a stated reason when there is no date, which is the honest
#: place for that to be said.
#:
#: Deliberately short. Every additional required role is a source this agent
#: refuses, and the applicability report is a better place to say "ROAS could
#: not be computed" than the qualification gate is.
DEFAULT_REQUIRED_ROLES: tuple[ColumnRole, ...] = (ColumnRole.SPEND,)

#: At least ONE of these must also be present. Spend and a date alone are
#: not enough to call a file an ads export - almost any commerce CSV has a
#: cost column and a date, and a retail dataset showing a Marketing tab is a
#: worse failure than an ads file being refused. These are the roles that
#: only an ad platform produces.
DEFAULT_REQUIRED_ANY_OF: tuple[ColumnRole, ...] = (
    ColumnRole.IMPRESSIONS,
    ColumnRole.CLICKS,
    ColumnRole.CTR,
    ColumnRole.FREQUENCY,
)

#: Roles that unlock specific rules and key values without gating the agent.
DEFAULT_OPTIONAL_ROLES: tuple[ColumnRole, ...] = (
    ColumnRole.EVENT_DATE,
    ColumnRole.CAMPAIGN_ID,
    ColumnRole.IMPRESSIONS,
    ColumnRole.CLICKS,
    ColumnRole.CTR,
    ColumnRole.FREQUENCY,
    ColumnRole.CONVERSIONS,
    ColumnRole.CONVERSION_VALUE,
)


@dataclass(frozen=True)
class MarketingConfig:
    """Thresholds and grain. Every field is reported back in the findings
    output, so a reader can always see what a rule was measured against."""

    # -- grain --
    #: The row grain the agent aggregates to before any rule runs. Ad
    #: platforms export at whatever grain the user picked in their UI, so
    #: this is normalised rather than assumed.
    grain: str = "adset_day"

    # -- WARNING thresholds (each escalates to a human) --
    #: A CPA above baseline by this multiple is a spike. 1.3 = 30% worse.
    cpa_spike_multiple: float = 1.3
    #: Average impressions per person above which an audience is saturated.
    frequency_fatigue_threshold: float = 3.5
    #: A CTR decline sharper than this fraction, comparing the recent window
    #: against the prior one, is a decline rather than noise.
    ctr_decline_fraction: float = 0.30
    #: Daily spend outside +/- this fraction of the configured target is
    #: mis-pacing. Only checked when a target is actually configured.
    budget_pacing_tolerance: float = 0.25
    #: The daily spend a campaign is meant to run at. None means the rule
    #: does not fire - a pacing rule with a guessed target is worse than no
    #: pacing rule, because it invents the thing it measures against.
    daily_budget_target: float | None = None

    # -- IMPROVEMENT thresholds (informational, never blocking) --
    #: ROAS below this is flagged as an improvement opportunity.
    roas_target: float = 2.0
    #: An ad set whose CPA is worse than the account median by this multiple
    #: is underperforming its peers.
    underperformance_multiple: float = 1.5

    # -- windows --
    #: Days in the "recent" window for trend comparisons. The prior window
    #: of the same length is what it is compared against.
    trend_window_days: int = 7

    #: Length of each half of a period-over-period comparison, in days.
    #: Separate from trend_window_days: a trend is measured within one
    #: window, a movement between two.
    comparison_window_days: int = 7

    #: The cumulative share band A accounts for. Stated here so the
    #: marketing pack reports the same cutoff the analytics Pareto engine
    #: applies, rather than a second number that could drift from it.
    concentration_band_cutoff: float = 0.8

    #: Below this many ad sets, a median is a statement about two or three
    #: numbers and ranking against it says nothing.
    min_adsets_for_ranking: int = 3

    #: How large a period-over-period move must be, relative to the prior
    #: window, before it is reported. Without it every ad set reports a
    #: movement every run - a CTR shifting by 0.002% is arithmetic, not a
    #: finding, and a list of them buries the one move that matters.
    min_relative_movement: float = 0.05
    #: Minimum rows in a window before a trend rule will fire. Below this a
    #: comparison is noise, and the rule reports that it was skipped.
    min_rows_for_trend: int = 3

    # -- preprocessing --
    #: Rows whose campaign label matches this are platform summary rows and
    #: are EXCLUDED from row-level analysis. Meta and Google both append
    #: them; summing a column that already contains its own total is the
    #: quietest way to double every number on the page.
    summary_row_labels: tuple[str, ...] = ("total", "totals", "grand total", "summary", "all campaigns", "account total")

    #: A CTR column may arrive as a fraction (0.023) or as percentage points
    #: (2.3). Decided per COLUMN, not per value: if the median exceeds this,
    #: the column is read as percentage points and divided by 100.
    ctr_percent_detection_threshold: float = 1.0

    required_roles: tuple[ColumnRole, ...] = field(default=DEFAULT_REQUIRED_ROLES)
    #: At least one of these must be present too. Empty disables the check.
    required_any_of: tuple[ColumnRole, ...] = field(default=DEFAULT_REQUIRED_ANY_OF)
    optional_roles: tuple[ColumnRole, ...] = field(default=DEFAULT_OPTIONAL_ROLES)

    def as_reported(self) -> dict:
        """The knobs, echoed into the findings payload. A result whose
        thresholds are invisible is not reproducible."""
        return {
            "grain": self.grain,
            "cpa_spike_multiple": self.cpa_spike_multiple,
            "frequency_fatigue_threshold": self.frequency_fatigue_threshold,
            "ctr_decline_fraction": self.ctr_decline_fraction,
            "budget_pacing_tolerance": self.budget_pacing_tolerance,
            "daily_budget_target": self.daily_budget_target,
            "roas_target": self.roas_target,
            "underperformance_multiple": self.underperformance_multiple,
            "trend_window_days": self.trend_window_days,
            "comparison_window_days": self.comparison_window_days,
            "concentration_band_cutoff": self.concentration_band_cutoff,
            "min_adsets_for_ranking": self.min_adsets_for_ranking,
            "min_relative_movement": self.min_relative_movement,
            "min_rows_for_trend": self.min_rows_for_trend,
            "ctr_percent_detection_threshold": self.ctr_percent_detection_threshold,
            "summary_row_labels": list(self.summary_row_labels),
            "required_roles": [r.value for r in self.required_roles],
            "required_any_of": [r.value for r in self.required_any_of],
        }
