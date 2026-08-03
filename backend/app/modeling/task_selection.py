"""Part 2 (data-shape half): task type is chosen from the data's shape
alone, never from the LLM's say-so - exactly the way chart type was chosen
in Phase 5. A datetime column paired with the target means forecast; a
low-cardinality categorical target means classification; any other numeric
target means regression; anything else is unsupported and escalates.

Forecasting additionally needs a genuine time series, not a per-row target -
build_time_series aggregates the target over the chosen datetime column
before any splitter or model ever sees it. Aggregation method is itself
data-shape-driven: a numeric target is summed per period (a measure, e.g.
"total sales"); a non-numeric target (an id/categorical column) is counted
per period (a volume, e.g. "order volume" = count of order_id per period).
"""

from __future__ import annotations

import pandas as pd

from app.exploration.columns import column_kind, datetime_columns
from app.exploration.findings import ColumnKind
from app.modeling.config import ModelingConfig
from app.modeling.models import AggregationMethod, TaskType


def choose_datetime_column(df: pd.DataFrame, target_column: str) -> str | None:
    """The first datetime column in the schema, other than the target
    itself (deterministic column order). Detection itself is NOT this
    module's concern - it happens exactly once, at connector fetch time
    (app/datetime_coercion.py), so a date-like text column is already
    real datetime64 by the time any consumer (including this one) sees the
    DataFrame. There is no module-local heuristic left here to disagree
    with app/profiling.py or app/exploration/columns.py about the same
    column - that disagreement is what Phase 7.5 fixed structurally."""
    candidates = [c for c in datetime_columns(df) if c != target_column]
    return candidates[0] if candidates else None


def select_task_type(df: pd.DataFrame, target_column: str, config: ModelingConfig) -> tuple[TaskType, str | None]:
    """Returns (task_type, datetime_column). datetime_column is set only for
    TaskType.FORECAST - the time axis build_time_series aggregates over."""
    datetime_column = choose_datetime_column(df, target_column)
    if datetime_column is not None:
        return TaskType.FORECAST, datetime_column

    kind = column_kind(df[target_column])
    if kind == ColumnKind.DATETIME:
        return TaskType.UNSUPPORTED, None
    if kind == ColumnKind.CATEGORICAL:
        cardinality = df[target_column].nunique(dropna=True)
        if 1 < cardinality < config.classification_cardinality_ceiling:
            return TaskType.CLASSIFICATION, None
        return TaskType.UNSUPPORTED, None
    # NUMERIC
    return TaskType.REGRESSION, None


def choose_aggregation(df: pd.DataFrame, target_column: str) -> AggregationMethod:
    kind = column_kind(df[target_column])
    return AggregationMethod.SUM if kind == ColumnKind.NUMERIC else AggregationMethod.COUNT


def infer_forecast_frequency(timestamps: pd.Series, config: ModelingConfig) -> str:
    """Chosen from the datetime column's SPAN, not inferred from raw (almost
    always irregular, per-transaction) timestamps via pd.infer_freq, which
    requires an already-regular index to return anything useful. A longer
    span aggregates to a coarser, more business-meaningful granularity."""
    valid = timestamps.dropna()
    if valid.empty:
        return "D"
    span_days = (valid.max() - valid.min()).days
    if span_days >= config.forecast_monthly_span_days:
        return "MS"
    if span_days >= config.forecast_weekly_span_days:
        return "W"
    return "D"


def build_time_series(
    df: pd.DataFrame, target_column: str, datetime_column: str, aggregation: AggregationMethod, config: ModelingConfig
) -> pd.Series:
    """Collapses the raw per-row frame into one row per period - what
    Part 4's splitter and Part 2's forecast models actually train on."""
    working = df[[datetime_column, target_column]].dropna(subset=[datetime_column])
    freq = infer_forecast_frequency(working[datetime_column], config)
    grouped = working.set_index(datetime_column).sort_index().resample(freq)
    if aggregation == AggregationMethod.SUM:
        series = grouped[target_column].sum()
    else:
        series = grouped[target_column].count()
    series.index.name = datetime_column
    series.name = target_column
    return series
