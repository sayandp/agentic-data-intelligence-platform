"""Part 3: chart type is selected DETERMINISTICALLY BY DATA SHAPE, never by
the LLM. charts.py has no LLM dependency at all - these tests confirm the
four documented mappings and that nothing about narrative generation (a
recommendation that literally names a different chart type) can influence
chart selection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.exploration.engine import ExplorationEngine
from app.exploration.findings import DataQualityContext, FindingType
from app.narrative.charts import build_charts
from app.narrative.models import ChartType


def _quality_context() -> DataQualityContext:
    return DataQualityContext(total_events=0)


def test_trend_finding_produces_a_line_chart():
    n = 200
    df = pd.DataFrame({"order_date": pd.date_range("2022-01-01", periods=n), "amount": np.linspace(0, 100, n)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    trend_findings = [f for f in findings.findings if f.finding_type == FindingType.TREND]
    assert trend_findings

    charts = build_charts(findings, df)
    trend_chart = next(c for c in charts if trend_findings[0].id in c.finding_ids)
    assert trend_chart.chart_type == ChartType.LINE


def test_correlation_finding_produces_a_scatter_chart():
    rng = np.random.default_rng(0)
    n = 200
    a = rng.normal(0, 1, n)
    df = pd.DataFrame({"a": a, "b": a * 2 + rng.normal(0, 0.1, n)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    corr_findings = [f for f in findings.findings if f.finding_type == FindingType.CORRELATION]
    assert corr_findings

    charts = build_charts(findings, df)
    corr_chart = next(c for c in charts if corr_findings[0].id in c.finding_ids)
    assert corr_chart.chart_type == ChartType.SCATTER


def test_categorical_summary_produces_a_bar_chart():
    df = pd.DataFrame({"city": (["New York", "Los Angeles", "Chicago"] * 40)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "categorical"]
    assert summary_findings

    charts = build_charts(findings, df)
    bar_chart = next(c for c in charts if summary_findings[0].id in c.finding_ids)
    assert bar_chart.chart_type == ChartType.BAR


def test_identifier_like_categorical_column_produces_no_bar_chart():
    df = pd.DataFrame({"order_id": [f"ORD{i:05d}" for i in range(200)]})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "categorical"]
    assert summary_findings

    charts = build_charts(findings, df)
    assert not any(summary_findings[0].id in c.finding_ids for c in charts)


def test_numeric_summary_produces_a_histogram():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"amount": rng.normal(50, 5, 200)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "numeric"]
    assert summary_findings

    charts = build_charts(findings, df)
    hist_chart = next(c for c in charts if summary_findings[0].id in c.finding_ids)
    assert hist_chart.chart_type == ChartType.HISTOGRAM


def test_other_finding_types_produce_no_chart():
    """outlier_cluster, cardinality_note, missing_pattern, distribution_shape
    aren't among the four named data shapes - no fifth chart type gets
    invented for them."""
    rng = np.random.default_rng(0)
    n = 200
    values = rng.normal(50, 5, n)
    values[:10] = 1000.0
    df = pd.DataFrame({"amount": values, "uid": [f"id-{i}" for i in range(n)]})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())

    outlier_findings = [f for f in findings.findings if f.finding_type == FindingType.OUTLIER_CLUSTER]
    cardinality_findings = [f for f in findings.findings if f.finding_type == FindingType.CARDINALITY_NOTE]
    assert outlier_findings and cardinality_findings

    charts = build_charts(findings, df)
    charted_ids = {fid for c in charts for fid in c.finding_ids}
    assert outlier_findings[0].id not in charted_ids
    assert cardinality_findings[0].id not in charted_ids


def test_figure_json_is_plain_json_serializable():
    import json

    df = pd.DataFrame({"amount": np.linspace(1, 100, 60)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    charts = build_charts(findings, df)
    assert charts
    for chart in charts:
        json.dumps(chart.figure_json)  # must not raise


def test_chart_selection_ignores_an_llm_recommendation_naming_a_different_chart_type(monkeypatch):
    """The headline guarantee of Part 3: even when the (fake) LLM's
    recommendation text explicitly names a different chart type, chart
    selection is untouched - build_charts takes findings and the repaired
    frame only, never narrative output, and is called independently of
    whatever the Narrative Agent produced."""
    n = 200
    df = pd.DataFrame({"order_date": pd.date_range("2022-01-01", periods=n), "amount": np.linspace(0, 100, n)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    trend_findings = [f for f in findings.findings if f.finding_type == FindingType.TREND]

    from app.narrative.models import GroundedClaim, GroundedClaimsResponse, NarrativeProse, Recommendation
    from app.narrative.pipeline import generate_narrative_report
    from app.narrative.agent import NarrativeAgent
    from tests.fakes import FakeLLMClient

    claim = GroundedClaim(claim_text="amount trends upward over order_date.", finding_ids=[trend_findings[0].id], values=[])
    stage1 = GroundedClaimsResponse(claims=[claim])
    # The model suggests a bar chart for a trend - narrative content the
    # chart-selection code must never see or act on.
    stage2 = NarrativeProse(
        report_text="Amount trends upward over order_date.",
        recommendations=[Recommendation(text="A bar chart would present this trend more clearly.", claim_id="claim-0")],
    )
    agent = NarrativeAgent(llm_client=FakeLLMClient(responses=[stage1, stage2]), sleep=lambda _s: None)

    report = generate_narrative_report(findings, df, agent)

    trend_chart = next(c for c in report.charts if trend_findings[0].id in c.finding_ids)
    assert trend_chart.chart_type == ChartType.LINE  # untouched by the recommendation's suggestion


# ---- dashboard UX pass, Part 4: discrete integer columns get a bar chart ----


def test_low_cardinality_integer_column_produces_a_value_count_bar_chart():
    """review_score: an integer 1-5 rating - a histogram with fractional bin
    edges is the wrong chart for a column that only ever holds 5 distinct
    whole values."""
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"review_score": rng.integers(1, 6, 200)})  # int64, 5 distinct values
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "numeric"]
    assert summary_findings

    charts = build_charts(findings, df)
    chart = next(c for c in charts if summary_findings[0].id in c.finding_ids)
    assert chart.chart_type == ChartType.BAR
    # one bar per distinct value, ordered by value - never by frequency
    assert [pt for pt in chart.figure_json["data"][0]["x"]] == ["1", "2", "3", "4", "5"]


def test_float_column_still_produces_a_histogram_regardless_of_cardinality():
    """The dtype check is the deciding factor, not cardinality alone - a
    float column with few distinct values (e.g. a rounded percentage) is
    still continuous in kind, never a bar-of-value-counts."""
    values = np.array(([1.0, 2.0, 3.0, 4.0, 5.0] * 40), dtype="float64")
    df = pd.DataFrame({"rounded_score": values})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "numeric"]
    assert summary_findings

    charts = build_charts(findings, df)
    chart = next(c for c in charts if summary_findings[0].id in c.finding_ids)
    assert chart.chart_type == ChartType.HISTOGRAM


def test_high_cardinality_integer_column_still_produces_a_histogram():
    """An integer column with many distinct values (a real continuous
    quantity that just happens to be stored as int, e.g. a row count or an
    amount in cents) stays a histogram - the cardinality threshold is what
    keeps this from reclassifying every int column as discrete."""
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"amount_cents": rng.integers(1, 100_000, 200)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())
    summary_findings = [f for f in findings.findings if f.finding_type == FindingType.SUMMARY_STAT and f.payload.kind == "numeric"]
    assert summary_findings

    charts = build_charts(findings, df)
    chart = next(c for c in charts if summary_findings[0].id in c.finding_ids)
    assert chart.chart_type == ChartType.HISTOGRAM


def test_max_charts_cap_is_enforced():
    from app.narrative.config import NarrativeConfig

    rng = np.random.default_rng(0)
    n = 100
    df = pd.DataFrame({f"col{i}": rng.normal(0, 1, n) for i in range(10)})
    findings = ExplorationEngine().run(df, run_id="r", data_quality_context=_quality_context())

    charts = build_charts(findings, df, NarrativeConfig(max_charts=3))
    assert len(charts) <= 3
