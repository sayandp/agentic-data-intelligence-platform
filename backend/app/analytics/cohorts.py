"""Cohort retention, repeat/churn behaviour, and historical CLV.

Three analyses that all rest on entity + date (+ value for CLV), grouped in
one module because they share the same window arithmetic and would otherwise
drift apart on what "a period" means.

NO LLM. NO CAUSAL VOCABULARY - retention DECLINES across periods; nothing
here causes it to.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.applicability import AnalysisKind
from app.analytics.entity_features import build_entity_features
from app.analytics.findings import (
    AnalysisEvidence,
    AnalysisFinding,
    AnalysisFindingType,
    BusinessAnalysisResult,
    LifetimeValuePayload,
    RepeatBehaviourPayload,
    RetentionMatrixPayload,
)
from app.analytics.roles import ColumnRole, RoleDetection
from app.analytics.rfm import score_entities

#: Cohort period. Month is the default because most business questions are
#: asked monthly; week and day exist for shorter-lived data.
DEFAULT_GRANULARITY = "month"
_PERIOD_FREQ = {"month": "M", "week": "W", "day": "D"}

#: Cap the triangle. A 500-cohort matrix is not a heatmap anyone reads, and
#: the cap is always recorded in evidence.
MAX_COHORTS = 36
MAX_PERIODS = 24

#: How long without an event before an entity is FLAGGED inactive.
#:
#: This is a CHOICE, not a discovered fact - there is no such thing as the
#: true churn window for arbitrary business data. It is defaulted, always
#: stated in the output, and overridable. The brief was explicit: do not
#: invent a churn definition silently.
DEFAULT_INACTIVITY_WINDOW_DAYS = 90


def _period_index(series: pd.Series, granularity: str) -> pd.Series:
    return series.dt.to_period(_PERIOD_FREQ[granularity])


def _require(detection: RoleDetection, *roles: ColumnRole) -> tuple[dict, list[str]]:
    names = {
        ColumnRole.ENTITY_ID: "a customer-like identifier",
        ColumnRole.EVENT_DATE: "a date column",
        ColumnRole.MONETARY: "a non-negative monetary column",
    }
    resolved, missing = {}, []
    for role in roles:
        found = detection.best(role)
        if found is None:
            missing.append(names[role])
        else:
            resolved[role] = found
    return resolved, missing


def run_cohort_retention(
    df: pd.DataFrame,
    detection: RoleDetection,
    granularity: str = DEFAULT_GRANULARITY,
) -> BusinessAnalysisResult:
    analysis = AnalysisKind.COHORT_RETENTION.value
    if granularity not in _PERIOD_FREQ:
        raise ValueError(f"granularity must be one of {sorted(_PERIOD_FREQ)}, got {granularity!r}")

    resolved, missing = _require(detection, ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE)
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    entity_col = resolved[ColumnRole.ENTITY_ID].column
    date_col = resolved[ColumnRole.EVENT_DATE].column
    frame = df[[entity_col, date_col]].dropna()
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame = frame.dropna(subset=[date_col])
    if frame.empty:
        return BusinessAnalysisResult(analysis=analysis, ran=False, not_run_reason="no rows with both an entity and a usable date")

    frame["period"] = _period_index(frame[date_col], granularity)
    first_period = frame.groupby(entity_col, observed=True)["period"].transform("min")
    frame["cohort"] = first_period
    frame["offset"] = (frame["period"] - frame["cohort"]).apply(lambda x: int(x.n))

    cohort_sizes = frame.groupby("cohort", observed=True)[entity_col].nunique().sort_index()
    if len(cohort_sizes) < 2:
        return BusinessAnalysisResult(
            analysis=analysis,
            ran=False,
            not_run_reason=(
                f"only {len(cohort_sizes)} {granularity} cohort(s) in this data - "
                "retention needs at least two to compare"
            ),
        )

    cohorts = list(cohort_sizes.index)[-MAX_COHORTS:]
    counts = (
        frame[frame["cohort"].isin(cohorts)]
        .groupby(["cohort", "offset"], observed=True)[entity_col]
        .nunique()
        .unstack(fill_value=0)
        .sort_index()
    )
    offsets = [o for o in sorted(counts.columns) if o < MAX_PERIODS]

    matrix: list[list[float | None]] = []
    for cohort in cohorts:
        size = int(cohort_sizes[cohort])
        row: list[float | None] = []
        for offset in offsets:
            # None, not 0.0: a cohort acquired last month has NO data for
            # offset 6 yet. Reporting that as 0% retention would be a
            # fabricated number, not a measured one.
            cohort_period_end = cohort + offset
            if cohort_period_end > frame["period"].max():
                row.append(None)
                continue
            value = int(counts.loc[cohort, offset]) if offset in counts.columns else 0
            row.append(round(value / size, 6) if size else None)
        matrix.append(row)

    mean_curve: list[float | None] = []
    for i, _ in enumerate(offsets):
        present = [r[i] for r in matrix if r[i] is not None]
        mean_curve.append(round(sum(present) / len(present), 6) if present else None)

    finding = AnalysisFinding(
        analysis=analysis,
        finding_type=AnalysisFindingType.RETENTION_MATRIX,
        columns=[entity_col, date_col],
        payload=RetentionMatrixPayload(
            granularity=granularity,
            cohort_labels=[str(c) for c in cohorts],
            cohort_sizes=[int(cohort_sizes[c]) for c in cohorts],
            periods_since_acquisition=offsets,
            retained_share=matrix,
            mean_retained_share=mean_curve,
        ),
        evidence=AnalysisEvidence(
            sample_size=int(len(frame)),
            entity_count=int(frame[entity_col].nunique()),
            parameters={
                "granularity": granularity,
                "cohort_cap": MAX_COHORTS,
                "period_cap": MAX_PERIODS,
                "cohorts_truncated": len(cohort_sizes) > MAX_COHORTS,
            },
        ),
    )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=[finding],
        parameters={
            "entity_column": entity_col,
            "date_column": date_col,
            "granularity": granularity,
            "cohort_count": len(cohorts),
        },
    )


def run_retention_churn(
    df: pd.DataFrame,
    detection: RoleDetection,
    inactivity_window_days: int = DEFAULT_INACTIVITY_WINDOW_DAYS,
) -> BusinessAnalysisResult:
    analysis = AnalysisKind.RETENTION_CHURN.value
    # Argument validation precedes role resolution: a nonsensical window is
    # a programming error whatever the data looks like, and reporting it as
    # "no entity column" would point the reader at the wrong thing.
    if inactivity_window_days <= 0:
        raise ValueError(f"inactivity_window_days must be positive, got {inactivity_window_days}")

    resolved, missing = _require(detection, ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE)
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    entity_col = resolved[ColumnRole.ENTITY_ID].column
    date_col = resolved[ColumnRole.EVENT_DATE].column
    monetary = detection.best(ColumnRole.MONETARY)

    features = build_entity_features(df, entity_col, date_col, monetary.column if monetary else None)
    if features is None or features.entity_count == 0:
        return BusinessAnalysisResult(analysis=analysis, ran=False, not_run_reason="no rows with both an entity and a usable date")

    frame = features.frame
    entity_count = int(len(frame))
    repeat = frame[frame["frequency"] >= 2]
    inactive = frame[frame["recency_days"] > inactivity_window_days]

    # Gap distribution across entities with at least two events.
    events = df[[entity_col, date_col]].dropna()
    events[date_col] = pd.to_datetime(events[date_col], errors="coerce")
    events = events.dropna(subset=[date_col]).sort_values([entity_col, date_col])
    gaps = events.groupby(entity_col, observed=True)[date_col].diff().dt.days.dropna()

    finding = AnalysisFinding(
        analysis=analysis,
        finding_type=AnalysisFindingType.REPEAT_BEHAVIOUR,
        columns=[entity_col, date_col],
        payload=RepeatBehaviourPayload(
            entity_count=entity_count,
            repeat_entity_count=int(len(repeat)),
            repeat_rate=round(len(repeat) / entity_count, 6) if entity_count else 0.0,
            inactivity_window_days=inactivity_window_days,
            inactive_entity_count=int(len(inactive)),
            inactive_share=round(len(inactive) / entity_count, 6) if entity_count else 0.0,
            gap_days_median=round(float(gaps.median()), 3) if len(gaps) else None,
            gap_days_p25=round(float(gaps.quantile(0.25)), 3) if len(gaps) else None,
            gap_days_p75=round(float(gaps.quantile(0.75)), 3) if len(gaps) else None,
            observation_end=str(features.observation_end),
        ),
        evidence=AnalysisEvidence(
            sample_size=int(len(df)),
            entity_count=entity_count,
            parameters={
                "inactivity_window_days": inactivity_window_days,
                "window_is_a_configured_choice": True,
                "observation_end": str(features.observation_end),
            },
        ),
    )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=[finding],
        parameters={
            "entity_column": entity_col,
            "date_column": date_col,
            "inactivity_window_days": inactivity_window_days,
            "observation_end": str(features.observation_end),
        },
    )


def run_historical_clv(
    df: pd.DataFrame,
    detection: RoleDetection,
    margin_rate: float | None = None,
) -> BusinessAnalysisResult:
    """HISTORICAL, descriptive value per entity, reported per RFM segment.

    This is what customers HAVE been worth over the observed window. It is
    not a forecast and must never be presented as one - hence
    `historical_value_per_entity` and an explicit `value_basis`.
    """
    analysis = AnalysisKind.HISTORICAL_CLV.value
    if margin_rate is not None and not (0 < margin_rate <= 1):
        raise ValueError(f"margin_rate must be in (0, 1], got {margin_rate}")

    resolved, missing = _require(detection, ColumnRole.ENTITY_ID, ColumnRole.EVENT_DATE, ColumnRole.MONETARY)
    if missing:
        return BusinessAnalysisResult(
            analysis=analysis, ran=False, not_run_reason=f"needs {', '.join(missing)}; not detected"
        )

    entity_col = resolved[ColumnRole.ENTITY_ID].column
    date_col = resolved[ColumnRole.EVENT_DATE].column
    value_col = resolved[ColumnRole.MONETARY].column

    features = build_entity_features(df, entity_col, date_col, value_col)
    if features is None or features.entity_count == 0:
        return BusinessAnalysisResult(analysis=analysis, ran=False, not_run_reason="no rows with both an entity and a usable date")

    scored = score_entities(features)
    # No margin supplied -> report revenue and SAY so. Assuming a margin
    # would silently turn revenue into profit, which is the single most
    # misleading thing this analysis could do.
    basis = "revenue" if margin_rate is None else "margin"
    multiplier = 1.0 if margin_rate is None else margin_rate

    findings: list[AnalysisFinding] = []
    counts = scored["segment"].value_counts()
    for segment in sorted(counts.index, key=lambda s: (-int(counts[s]), str(s))):
        members = scored[scored["segment"] == segment]
        orders = float(members["frequency"].sum())
        value = float(members["monetary"].sum()) * multiplier
        aov = value / orders if orders else 0.0
        frequency = orders / len(members) if len(members) else 0.0
        lifespan = float(members["lifespan_days"].mean())
        findings.append(
            AnalysisFinding(
                analysis=analysis,
                finding_type=AnalysisFindingType.LIFETIME_VALUE,
                columns=[entity_col, date_col, value_col],
                payload=LifetimeValuePayload(
                    segment=str(segment),
                    entity_count=int(len(members)),
                    average_order_value=round(aov, 6),
                    purchase_frequency=round(frequency, 6),
                    observed_lifespan_days=round(lifespan, 3),
                    historical_value_per_entity=round(aov * frequency, 6),
                    value_basis=basis,
                    margin_rate=margin_rate,
                ),
                evidence=AnalysisEvidence(
                    sample_size=int(len(df)),
                    entity_count=int(len(scored)),
                    total_value=round(float(scored["monetary"].sum()) * multiplier, 6),
                    parameters={
                        "value_basis": basis,
                        "margin_rate": margin_rate,
                        "descriptive_not_predictive": True,
                        "observation_end": str(features.observation_end),
                    },
                ),
            )
        )

    return BusinessAnalysisResult(
        analysis=analysis,
        ran=True,
        findings=findings,
        parameters={
            "entity_column": entity_col,
            "date_column": date_col,
            "value_column": value_col,
            "value_basis": basis,
            "margin_rate": margin_rate,
            "descriptive_not_predictive": True,
            "observation_end": str(features.observation_end),
        },
    )
