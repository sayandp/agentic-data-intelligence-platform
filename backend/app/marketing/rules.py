"""The rule pack. Deterministic, configurable, and incapable of fixing
anything.

NO RULE HERE AUTO-FIXES. Not one, ever, and there is no code path by which
one could: this module returns FINDINGS, not actions. It has no access to
the fix_chain, produces no fix spec, and nothing it emits is shaped like
something app/gate.py could apply. Ad spend is real money and a wrong
automatic "correction" to a campaign is not recoverable by re-running an
ingest - so every WARNING escalates to a person, and
tests/test_marketing_rules.py asserts that none of these can reach the
auto-apply allowlist.

NO LLM. NO CAUSAL VOCABULARY. A frequency ABOVE a threshold is reported; it
is never said to cause a CTR decline. Two things that moved together moved
together.

ON "VS NORMAL": app/profiling.py's baseline profiles the RAW columns of a
source at ingest time. `spend` is such a column, so a spend comparison can
and does read the real baseline. `cpa`, `roas` and `ctr` are DERIVED by
app/marketing/preprocess.py AFTER profiling, so no baseline entry for them
exists or could exist without changing the profiling path. Those rules
therefore compare against the run's own prior window, and every finding
states which basis it used in `compared_against` - the reader is never left
to assume a comparison was against something it was not.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.roles import ColumnRole
from app.marketing.config import MarketingConfig
from app.marketing.findings import (
    SEVERITY_OF,
    AccountTotalsPayload,
    MarketingEvidence,
    MarketingFinding,
    MarketingFindingType,
    MarketingSeverity,
    ThresholdBreachPayload,
)


def _evidence(df: pd.DataFrame, date_col: str | None, config: MarketingConfig, **parameters) -> MarketingEvidence:
    period_start = period_end = None
    if date_col and date_col in df.columns and not df.empty:
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if not dates.empty:
            period_start = dates.min().date().isoformat()
            period_end = dates.max().date().isoformat()
    return MarketingEvidence(row_count=len(df), period_start=period_start, period_end=period_end, parameters=parameters)


def _breach(
    finding_type: MarketingFindingType,
    metric: str,
    observed: float,
    threshold: float,
    compared_against: str,
    columns: list[str],
    evidence: MarketingEvidence,
    scope: str | None = None,
    undefined_note: str | None = None,
) -> MarketingFinding:
    severity = SEVERITY_OF[finding_type]
    return MarketingFinding(
        finding_type=finding_type,
        severity=severity,
        columns=columns,
        payload=ThresholdBreachPayload(
            finding_type=finding_type,
            severity=severity,
            metric=metric,
            observed=round(float(observed), 6),
            threshold=round(float(threshold), 6),
            compared_against=compared_against,
            scope=scope,
            undefined_note=undefined_note,
        ),
        evidence=evidence,
    )


def _split_windows(df: pd.DataFrame, date_col: str, window_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The recent window and the one immediately before it, of equal length."""
    dates = pd.to_datetime(df[date_col], errors="coerce")
    latest = dates.max()
    if pd.isna(latest):
        return df.iloc[0:0], df.iloc[0:0]
    recent_start = latest - pd.Timedelta(days=window_days - 1)
    prior_start = recent_start - pd.Timedelta(days=window_days)
    recent = df[dates >= recent_start]
    prior = df[(dates >= prior_start) & (dates < recent_start)]
    return recent, prior


# ---------------------------------------------------------------------------
# WARNING rules. Each escalates to a human; none is auto-applicable.
# ---------------------------------------------------------------------------


def cpa_above_baseline(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    """Cost per acquisition, recent window against the prior one."""
    date_col = roles.get(ColumnRole.EVENT_DATE)
    if "cpa" not in df.columns or not date_col:
        return [], [{"rule": MarketingFindingType.CPA_ABOVE_BASELINE.value, "reason": "needs a derived CPA and a reporting date"}]

    recent, prior = _split_windows(df, date_col, config.trend_window_days)
    recent_cpa, prior_cpa = recent["cpa"].dropna(), prior["cpa"].dropna()
    if len(recent_cpa) < config.min_rows_for_trend or len(prior_cpa) < config.min_rows_for_trend:
        return [], [
            {
                "rule": MarketingFindingType.CPA_ABOVE_BASELINE.value,
                "reason": (
                    f"needs at least {config.min_rows_for_trend} row(s) with a defined CPA in each of the "
                    f"{config.trend_window_days}-day windows; found {len(recent_cpa)} recent and {len(prior_cpa)} prior"
                ),
            }
        ]

    observed, base = float(recent_cpa.mean()), float(prior_cpa.mean())
    threshold = base * config.cpa_spike_multiple
    if observed <= threshold:
        return [], []

    undefined = int(recent["cpa"].isna().sum())
    return [
        _breach(
            MarketingFindingType.CPA_ABOVE_BASELINE,
            metric="cpa",
            observed=observed,
            threshold=threshold,
            compared_against=(
                f"mean CPA of {base:,.2f} over the prior {config.trend_window_days} days, "
                f"times the configured {config.cpa_spike_multiple} spike multiple. "
                "CPA is derived after baseline profiling, so this is a within-run comparison, "
                "not a comparison against the source's stored baseline."
            ),
            columns=[c for c in [roles.get(ColumnRole.SPEND), roles.get(ColumnRole.CONVERSIONS)] if c],
            evidence=_evidence(
                recent, date_col, config,
                cpa_spike_multiple=config.cpa_spike_multiple,
                trend_window_days=config.trend_window_days,
                prior_window_mean_cpa=round(base, 6),
            ),
            undefined_note=(
                f"{undefined} row(s) in the recent window recorded zero conversions and have no CPA; "
                "they are excluded from this mean rather than counted as zero"
            )
            if undefined
            else None,
        )
    ], []


def frequency_fatigue(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    """Average impressions per person above the configured ceiling."""
    freq_col = roles.get(ColumnRole.FREQUENCY)
    date_col = roles.get(ColumnRole.EVENT_DATE)
    campaign_col = roles.get(ColumnRole.CAMPAIGN_ID)
    if not freq_col or freq_col not in df.columns:
        return [], [{"rule": MarketingFindingType.FREQUENCY_FATIGUE.value, "reason": "needs a frequency column"}]

    findings: list[MarketingFinding] = []
    groups = df.groupby(campaign_col, dropna=False) if campaign_col and campaign_col in df.columns else [(None, df)]
    for scope, group in groups:
        values = group[freq_col].dropna()
        if values.empty:
            continue
        observed = float(values.mean())
        if observed <= config.frequency_fatigue_threshold:
            continue
        findings.append(
            _breach(
                MarketingFindingType.FREQUENCY_FATIGUE,
                metric="frequency",
                observed=observed,
                threshold=config.frequency_fatigue_threshold,
                compared_against=f"the configured fatigue threshold of {config.frequency_fatigue_threshold}",
                columns=[freq_col],
                evidence=_evidence(group, date_col, config, frequency_fatigue_threshold=config.frequency_fatigue_threshold),
                scope=str(scope) if scope is not None else None,
            )
        )
    return findings, []


def ctr_decline(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    """Click-through rate, recent window against the prior one."""
    date_col = roles.get(ColumnRole.EVENT_DATE)
    if "ctr" not in df.columns or not date_col:
        return [], [{"rule": MarketingFindingType.CTR_DECLINE.value, "reason": "needs a derived CTR and a reporting date"}]

    recent, prior = _split_windows(df, date_col, config.trend_window_days)
    recent_ctr, prior_ctr = recent["ctr"].dropna(), prior["ctr"].dropna()
    if len(recent_ctr) < config.min_rows_for_trend or len(prior_ctr) < config.min_rows_for_trend:
        return [], [
            {
                "rule": MarketingFindingType.CTR_DECLINE.value,
                "reason": (
                    f"needs at least {config.min_rows_for_trend} row(s) with a defined CTR in each "
                    f"{config.trend_window_days}-day window; found {len(recent_ctr)} recent and {len(prior_ctr)} prior"
                ),
            }
        ]

    observed, base = float(recent_ctr.mean()), float(prior_ctr.mean())
    if base <= 0:
        return [], [{"rule": MarketingFindingType.CTR_DECLINE.value, "reason": "prior window recorded no clicks, so there is no rate to compare against"}]

    decline = (base - observed) / base
    if decline < config.ctr_decline_fraction:
        return [], []

    floor = base * (1 - config.ctr_decline_fraction)
    return [
        _breach(
            MarketingFindingType.CTR_DECLINE,
            metric="ctr",
            observed=observed,
            threshold=floor,
            compared_against=(
                f"mean CTR of {base:.4%} over the prior {config.trend_window_days} days, less the configured "
                f"{config.ctr_decline_fraction:.0%} decline tolerance. CTR is recomputed after aggregation, "
                "so this is a within-run comparison rather than one against the source's stored baseline."
            ),
            columns=[c for c in [roles.get(ColumnRole.CLICKS), roles.get(ColumnRole.IMPRESSIONS)] if c],
            evidence=_evidence(
                recent, date_col, config,
                ctr_decline_fraction=config.ctr_decline_fraction,
                trend_window_days=config.trend_window_days,
                prior_window_mean_ctr=round(base, 6),
                observed_decline_fraction=round(decline, 6),
            ),
        )
    ], []


def budget_mispacing(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    """Daily spend outside the tolerance band around a CONFIGURED target.

    Fires only when a target is configured. A pacing rule that invents its
    own target measures against a number nobody chose.
    """
    spend_col = roles.get(ColumnRole.SPEND)
    date_col = roles.get(ColumnRole.EVENT_DATE)
    if not spend_col or not date_col or spend_col not in df.columns:
        return [], [{"rule": MarketingFindingType.BUDGET_MISPACING.value, "reason": "needs a spend column and a reporting date"}]
    if config.daily_budget_target is None:
        return [], [
            {
                "rule": MarketingFindingType.BUDGET_MISPACING.value,
                "reason": "no daily budget target is configured; pacing is not measured against a guessed target",
            }
        ]

    daily = df.groupby(pd.to_datetime(df[date_col], errors="coerce").dt.date)[spend_col].sum()
    if daily.empty:
        return [], [{"rule": MarketingFindingType.BUDGET_MISPACING.value, "reason": "no dated spend rows to pace"}]

    target = float(config.daily_budget_target)
    observed = float(daily.mean())
    tolerance = config.budget_pacing_tolerance
    low, high = target * (1 - tolerance), target * (1 + tolerance)
    if low <= observed <= high:
        return [], []

    return [
        _breach(
            MarketingFindingType.BUDGET_MISPACING,
            metric="daily_spend",
            observed=observed,
            threshold=high if observed > high else low,
            compared_against=(
                f"the configured daily budget target of {target:,.2f} "
                f"with a {tolerance:.0%} tolerance band ({low:,.2f} to {high:,.2f})"
            ),
            columns=[spend_col],
            evidence=_evidence(df, date_col, config, daily_budget_target=target, budget_pacing_tolerance=tolerance, days_observed=len(daily)),
        )
    ], []


# ---------------------------------------------------------------------------
# IMPROVEMENT rules. Informational, acknowledgeable, never blocking.
# ---------------------------------------------------------------------------


def roas_below_target(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    spend_col, value_col = roles.get(ColumnRole.SPEND), roles.get(ColumnRole.CONVERSION_VALUE)
    date_col = roles.get(ColumnRole.EVENT_DATE)
    if "roas" not in df.columns or not spend_col or not value_col:
        return [], [{"rule": MarketingFindingType.ROAS_BELOW_TARGET.value, "reason": "needs spend and conversion-value columns"}]

    total_spend = float(df[spend_col].sum())
    if total_spend <= 0:
        return [], [{"rule": MarketingFindingType.ROAS_BELOW_TARGET.value, "reason": "no spend recorded, so return on ad spend is undefined"}]

    observed = float(df[value_col].sum()) / total_spend
    if observed >= config.roas_target:
        return [], []
    return [
        _breach(
            MarketingFindingType.ROAS_BELOW_TARGET,
            metric="roas",
            observed=observed,
            threshold=config.roas_target,
            compared_against=f"the configured ROAS target of {config.roas_target}",
            columns=[value_col, spend_col],
            evidence=_evidence(df, date_col, config, roas_target=config.roas_target, total_spend=round(total_spend, 2)),
        )
    ], []


def adset_underperforming(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    """Ad sets whose CPA is worse than the account median by a multiple."""
    campaign_col, date_col = roles.get(ColumnRole.CAMPAIGN_ID), roles.get(ColumnRole.EVENT_DATE)
    if "cpa" not in df.columns or not campaign_col or campaign_col not in df.columns:
        return [], [{"rule": MarketingFindingType.ADSET_UNDERPERFORMING.value, "reason": "needs a derived CPA and a campaign/ad-set column"}]

    per_adset = df.groupby(campaign_col, dropna=False)["cpa"].mean().dropna()
    if len(per_adset) < 2:
        return [], [{"rule": MarketingFindingType.ADSET_UNDERPERFORMING.value, "reason": "needs at least two ad sets with a defined CPA to compare"}]

    median = float(per_adset.median())
    threshold = median * config.underperformance_multiple
    findings = []
    for scope, observed in per_adset.items():
        if float(observed) <= threshold:
            continue
        findings.append(
            _breach(
                MarketingFindingType.ADSET_UNDERPERFORMING,
                metric="cpa",
                observed=float(observed),
                threshold=threshold,
                compared_against=(
                    f"the account median CPA of {median:,.2f} across {len(per_adset)} ad sets, "
                    f"times the configured {config.underperformance_multiple} multiple"
                ),
                columns=[campaign_col],
                evidence=_evidence(
                    df[df[campaign_col] == scope], date_col, config,
                    underperformance_multiple=config.underperformance_multiple,
                    account_median_cpa=round(median, 6),
                    adsets_compared=len(per_adset),
                ),
                scope=str(scope),
            )
        )
    return findings, []


# ---------------------------------------------------------------------------
# KEY VALUES. Reported, never a finding about anything.
# ---------------------------------------------------------------------------


def account_totals(df, roles, config, baseline_profile=None) -> tuple[list[MarketingFinding], list[dict]]:
    date_col = roles.get(ColumnRole.EVENT_DATE)
    evidence = _evidence(df, date_col, config, grain=config.grain)

    def total(role: ColumnRole) -> float | None:
        column = roles.get(role)
        return float(df[column].sum()) if column and column in df.columns else None

    spend = total(ColumnRole.SPEND)
    impressions = total(ColumnRole.IMPRESSIONS)
    clicks = total(ColumnRole.CLICKS)
    conversions = total(ColumnRole.CONVERSIONS)
    value = total(ColumnRole.CONVERSION_VALUE)

    undefined: dict[str, str] = {}

    def ratio(numerator, denominator, name, note, needs: str) -> float | None:
        # A blank with no reason is the thing this agent exists to avoid. Two
        # different absences are possible and they mean different things: the
        # source never carried the column, or it did and the denominator was
        # zero. Both are recorded; neither is rendered as 0.
        if numerator is None or denominator is None:
            undefined[name] = f"this export has no {needs} column, so {name.replace('_', ' ')} cannot be computed"
            return None
        if denominator == 0:
            undefined[name] = note
            return None
        return numerator / denominator

    payload = AccountTotalsPayload(
        period_start=evidence.period_start,
        period_end=evidence.period_end,
        total_spend=spend,
        total_impressions=int(impressions) if impressions is not None else None,
        total_clicks=int(clicks) if clicks is not None else None,
        total_conversions=int(conversions) if conversions is not None else None,
        total_conversion_value=value,
        blended_cpa=ratio(
            spend, conversions, "blended_cpa",
            "no conversions were recorded in this period, so a blended CPA is undefined",
            needs="conversions",
        ),
        blended_roas=ratio(
            value, spend, "blended_roas",
            "no spend was recorded in this period, so a blended ROAS is undefined",
            needs="conversion-value",
        ),
        blended_ctr=ratio(
            clicks, impressions, "blended_ctr",
            "no impressions were recorded in this period, so a blended CTR is undefined",
            needs="impressions",
        ),
        undefined=undefined,
    )
    columns = [c for c in roles.values() if c in df.columns]
    return [
        MarketingFinding(
            finding_type=MarketingFindingType.ACCOUNT_TOTALS,
            severity=MarketingSeverity.KEY_VALUE,
            columns=columns,
            payload=payload,
            evidence=evidence,
        )
    ], []


#: Every rule, in report order: key values first, then warnings, then
#: improvements. Declared as data so the engine cannot run a rule the
#: severity table does not know about.
RULES = (
    account_totals,
    cpa_above_baseline,
    frequency_fatigue,
    ctr_decline,
    budget_mispacing,
    roas_below_target,
    adset_underperforming,
)
