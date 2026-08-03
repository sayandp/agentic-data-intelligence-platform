"""Part 3: deterministic leakage prevention, run before training and never
skippable by a model family's own feature-selection. Every excluded
feature is RECORDED with why, not merely dropped silently - the exclusion
list is a report artifact (Part 3: "feature exclusions are reported in the
model output"), not incidental logging.

Four exclusion classes, checked in order for every feature candidate:
  1. identifier - flagged by Exploration findings as near-unique, or high
     cardinality approaching row count on its own.
  2. constant, or null above threshold.
  3. near-perfectly related to the target - correlation for a numeric
     target, exact-match agreement rate for a categorical one (this is what
     catches "a copy of the target under another name" regardless of task
     type).
  4. a timestamp at or after the chosen time-axis column's own value on any
     row - future information relative to the event being modeled.
"""

from __future__ import annotations

import pandas as pd

from app.exploration.findings import CardinalityNoteKind, ExplorationFindings, FindingType
from app.modeling.config import ModelingConfig
from app.modeling.models import ExcludedFeature


def _near_unique_columns_from_findings(findings: ExplorationFindings | None) -> set[str]:
    if findings is None:
        return set()
    result: set[str] = set()
    for finding in findings.findings:
        if finding.finding_type != FindingType.CARDINALITY_NOTE:
            continue
        payload = finding.payload
        if getattr(payload, "note", None) == CardinalityNoteKind.NEAR_UNIQUE:
            result.add(payload.column)
    return result


def select_features(
    df: pd.DataFrame,
    target_column: str,
    datetime_column: str | None,
    findings: ExplorationFindings | None,
    config: ModelingConfig,
) -> tuple[list[str], list[ExcludedFeature]]:
    """Returns (kept_feature_columns, excluded_with_reasons). Never mutates
    df. datetime_column, when set, is the chosen time axis (Part 2) and is
    excluded as a feature by construction - it's the index, not an input."""
    identifier_columns = _near_unique_columns_from_findings(findings)
    target_series = df[target_column]
    target_numeric = pd.api.types.is_numeric_dtype(target_series)
    row_count = len(df)

    kept: list[str] = []
    excluded: list[ExcludedFeature] = []

    for column in df.columns:
        if column in (target_column, datetime_column):
            continue
        reason = _exclusion_reason(
            df[column], target_series, target_numeric, datetime_column, df, identifier_columns, row_count, config, column
        )
        if reason:
            excluded.append(ExcludedFeature(column=column, reason=reason))
        else:
            kept.append(column)

    return kept, excluded


def _exclusion_reason(
    series: pd.Series,
    target_series: pd.Series,
    target_numeric: bool,
    datetime_column: str | None,
    df: pd.DataFrame,
    identifier_columns: set[str],
    row_count: int,
    config: ModelingConfig,
    column: str,
) -> str | None:
    non_null = series.dropna()

    if column in identifier_columns:
        return "flagged by Exploration findings as near-unique (identifier-like)"

    # The cardinality-ratio identifier check only makes sense for
    # categorical/object columns (a UUID or order-id string with one
    # distinct value per row IS an identifier). A numeric or datetime
    # column with a distinct value on nearly every row is normal - a
    # continuous measure (price, latitude) or a timestamp is expected to
    # vary almost row-by-row without being an identifier at all.
    is_categorical_like = not pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_datetime64_any_dtype(series)
    if is_categorical_like and row_count > 0:
        unique_ratio = non_null.nunique() / row_count
        if unique_ratio >= config.identifier_cardinality_ratio:
            return f"high cardinality ({non_null.nunique()}/{row_count} rows, ratio {unique_ratio:.2f}) - looks like an identifier, not a feature"

    if non_null.nunique() <= 1:
        return "constant column (zero or one distinct non-null value)"

    null_rate = series.isna().mean()
    if null_rate > config.null_rate_threshold:
        return f"null rate {null_rate:.1%} exceeds threshold {config.null_rate_threshold:.0%}"

    common = series.notna() & target_series.notna()
    if common.sum() > 0:
        if target_numeric and pd.api.types.is_numeric_dtype(series):
            corr = series[common].corr(target_series[common])
            if corr is not None and abs(corr) > config.leakage_correlation_threshold:
                return f"near-perfectly correlated with target (|r|={abs(corr):.4f} > {config.leakage_correlation_threshold})"
        else:
            agreement = (series[common] == target_series[common]).mean()
            if agreement > config.leakage_correlation_threshold:
                return f"near-perfect agreement with target ({agreement:.1%} of rows match) - likely a copy or derivative of the target"

    if datetime_column is not None and pd.api.types.is_datetime64_any_dtype(series):
        reference = df[datetime_column]
        at_or_after = (series >= reference) & series.notna() & reference.notna()
        if at_or_after.any():
            return "timestamp at or after the reference time column's own value on at least one row - future information relative to the event being modeled"

    return None
