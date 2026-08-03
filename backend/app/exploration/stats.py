"""Part 2.1: per-column summary statistics, and the cardinality_note finding
(a categorical column whose values look more like an identifier than a
category - the same signal app/baseline_sanity.py's cardinality floor
checks for at baseline time, surfaced here as a finding instead of a
sanity-floor rejection)."""

from __future__ import annotations

import pandas as pd

from app.exploration.columns import column_kind
from app.exploration.config import ExplorationConfig
from app.exploration.findings import (
    CardinalityNoteKind,
    CardinalityNotePayload,
    CategoricalSummaryPayload,
    CategoryFrequency,
    ColumnKind,
    DatetimeSummaryPayload,
    Evidence,
    Finding,
    FindingType,
    NumericSummaryPayload,
)


def compute_summary_stats(df: pd.DataFrame, config: ExplorationConfig) -> list[Finding]:
    findings: list[Finding] = []
    for column in df.columns:
        series = df[column]
        kind = column_kind(series)
        count = int(len(series))
        null_count = int(series.isna().sum())
        null_rate = (null_count / count) if count else 0.0
        dtype = str(series.dtype)

        if kind == ColumnKind.NUMERIC:
            payload = _numeric_payload(series, count, null_count, null_rate, dtype)
        elif kind == ColumnKind.DATETIME:
            payload = _datetime_payload(series, count, null_count, null_rate, dtype)
        else:
            payload = _categorical_payload(series, count, null_count, null_rate, dtype, config.categorical_top_n)

        findings.append(
            Finding(
                finding_type=FindingType.SUMMARY_STAT,
                columns=[column],
                payload=payload,
                evidence=Evidence(sample_size=count),
            )
        )
    return findings


def _numeric_payload(series: pd.Series, count: int, null_count: int, null_rate: float, dtype: str) -> NumericSummaryPayload:
    non_null = series.dropna().astype(float)
    if non_null.empty:
        return NumericSummaryPayload(count=count, null_count=null_count, null_rate=null_rate, dtype=dtype)
    return NumericSummaryPayload(
        count=count,
        null_count=null_count,
        null_rate=null_rate,
        dtype=dtype,
        min=float(non_null.min()),
        max=float(non_null.max()),
        mean=float(non_null.mean()),
        median=float(non_null.median()),
        std=float(non_null.std(ddof=0)),
        q1=float(non_null.quantile(0.25)),
        q3=float(non_null.quantile(0.75)),
        skew=float(non_null.skew()) if len(non_null) >= 3 else None,
    )


def _categorical_payload(
    series: pd.Series, count: int, null_count: int, null_rate: float, dtype: str, top_n: int
) -> CategoricalSummaryPayload:
    non_null = series.dropna().astype(str)
    if non_null.empty:
        return CategoricalSummaryPayload(count=count, null_count=null_count, null_rate=null_rate, dtype=dtype, cardinality=0)
    value_counts = non_null.value_counts()  # sorted by count desc, pandas breaks ties by first-seen order
    top = value_counts.head(top_n)
    return CategoricalSummaryPayload(
        count=count,
        null_count=null_count,
        null_rate=null_rate,
        dtype=dtype,
        cardinality=int(non_null.nunique()),
        mode=str(value_counts.index[0]),
        top_frequencies=[CategoryFrequency(value=str(v), count=int(c)) for v, c in top.items()],
    )


def _datetime_payload(series: pd.Series, count: int, null_count: int, null_rate: float, dtype: str) -> DatetimeSummaryPayload:
    non_null = series.dropna()
    if non_null.empty:
        return DatetimeSummaryPayload(count=count, null_count=null_count, null_rate=null_rate, dtype=dtype)
    minimum, maximum = non_null.min(), non_null.max()
    span_days = (maximum - minimum).total_seconds() / 86400.0
    try:
        inferred_frequency = pd.infer_freq(pd.DatetimeIndex(non_null.sort_values().unique()))
    except (ValueError, TypeError):
        inferred_frequency = None
    return DatetimeSummaryPayload(
        count=count,
        null_count=null_count,
        null_rate=null_rate,
        dtype=dtype,
        min=minimum.isoformat(),
        max=maximum.isoformat(),
        span_days=span_days,
        inferred_frequency=inferred_frequency,
    )


def compute_cardinality_notes(df: pd.DataFrame, config: ExplorationConfig) -> list[Finding]:
    findings: list[Finding] = []
    for column in df.columns:
        series = df[column]
        if column_kind(series) != ColumnKind.CATEGORICAL:
            continue
        non_null = series.dropna()
        row_count = int(len(non_null))
        if row_count < config.cardinality_note_min_rows:
            continue
        cardinality = int(non_null.astype(str).nunique())
        unique_ratio = cardinality / row_count
        if unique_ratio < config.cardinality_note_unique_ratio:
            continue
        note = (
            CardinalityNoteKind.NEAR_UNIQUE
            if unique_ratio >= config.cardinality_note_near_unique_ratio
            else CardinalityNoteKind.HIGH_CARDINALITY
        )
        findings.append(
            Finding(
                finding_type=FindingType.CARDINALITY_NOTE,
                columns=[column],
                payload=CardinalityNotePayload(
                    column=column, cardinality=cardinality, row_count=row_count, unique_ratio=unique_ratio, note=note
                ),
                evidence=Evidence(sample_size=row_count),
            )
        )
    return findings
