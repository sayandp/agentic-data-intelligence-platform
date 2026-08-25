"""Part 3: the deeper marketing analyses.

Four additions, all deterministic and all capability-gated like every rule
before them. Each returns `(findings, skipped)` and is registered in
`app/marketing/rules.py::RULES`, so the engine's existing guarantees - one
rule never sinking the rest, stable ids assigned over the final list - apply
unchanged.

TWO PIECES OF MACHINERY ARE REUSED, NOT REBUILT.

`Delta` (app/comparison/models.py) computes every period-over-period movement.
It already answers the question this needs answered - absolute and relative
change, with a relative change from zero reported as undefined rather than as
infinity or as zero - and a second implementation would be a second set of
arithmetic rules to keep in agreement.

`run_abc_pareto` (app/analytics/pareto.py) computes spend concentration. It is
called UNMODIFIED, through a synthetic RoleDetection naming the ad set as the
item and spend as the monetary column. That is the whole point: the band
cutoffs, the concentration floor, the held-out non-contributing entities and
the "concentration is weak for this data" note are one implementation, and
ad-set spend is simply another subject to run it over.

NOTHING HERE IS CAUSAL. A frequency trend rising while a performance trend
falls is reported as two co-occurring movements over the same period. That is
what the data supports; anything stronger would be a claim these columns
cannot carry.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.pareto import run_abc_pareto
from app.analytics.roles import ColumnRole, Confidence, RoleCandidate, RoleDetection
from app.comparison.models import Delta
from app.marketing.config import MarketingConfig
from app.marketing.findings import (
    MarketingEvidence,
    MarketingFinding,
    MarketingFindingType,
    MarketingSeverity,
    SEVERITY_OF,
    SpendConcentrationPayload,
    ThresholdBreachPayload,
)

#: Score given to a synthetically asserted role. High enough to clear the
#: usable-confidence floor: these are not guesses, they are the marketing
#: pack's own already-resolved roles being handed to another analysis.
_ASSERTED_SCORE = 0.95


def _evidence(df: pd.DataFrame, date_col: str | None, config: MarketingConfig, **parameters) -> MarketingEvidence:
    dates = pd.to_datetime(df[date_col], errors="coerce") if date_col and date_col in df.columns else None
    return MarketingEvidence(
        row_count=int(len(df)),
        window_start=dates.min().date().isoformat() if dates is not None and dates.notna().any() else None,
        window_end=dates.max().date().isoformat() if dates is not None and dates.notna().any() else None,
        parameters=parameters,
    )


def _finding(
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
    """Every finding states rule, observed value, threshold and comparison
    basis - the guarantee the pack already made, kept for the new rules."""
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


def _adset_column(roles: dict) -> str | None:
    """What identifies an ad set.

    There is no separate ad-set role: the detector assigns an ad set name to
    CAMPAIGN_ID (and to ENTITY_ID), which is the grain this pack already
    reports at. Both are tried so a table naming only one still ranks, and
    the resolved column travels into every finding's `columns` so a reader
    is never left guessing which level a number describes.
    """
    return roles.get(ColumnRole.CAMPAIGN_ID) or roles.get(ColumnRole.ENTITY_ID)


def _split_windows(df: pd.DataFrame, date_col: str, window_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The recent window and the equal-length one before it.

    Same definition app/marketing/rules.py uses. Imported rather than
    redefined would be better still; it lives there as a private helper, and
    duplicating the four lines is preferable to making rules.py import from
    this module while this module imports from it.
    """
    dates = pd.to_datetime(df[date_col], errors="coerce")
    latest = dates.max()
    if pd.isna(latest):
        return df.iloc[0:0], df.iloc[0:0]
    recent_start = latest - pd.Timedelta(days=window_days - 1)
    prior_start = recent_start - pd.Timedelta(days=window_days)
    return df[dates >= recent_start], df[(dates >= prior_start) & (dates < recent_start)]


# ---------------------------------------------------------------------------
# 1. period-over-period movement per ad set
# ---------------------------------------------------------------------------

#: Metrics reported per ad set, and whether a RISE is the unwelcome direction.
_MOVEMENT_METRICS: tuple[tuple[str, bool], ...] = (
    ("cpa", True),
    ("roas", False),
    ("ctr", False),
)


def adset_period_movement(df, roles, config, baseline_profile=None):
    """How each ad set moved between the last window and the one before it.

    Uses Part 1's `Delta` for the arithmetic, including its rule that a
    relative change from a zero baseline is undefined rather than infinite.
    """
    rule = MarketingFindingType.ADSET_PERIOD_MOVEMENT.value
    date_col = roles.get(ColumnRole.EVENT_DATE)
    adset_col = _adset_column(roles)
    if not date_col or not adset_col:
        return [], [{"rule": rule, "reason": "needs a reporting date and an ad set or campaign identifier"}]

    available = [m for m, _ in _MOVEMENT_METRICS if m in df.columns]
    if not available:
        return [], [{"rule": rule, "reason": "needs at least one derived metric (cpa, roas or ctr)"}]

    recent, prior = _split_windows(df, date_col, config.comparison_window_days)
    if recent.empty or prior.empty:
        return [], [
            {
                "rule": rule,
                "reason": (
                    f"needs two complete {config.comparison_window_days}-day windows; "
                    f"this export covers {len(recent)} recent and {len(prior)} prior row(s)"
                ),
            }
        ]

    findings: list[MarketingFinding] = []
    for metric in available:
        recent_by = recent.groupby(adset_col, observed=True)[metric].mean()
        prior_by = prior.groupby(adset_col, observed=True)[metric].mean()
        for adset in sorted(set(recent_by.index) & set(prior_by.index)):
            before, after = prior_by.get(adset), recent_by.get(adset)
            if pd.isna(before) or pd.isna(after):
                continue
            delta = Delta(label=metric, before=float(before), after=float(after))
            if not delta.changed:
                continue
            # Material moves only. `Delta.relative` is None when the prior
            # window was zero - that IS material (a metric appearing from
            # nothing), so it is reported rather than filtered out by a
            # comparison against a threshold it cannot be measured against.
            if delta.relative is not None and abs(delta.relative) < config.min_relative_movement:
                continue
            findings.append(
                _finding(
                    MarketingFindingType.ADSET_PERIOD_MOVEMENT,
                    metric=metric,
                    observed=float(after),
                    threshold=float(before),
                    compared_against=(
                        f"the same ad set's mean {metric} over the prior {config.comparison_window_days} days "
                        f"(within this run; {metric} is derived after baseline profiling, so no stored baseline exists for it)"
                    ),
                    columns=[adset_col, date_col, metric],
                    evidence=_evidence(
                        recent,
                        date_col,
                        config,
                        window_days=config.comparison_window_days,
                        absolute_change=delta.absolute,
                        relative_change=delta.relative,
                        relative_change_note=(
                            None
                            if delta.relative is not None
                            else "the prior window's value was zero, so relative change is undefined"
                        ),
                    ),
                    scope=str(adset),
                )
            )
    return findings, []


# ---------------------------------------------------------------------------
# 2. fatigue: frequency trend against performance trend
# ---------------------------------------------------------------------------


def _direction(series: pd.Series) -> float | None:
    """Mean change per step over an ordered series. None when there is not
    enough of it to have a direction at all."""
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    return float((clean.iloc[-1] - clean.iloc[0]) / (len(clean) - 1))


def adset_fatigue_trend(df, roles, config, baseline_profile=None):
    """Frequency rising while a performance metric falls, over the same period.

    Reported as two co-occurring movements, never as one explaining the other:
    an audience seeing an ad more often and that ad performing worse are two
    measurements, and this pack has no evidence connecting them.
    """
    rule = MarketingFindingType.ADSET_FATIGUE_TREND.value
    freq_col = roles.get(ColumnRole.FREQUENCY)
    date_col = roles.get(ColumnRole.EVENT_DATE)
    adset_col = _adset_column(roles)
    if not freq_col or not date_col or not adset_col:
        return [], [{"rule": rule, "reason": "needs a frequency column, a reporting date and an ad set identifier"}]

    performance = next((m for m in ("ctr", "roas") if m in df.columns), None)
    if performance is None:
        return [], [{"rule": rule, "reason": "needs a derived ctr or roas to trend against frequency"}]

    work = df.copy()
    work["__date__"] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=["__date__"])
    if work.empty:
        return [], [{"rule": rule, "reason": "no parseable reporting dates"}]

    findings: list[MarketingFinding] = []
    for adset, group in work.groupby(adset_col, observed=True):
        ordered = group.sort_values("__date__")
        daily = ordered.groupby(ordered["__date__"].dt.date).agg({freq_col: "mean", performance: "mean"})
        freq_direction = _direction(daily[freq_col])
        perf_direction = _direction(daily[performance])
        if freq_direction is None or perf_direction is None:
            continue
        # Both directions must be real movements, and opposed.
        if freq_direction <= 0 or perf_direction >= 0:
            continue

        findings.append(
            _finding(
                MarketingFindingType.ADSET_FATIGUE_TREND,
                metric=f"{freq_col} vs {performance}",
                observed=freq_direction,
                threshold=0.0,
                compared_against=(
                    f"the same ad set's own {performance} trend over the same days (within this run; "
                    f"{performance} is derived after baseline profiling, so no stored baseline exists for it). "
                    f"Frequency rose by {freq_direction:.4g} per day while {performance} fell by "
                    f"{abs(perf_direction):.4g} per day. These are two co-occurring movements, not a "
                    "demonstrated relationship."
                ),
                columns=[adset_col, date_col, freq_col, performance],
                evidence=_evidence(
                    ordered,
                    date_col,
                    config,
                    frequency_change_per_day=freq_direction,
                    performance_metric=performance,
                    performance_change_per_day=perf_direction,
                    days_observed=int(len(daily)),
                ),
                scope=str(adset),
            )
        )
    return findings, []


# ---------------------------------------------------------------------------
# 3. spend concentration - the analytics Pareto engine, unmodified
# ---------------------------------------------------------------------------


def _synthetic_detection(df: pd.DataFrame, entity_column: str, value_column: str) -> RoleDetection:
    """A RoleDetection asserting one entity and one monetary column.

    Deliberately carries NO quantity candidate: `resolve_value_basis` derives
    a line total only when a quantity exists AND the monetary column reads as
    a unit price, and spend is already a total. Omitting quantity makes the
    basis unambiguously "the column as-is", which is what an ad-spend figure
    is.
    """
    return RoleDetection(
        candidates=[
            RoleCandidate(value_column, ColumnRole.MONETARY, _ASSERTED_SCORE, ["asserted by the marketing pack"]),
            RoleCandidate(entity_column, ColumnRole.ITEM_ID, _ASSERTED_SCORE, ["asserted by the marketing pack"]),
        ],
        minimum_usable=Confidence.MEDIUM,
        row_count=int(len(df)),
    )


def spend_concentration(df, roles, config, baseline_profile=None):
    """Which ad sets consume the budget, and which return it.

    Runs the analytics agent's ABC/Pareto over ad sets twice - once by spend,
    once by conversion value - and reports the top band of each. Comparing the
    two answers "is the money going where the return is" without this module
    computing a single band boundary of its own.
    """
    rule = MarketingFindingType.SPEND_CONCENTRATION.value
    adset_col = _adset_column(roles)
    spend_col = roles.get(ColumnRole.SPEND)
    if not adset_col or not spend_col:
        return [], [{"rule": rule, "reason": "needs a spend column and an ad set or campaign identifier"}]

    value_col = roles.get(ColumnRole.CONVERSION_VALUE)
    findings: list[MarketingFinding] = []
    skipped: list[dict] = []

    for label, column in (("spend", spend_col), ("return", value_col)):
        if column is None:
            skipped.append(
                {"rule": rule, "reason": "no conversion-value column, so return concentration is not measured"}
            )
            continue

        result = run_abc_pareto(df, _synthetic_detection(df, adset_col, column))
        if not result.ran:
            skipped.append({"rule": rule, "reason": f"{label} concentration: {result.not_run_reason}"})
            continue

        band_a = next(
            (
                f.payload.model_dump()
                for f in result.findings
                if f.payload.model_dump().get("band") == "A"
            ),
            None,
        )
        if band_a is None:
            continue

        entity_count = int(band_a.get("entity_count") or 0)
        share = float(band_a.get("value_share") or 0.0)
        severity = SEVERITY_OF[MarketingFindingType.SPEND_CONCENTRATION]
        findings.append(
            MarketingFinding(
                finding_type=MarketingFindingType.SPEND_CONCENTRATION,
                severity=severity,
                columns=[adset_col, column],
                payload=SpendConcentrationPayload(
                    measured_over=label,
                    band="A",
                    entity_count=entity_count,
                    value_share=round(share, 6),
                    band_cutoff=float(config.concentration_band_cutoff),
                    top_entities=[str(e) for e in (band_a.get("top_entities") or [])],
                    compared_against=(
                        f"the A/B/C band cutoffs the business-analytics agent uses, applied to `{column}` "
                        f"grouped by `{adset_col}`. Band A is the smallest set of ad sets accounting for "
                        f"{config.concentration_band_cutoff:.0%} of {label}."
                    ),
                ),
                evidence=_evidence(
                    df,
                    roles.get(ColumnRole.EVENT_DATE),
                    config,
                    band="A",
                    entity_count=entity_count,
                    measured_over=label,
                ),
            )
        )

    return findings, skipped


# ---------------------------------------------------------------------------
# 4. efficiency ranking against the account median
# ---------------------------------------------------------------------------

#: Metrics ranked, and whether a HIGHER value is worse.
_EFFICIENCY_METRICS: tuple[tuple[str, bool], ...] = (("cpa", True), ("roas", False))


def adset_efficiency_ranking(df, roles, config, baseline_profile=None):
    """Every ad set placed against the account median, with the median stated.

    The median rather than the mean: one runaway ad set drags a mean far
    enough that most ad sets sit "below average", which is arithmetic rather
    than a finding.
    """
    rule = MarketingFindingType.ADSET_EFFICIENCY_RANK.value
    adset_col = _adset_column(roles)
    if not adset_col:
        return [], [{"rule": rule, "reason": "needs an ad set or campaign identifier"}]

    available = [(m, worse_high) for m, worse_high in _EFFICIENCY_METRICS if m in df.columns]
    if not available:
        return [], [{"rule": rule, "reason": "needs a derived cpa or roas"}]

    findings: list[MarketingFinding] = []
    for metric, worse_when_high in available:
        by_adset = df.groupby(adset_col, observed=True)[metric].mean().dropna()
        if by_adset.empty or len(by_adset) < config.min_adsets_for_ranking:
            continue
        median = float(by_adset.median())
        if median == 0:
            continue

        for adset in sorted(by_adset.index):
            observed = float(by_adset[adset])
            worse = observed > median if worse_when_high else observed < median
            if not worse:
                continue
            findings.append(
                _finding(
                    MarketingFindingType.ADSET_EFFICIENCY_RANK,
                    metric=metric,
                    observed=observed,
                    threshold=median,
                    compared_against=(
                        f"the median {metric} across all {len(by_adset)} ad sets in this export "
                        f"(median {median:,.4g}; within this run, since {metric} is derived after baseline "
                        "profiling and has no stored baseline)"
                    ),
                    columns=[adset_col, metric],
                    evidence=_evidence(
                        df,
                        roles.get(ColumnRole.EVENT_DATE),
                        config,
                        account_median=median,
                        adsets_ranked=int(len(by_adset)),
                        direction="above the median" if worse_when_high else "below the median",
                    ),
                    scope=str(adset),
                )
            )
    return findings, []


DEPTH_RULES = (
    adset_period_movement,
    adset_fatigue_trend,
    spend_concentration,
    adset_efficiency_ranking,
)
