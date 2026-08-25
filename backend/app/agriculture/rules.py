"""The Agriculture Agent's rules. Deterministic, capability-gated, no LLM.

Every finding states four things - what was measured, what it came out at,
what it was compared against, and what that comparison basis was - because a
warning missing any of them cannot be judged.

EVERY HISTORY-BASED RULE COMPARES A DISTRICT-CROP WITH ITS OWN PAST. Soil,
rainfall and crop mix differ between districts, so a cross-district comparison
dressed as a collapse would be a false alarm every season. Where a rule does
compare across districts - the below-median improvement - it says so and names
the median.

NOTHING HERE IS CAUSAL. A yield falling in a season when rainfall also fell is
two measurements over the same period. This pack reports both and connects
neither.
"""

from __future__ import annotations

import pandas as pd

from app.agriculture.config import AgricultureConfig
from app.agriculture.findings import (
    AgricultureEvidence,
    AgricultureFinding,
    AgricultureFindingType,
    SEVERITY_OF,
    SeasonTotalsPayload,
    ThresholdBreachPayload,
)
from app.agriculture.preprocess import DERIVED_YIELD
from app.analytics.roles import ColumnRole

RuleResult = tuple[list[AgricultureFinding], list[dict]]


def _evidence(df: pd.DataFrame, grain: list[str], **parameters) -> AgricultureEvidence:
    return AgricultureEvidence(
        row_count=int(len(df)),
        grain=grain,
        observations=parameters.pop("observations", None),
        parameters=parameters,
    )


def _breach(
    finding_type: AgricultureFindingType,
    metric: str,
    observed: float,
    threshold: float,
    compared_against: str,
    columns: list[str],
    evidence: AgricultureEvidence,
    scope: str | None = None,
    undefined_note: str | None = None,
) -> AgricultureFinding:
    severity = SEVERITY_OF[finding_type]
    return AgricultureFinding(
        finding_type=finding_type,
        severity=severity,
        columns=[c for c in columns if c],
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


def _grain_columns(roles: dict, config: AgricultureConfig) -> list[str]:
    return [roles[r] for r in config.grain if roles.get(r)]


def _series_key(roles: dict) -> list[str]:
    """The identity of one growing series: a district and a crop. Its history
    is the same district-crop over successive years or seasons."""
    return [roles[r] for r in (ColumnRole.DISTRICT, ColumnRole.CROP) if roles.get(r)]


def _ordering_column(roles: dict) -> str | None:
    """What orders a history. Crop year first; a season label alone has no
    inherent order and is not used to claim one."""
    return roles.get(ColumnRole.CROP_YEAR)


# ---------------------------------------------------------------------------
# WARNING rules. Each escalates to a person; none is auto-applicable.
# ---------------------------------------------------------------------------


def yield_collapse(df, roles, config, baseline_profile=None) -> RuleResult:
    """A district-crop's yield falling far below its OWN historical mean."""
    rule = AgricultureFindingType.YIELD_COLLAPSE.value
    key = _series_key(roles)
    order = _ordering_column(roles)
    if not key or DERIVED_YIELD not in df.columns:
        return [], [{"rule": rule, "reason": "needs a district, a crop and a yield (supplied or derivable)"}]
    if not order:
        return [], [{"rule": rule, "reason": "needs a crop-year column to order a history"}]

    findings: list[AgricultureFinding] = []
    for name, group in df.groupby(key, observed=True):
        ordered = group.sort_values(order)
        values = pd.to_numeric(ordered[DERIVED_YIELD], errors="coerce").dropna()
        if len(values) < config.min_history_points + 1:
            continue
        latest = float(values.iloc[-1])
        history = values.iloc[:-1]
        mean = float(history.mean())
        if mean <= 0:
            continue
        if latest >= mean * config.yield_collapse_fraction:
            continue

        scope = " / ".join(str(n) for n in (name if isinstance(name, tuple) else (name,)))
        findings.append(
            _breach(
                AgricultureFindingType.YIELD_COLLAPSE,
                metric="yield",
                observed=latest,
                threshold=mean * config.yield_collapse_fraction,
                compared_against=(
                    f"this district-crop's OWN mean yield of {mean:,.4g} over its {len(history)} prior "
                    f"observation(s), times the {config.yield_collapse_fraction:.0%} collapse floor. Never "
                    "another district's - soil, rainfall and crop mix differ between them."
                ),
                columns=[*key, order, DERIVED_YIELD],
                evidence=_evidence(ordered, _grain_columns(roles, config), observations=int(len(values)), historical_mean=mean),
                scope=scope,
            )
        )
    return findings, []


def production_drop(df, roles, config, baseline_profile=None) -> RuleResult:
    """Production falling beyond a margin against the same district-crop's
    previous period."""
    rule = AgricultureFindingType.PRODUCTION_DROP.value
    key = _series_key(roles)
    order = _ordering_column(roles)
    production = roles.get(ColumnRole.PRODUCTION_QUANTITY)
    if not key or not production:
        return [], [{"rule": rule, "reason": "needs a district, a crop and a production column"}]
    if not order:
        return [], [{"rule": rule, "reason": "needs a crop-year column to order a history"}]

    findings: list[AgricultureFinding] = []
    for name, group in df.groupby(key, observed=True):
        ordered = group.sort_values(order)
        values = pd.to_numeric(ordered[production], errors="coerce").dropna()
        if len(values) < 2:
            continue
        previous, latest = float(values.iloc[-2]), float(values.iloc[-1])
        if previous <= 0:
            continue
        drop = (previous - latest) / previous
        if drop <= config.production_drop_fraction:
            continue

        scope = " / ".join(str(n) for n in (name if isinstance(name, tuple) else (name,)))
        findings.append(
            _breach(
                AgricultureFindingType.PRODUCTION_DROP,
                metric="production",
                observed=latest,
                threshold=previous * (1 - config.production_drop_fraction),
                compared_against=(
                    f"this district-crop's own previous period, which recorded {previous:,.4g}. A fall of more "
                    f"than {config.production_drop_fraction:.0%} is reported."
                ),
                columns=[*key, order, production],
                evidence=_evidence(ordered, _grain_columns(roles, config), observations=int(len(values)), previous=previous, fall_fraction=drop),
                scope=scope,
            )
        )
    return findings, []


def rainfall_outside_range(df, roles, config, baseline_profile=None) -> RuleResult:
    """Rainfall far outside the same season's own historical range."""
    rule = AgricultureFindingType.RAINFALL_OUTSIDE_RANGE.value
    rainfall = roles.get(ColumnRole.RAINFALL)
    season = roles.get(ColumnRole.SEASON)
    order = _ordering_column(roles)
    if not rainfall:
        return [], [{"rule": rule, "reason": "needs a rainfall column"}]
    if not season or not order:
        return [], [{"rule": rule, "reason": "needs a season and a crop year - a rainfall figure is only unusual relative to its own season"}]

    findings: list[AgricultureFinding] = []
    for season_name, group in df.groupby(season, observed=True):
        values = pd.to_numeric(group[rainfall], errors="coerce").dropna()
        if len(values) < config.min_history_points + 1:
            continue
        mean, std = float(values.mean()), float(values.std(ddof=0))
        if std <= 0:
            continue
        latest = float(pd.to_numeric(group.sort_values(order)[rainfall], errors="coerce").dropna().iloc[-1])
        deviations = abs(latest - mean) / std
        if deviations <= config.rainfall_sigma:
            continue

        findings.append(
            _breach(
                AgricultureFindingType.RAINFALL_OUTSIDE_RANGE,
                metric="rainfall",
                observed=latest,
                threshold=mean + config.rainfall_sigma * std * (1 if latest > mean else -1),
                compared_against=(
                    f"the {season_name} season's own historical range across {len(values)} observation(s): "
                    f"mean {mean:,.4g}, standard deviation {std:,.4g}. Outside "
                    f"{config.rainfall_sigma:g} standard deviations is reported."
                ),
                columns=[season, order, rainfall],
                evidence=_evidence(group, _grain_columns(roles, config), observations=int(len(values)), mean=mean, std=std, deviations=deviations),
                scope=str(season_name),
            )
        )
    return findings, []


def area_fall(df, roles, config, baseline_profile=None) -> RuleResult:
    """A sharp fall in area cultivated for a district-crop."""
    rule = AgricultureFindingType.AREA_FALL.value
    key = _series_key(roles)
    order = _ordering_column(roles)
    area = roles.get(ColumnRole.AREA_CULTIVATED)
    if not key or not area:
        return [], [{"rule": rule, "reason": "needs a district, a crop and an area column"}]
    if not order:
        return [], [{"rule": rule, "reason": "needs a crop-year column to order a history"}]

    findings: list[AgricultureFinding] = []
    for name, group in df.groupby(key, observed=True):
        ordered = group.sort_values(order)
        values = pd.to_numeric(ordered[area], errors="coerce").dropna()
        if len(values) < 2:
            continue
        previous, latest = float(values.iloc[-2]), float(values.iloc[-1])
        if previous <= 0:
            continue
        fall = (previous - latest) / previous
        if fall <= config.area_fall_fraction:
            continue

        scope = " / ".join(str(n) for n in (name if isinstance(name, tuple) else (name,)))
        findings.append(
            _breach(
                AgricultureFindingType.AREA_FALL,
                metric="area cultivated",
                observed=latest,
                threshold=previous * (1 - config.area_fall_fraction),
                compared_against=(
                    f"this district-crop's own previous period, which recorded {previous:,.4g}. A fall of more "
                    f"than {config.area_fall_fraction:.0%} is reported."
                ),
                columns=[*key, order, area],
                evidence=_evidence(ordered, _grain_columns(roles, config), observations=int(len(values)), previous=previous, fall_fraction=fall),
                scope=scope,
            )
        )
    return findings, []


# ---------------------------------------------------------------------------
# IMPROVEMENT rules. Informational; nothing here blocks a run.
# ---------------------------------------------------------------------------


def below_median_yield(df, roles, config, baseline_profile=None) -> RuleResult:
    """District-crops yielding below the median FOR THAT CROP.

    Per crop, not overall: rice and coconut yields differ by orders of
    magnitude, and a single pooled median would rank every low-yield crop as
    underperforming regardless of how it did.
    """
    rule = AgricultureFindingType.BELOW_MEDIAN_YIELD.value
    district = roles.get(ColumnRole.DISTRICT)
    crop = roles.get(ColumnRole.CROP)
    if not district or not crop or DERIVED_YIELD not in df.columns:
        return [], [{"rule": rule, "reason": "needs a district, a crop and a yield"}]

    findings: list[AgricultureFinding] = []
    for crop_name, crop_group in df.groupby(crop, observed=True):
        by_district = pd.to_numeric(crop_group[DERIVED_YIELD], errors="coerce").groupby(crop_group[district]).mean().dropna()
        if len(by_district) < 3:
            continue
        median = float(by_district.median())
        if median <= 0:
            continue
        floor = median * config.below_median_fraction
        for district_name in sorted(by_district.index):
            observed = float(by_district[district_name])
            if observed >= floor:
                continue
            findings.append(
                _breach(
                    AgricultureFindingType.BELOW_MEDIAN_YIELD,
                    metric="yield",
                    observed=observed,
                    threshold=floor,
                    compared_against=(
                        f"the median yield for {crop_name} across all {len(by_district)} districts in this "
                        f"export (median {median:,.4g}), times the {config.below_median_fraction:.0%} floor. "
                        "Compared per crop, since yields differ by orders of magnitude between crops."
                    ),
                    columns=[district, crop, DERIVED_YIELD],
                    evidence=_evidence(crop_group, _grain_columns(roles, config), observations=int(len(by_district)), crop_median=median),
                    scope=f"{district_name} / {crop_name}",
                )
            )
    return findings, []


def high_yield_variance(df, roles, config, baseline_profile=None) -> RuleResult:
    """A district-crop whose yield swings widely across its own history."""
    rule = AgricultureFindingType.HIGH_YIELD_VARIANCE.value
    key = _series_key(roles)
    if not key or DERIVED_YIELD not in df.columns:
        return [], [{"rule": rule, "reason": "needs a district, a crop and a yield"}]

    findings: list[AgricultureFinding] = []
    for name, group in df.groupby(key, observed=True):
        values = pd.to_numeric(group[DERIVED_YIELD], errors="coerce").dropna()
        if len(values) < config.min_history_points:
            continue
        mean = float(values.mean())
        if mean <= 0:
            continue
        cv = float(values.std(ddof=0)) / mean
        if cv <= config.high_variance_cv:
            continue

        scope = " / ".join(str(n) for n in (name if isinstance(name, tuple) else (name,)))
        findings.append(
            _breach(
                AgricultureFindingType.HIGH_YIELD_VARIANCE,
                metric="yield coefficient of variation",
                observed=cv,
                threshold=config.high_variance_cv,
                compared_against=(
                    f"this district-crop's own {len(values)} observation(s): mean yield {mean:,.4g}, "
                    f"standard deviation {float(values.std(ddof=0)):,.4g}. A coefficient of variation above "
                    f"{config.high_variance_cv:g} is reported as unstable."
                ),
                columns=[*key, DERIVED_YIELD],
                evidence=_evidence(group, _grain_columns(roles, config), observations=int(len(values)), mean=mean, cv=cv),
                scope=scope,
            )
        )
    return findings, []


# ---------------------------------------------------------------------------
# KEY VALUES. Reported plainly, never a finding about anything.
# ---------------------------------------------------------------------------


def season_totals(df, roles, config, baseline_profile=None) -> RuleResult:
    """Totals and summaries. Every field is optional and its absence is
    stated, never rendered as zero."""
    area = roles.get(ColumnRole.AREA_CULTIVATED)
    production = roles.get(ColumnRole.PRODUCTION_QUANTITY)
    rainfall = roles.get(ColumnRole.RAINFALL)
    district = roles.get(ColumnRole.DISTRICT)
    crop = roles.get(ColumnRole.CROP)
    season = roles.get(ColumnRole.SEASON)
    undefined: dict[str, str] = {}

    def _sum(column: str | None, field: str, what: str) -> float | None:
        if not column or column not in df.columns:
            undefined[field] = f"this source has no {what} column"
            return None
        values = pd.to_numeric(df[column], errors="coerce").dropna()
        if values.empty:
            undefined[field] = f"`{column}` has no numeric values"
            return None
        return round(float(values.sum()), 6)

    total_area = _sum(area, "total_area", "area-cultivated")
    total_production = _sum(production, "total_production", "production")

    average_yield = None
    yield_basis = None
    if DERIVED_YIELD in df.columns:
        yields = pd.to_numeric(df[DERIVED_YIELD], errors="coerce").dropna()
        if not yields.empty:
            average_yield = round(float(yields.mean()), 6)
            yield_basis = (
                f"mean of the per-row yield across {len(yields)} row(s) at the "
                f"{' / '.join(_grain_columns(roles, config)) or 'source'} grain"
            )
        else:
            undefined["average_yield"] = "no row had a computable yield"
    else:
        undefined["average_yield"] = "this source has neither a yield column nor an area to derive one from"

    top_crops: list[dict] = []
    if crop and production and {crop, production} <= set(df.columns):
        totals = pd.to_numeric(df[production], errors="coerce").groupby(df[crop]).sum().dropna()
        totals = totals.sort_values(ascending=False).head(config.top_crops)
        top_crops = [{"crop": str(name), "production": round(float(value), 6)} for name, value in totals.items()]

    rainfall_mean = rainfall_min = rainfall_max = None
    if rainfall and rainfall in df.columns:
        values = pd.to_numeric(df[rainfall], errors="coerce").dropna()
        if not values.empty:
            rainfall_mean = round(float(values.mean()), 6)
            rainfall_min = round(float(values.min()), 6)
            rainfall_max = round(float(values.max()), 6)
        else:
            undefined["rainfall_mean"] = f"`{rainfall}` has no numeric values"
    else:
        undefined["rainfall_mean"] = "this source has no rainfall column"

    payload = SeasonTotalsPayload(
        total_area=total_area,
        total_production=total_production,
        average_yield=average_yield,
        yield_basis=yield_basis,
        top_crops=top_crops,
        rainfall_mean=rainfall_mean,
        rainfall_min=rainfall_min,
        rainfall_max=rainfall_max,
        districts=int(df[district].nunique()) if district and district in df.columns else None,
        crops=int(df[crop].nunique()) if crop and crop in df.columns else None,
        seasons=sorted({str(s) for s in df[season].dropna().unique()}) if season and season in df.columns else [],
        undefined=undefined,
    )
    return [
        AgricultureFinding(
            finding_type=AgricultureFindingType.SEASON_TOTALS,
            severity=SEVERITY_OF[AgricultureFindingType.SEASON_TOTALS],
            columns=[c for c in (district, crop, season, area, production, rainfall) if c],
            payload=payload,
            evidence=_evidence(df, _grain_columns(roles, config)),
        )
    ], []


RULES = (
    season_totals,
    yield_collapse,
    production_drop,
    rainfall_outside_range,
    area_fall,
    below_median_yield,
    high_yield_variance,
)
