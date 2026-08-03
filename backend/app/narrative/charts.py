"""Part 3: chart generation. Chart TYPE is selected deterministically by
data shape - never by the LLM, which has no involvement in this module at
all (not in scope, not in the function signatures, not on the call path).
Four mappings, exactly as specified in the phase brief:

  datetime-indexed numeric  -> line       (TrendPayload)
  categorical comparison    -> bar        (CategoricalSummaryPayload)
  single numeric column     -> histogram  (NumericSummaryPayload)
  numeric pair correlation  -> scatter    (CorrelationPayload)

Every other finding type produces no chart - these are the four shapes
named in the brief, not an invitation to invent a fifth. Line/scatter/
histogram pull real per-row values from the repaired frame (a Finding's
payload only ever carries summary statistics, never raw arrays); bar
reuses the finding's own top_frequencies, which IS already the exact
aggregation a bar chart needs.

Dashboard UX pass, Part 4: a NumericSummaryPayload column doesn't always
mean "continuous" - an integer-dtype column with a small, fixed number of
distinct values (a 1-5 star rating, a 1-10 satisfaction score) is discrete/
ordinal, and a histogram with fractional bin edges misrepresents it (one
bar could span "2.3 to 2.7" for a column that only ever holds 1, 2, 3, 4, 5).
_numeric_chart below decides bar-of-value-counts vs. histogram from the
real column data (dtype + cardinality), same as the identifier-skip in
_bar_chart below decides whether a categorical bar chart is worth drawing -
never from the LLM, never from the Finding's own dtype STRING alone.
"""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go

from app.exploration.findings import (
    CategoricalSummaryPayload,
    CorrelationPayload,
    ExplorationFindings,
    Finding,
    FindingType,
    NumericSummaryPayload,
    TrendPayload,
)
from app.narrative.config import NarrativeConfig
from app.narrative.models import ChartRef, ChartType


def build_charts(findings: ExplorationFindings, repaired_df: pd.DataFrame, config: NarrativeConfig | None = None) -> list[ChartRef]:
    config = config or NarrativeConfig()
    charts: list[ChartRef] = []
    for finding in findings.findings:
        if len(charts) >= config.max_charts:
            break
        chart = _chart_for_finding(finding, repaired_df, config)
        if chart is not None:
            charts.append(chart)
    return charts


def _chart_for_finding(finding: Finding, df: pd.DataFrame, config: NarrativeConfig) -> ChartRef | None:
    payload = finding.payload
    if finding.finding_type == FindingType.TREND and isinstance(payload, TrendPayload):
        return _line_chart(finding, payload, df)
    if finding.finding_type == FindingType.CORRELATION and isinstance(payload, CorrelationPayload):
        return _scatter_chart(finding, payload, df)
    if finding.finding_type == FindingType.SUMMARY_STAT and isinstance(payload, CategoricalSummaryPayload):
        return _bar_chart(finding, payload)
    if finding.finding_type == FindingType.SUMMARY_STAT and isinstance(payload, NumericSummaryPayload):
        return _numeric_chart(finding, payload, df, config)
    return None


def _figure_json(fig: go.Figure) -> dict:
    # fig.to_plotly_json() can still hold numpy/pandas objects (e.g. a
    # datetime64 Series) that a plain json.dumps/DB JSON column can't take
    # as-is; round-tripping through Plotly's own JSON encoder (to_json)
    # guarantees a plain-JSON-compatible dict.
    return json.loads(fig.to_json())


def _line_chart(finding: Finding, payload: TrendPayload, df: pd.DataFrame) -> ChartRef | None:
    if payload.datetime_column not in df.columns or payload.numeric_column not in df.columns:
        return None
    subset = df[[payload.datetime_column, payload.numeric_column]].dropna().sort_values(payload.datetime_column)
    if subset.empty:
        return None
    title = f"{payload.numeric_column} over {payload.datetime_column}"
    fig = go.Figure(go.Scatter(x=subset[payload.datetime_column], y=subset[payload.numeric_column], mode="lines"))
    fig.update_layout(title=title, xaxis_title=payload.datetime_column, yaxis_title=payload.numeric_column)
    return ChartRef(chart_id=f"chart-{finding.id}", chart_type=ChartType.LINE, finding_ids=[finding.id], title=title, figure_json=_figure_json(fig))


def _scatter_chart(finding: Finding, payload: CorrelationPayload, df: pd.DataFrame) -> ChartRef | None:
    if payload.column_a not in df.columns or payload.column_b not in df.columns:
        return None
    subset = df[[payload.column_a, payload.column_b]].dropna()
    if subset.empty:
        return None
    title = f"{payload.column_a} vs {payload.column_b}"
    fig = go.Figure(go.Scatter(x=subset[payload.column_a], y=subset[payload.column_b], mode="markers"))
    fig.update_layout(title=title, xaxis_title=payload.column_a, yaxis_title=payload.column_b)
    return ChartRef(
        chart_id=f"chart-{finding.id}", chart_type=ChartType.SCATTER, finding_ids=[finding.id], title=title, figure_json=_figure_json(fig)
    )


# A "category frequencies" bar chart is meaningless once the column is
# (near) an identifier - every bar reads ~1, indistinguishable from noise,
# and misleads a reader into thinking it's a real distribution. This is the
# exact same signal app/exploration/stats.py::compute_cardinality_notes
# already uses to flag a column HIGH_CARDINALITY (its
# DEFAULT_CARDINALITY_NOTE_UNIQUE_RATIO), applied here to whether a bar
# chart is worth drawing at all, not just whether to note it.
IDENTIFIER_LIKE_UNIQUE_RATIO = 0.9


def _bar_chart(finding: Finding, payload: CategoricalSummaryPayload) -> ChartRef | None:
    if not payload.top_frequencies:
        return None
    non_null_count = payload.count - payload.null_count
    if non_null_count > 0 and payload.cardinality / non_null_count >= IDENTIFIER_LIKE_UNIQUE_RATIO:
        return None
    column = finding.columns[0] if finding.columns else "value"
    title = f"{column} - category frequencies"
    fig = go.Figure(go.Bar(x=[freq.value for freq in payload.top_frequencies], y=[freq.count for freq in payload.top_frequencies]))
    fig.update_layout(title=title, xaxis_title=column, yaxis_title="count")
    return ChartRef(chart_id=f"chart-{finding.id}", chart_type=ChartType.BAR, finding_ids=[finding.id], title=title, figure_json=_figure_json(fig))


def _numeric_chart(finding: Finding, payload: NumericSummaryPayload, df: pd.DataFrame, config: NarrativeConfig) -> ChartRef | None:
    column = finding.columns[0] if finding.columns else None
    if column is None or column not in df.columns:
        return None
    series = df[column]
    values = series.dropna()
    if values.empty:
        return None
    # Integer dtype + few enough distinct values => a discrete/ordinal
    # quantity (a 1-5 rating, a 1-10 score), not a continuous one - see this
    # module's docstring. A float column NEVER takes this path regardless of
    # cardinality (2.0/3.0/4.0 could still be meaningfully "between" in a way
    # an integer column's values structurally can't be).
    if pd.api.types.is_integer_dtype(series) and values.nunique() <= config.discrete_bar_max_cardinality:
        return _discrete_value_count_bar_chart(finding, values, column)
    return _histogram_chart(finding, values, column)


def _discrete_value_count_bar_chart(finding: Finding, values: pd.Series, column: str) -> ChartRef:
    counts = values.value_counts().sort_index()  # one bar per distinct value, ordered BY VALUE, not by frequency
    title = f"{column} - value counts"
    fig = go.Figure(go.Bar(x=[str(v) for v in counts.index], y=counts.to_numpy()))
    fig.update_layout(title=title, xaxis_title=column, yaxis_title="count")
    return ChartRef(chart_id=f"chart-{finding.id}", chart_type=ChartType.BAR, finding_ids=[finding.id], title=title, figure_json=_figure_json(fig))


def _histogram_chart(finding: Finding, values: pd.Series, column: str) -> ChartRef:
    title = f"{column} - distribution"
    fig = go.Figure(go.Histogram(x=values))
    fig.update_layout(title=title, xaxis_title=column, yaxis_title="count")
    return ChartRef(
        chart_id=f"chart-{finding.id}", chart_type=ChartType.HISTOGRAM, finding_ids=[finding.id], title=title, figure_json=_figure_json(fig)
    )
