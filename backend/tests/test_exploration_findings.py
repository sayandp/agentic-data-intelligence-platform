"""Unit tests for the Exploration Agent (Phase 4): deterministic, no LLM,
runs directly against the engine and analysis modules - no DB, no FastAPI.
End-to-end wiring (ingest -> validation -> exploration -> /findings) is
covered separately in tests/test_exploration_pipeline_api.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.exploration.config import ExplorationConfig
from app.exploration.engine import ExplorationEngine
from app.exploration.findings import (
    CardinalityNoteKind,
    ColumnKind,
    DataQualityContext,
    ExplorationFindings,
    FindingType,
    MissingPatternRelationship,
    ModalityHint,
    NormalityIndication,
    ResolutionKind,
    TrendDirection,
)

RNG_SEED = 7


def _empty_quality_context() -> DataQualityContext:
    return DataQualityContext(total_events=0)


def _dates(n: int, freq: str = "D", start: str = "2022-01-01") -> pd.Series:
    return pd.Series(pd.date_range(start, periods=n, freq=freq))


# ---- byte-reproducibility ----


def test_byte_reproducible_across_two_runs():
    rng = np.random.default_rng(RNG_SEED)
    n = 200
    df = pd.DataFrame(
        {
            "order_date": _dates(n),
            "amount": np.linspace(1.0, 500.0, n) + rng.normal(0, 1, n),
            "price": np.linspace(2.0, 1000.0, n) + rng.normal(0, 2, n),
            "city": (["New York", "Los Angeles", "Chicago"] * (n // 3 + 1))[:n],
        }
    )
    engine = ExplorationEngine()
    generated_at = pd.Timestamp("2024-01-01T00:00:00Z").to_pydatetime()

    first = engine.run(df, run_id="r1", data_quality_context=_empty_quality_context(), generated_at=generated_at)
    second = engine.run(df, run_id="r1", data_quality_context=_empty_quality_context(), generated_at=generated_at)

    assert first.model_dump_json() == second.model_dump_json()


# ---- correlation ----


def test_correlation_carries_sample_size_and_p_value():
    rng = np.random.default_rng(RNG_SEED)
    n = 100
    a = rng.normal(0, 1, n)
    df = pd.DataFrame({"a": a, "b": a * 2 + rng.normal(0, 0.1, n)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    corr = [f for f in result.findings if f.finding_type == FindingType.CORRELATION]
    assert corr
    for finding in corr:
        assert finding.evidence.sample_size > 0
        assert finding.evidence.p_value is not None


def test_correlation_suppressed_below_n30_and_recorded_in_skipped():
    rng = np.random.default_rng(RNG_SEED)
    n = 20  # below DEFAULT_CORRELATION_MIN_N
    a = rng.normal(0, 1, n)
    df = pd.DataFrame({"a": a, "b": a * 2 + 0.01})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    assert not [f for f in result.findings if f.finding_type == FindingType.CORRELATION]
    reasons = [s.reason for s in result.skipped if s.column == "a,b"]
    assert reasons and "suppressed" in reasons[0] and "n=20" in reasons[0]


def test_correlation_column_cap_enforced_and_explained():
    rng = np.random.default_rng(RNG_SEED)
    n = 100
    df = pd.DataFrame({f"col{i}": rng.normal(0, i + 1, n) for i in range(60)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    cap_skips = [s for s in result.skipped if "cap" in s.reason]
    assert len(cap_skips) == 10  # 60 numeric columns - the default cap of 50
    for skip in cap_skips:
        assert "50" in skip.reason


def test_pearson_and_spearman_both_reported_for_a_nonlinear_monotonic_pair():
    n = 60
    x = np.linspace(0.1, 1000, n)
    y = np.log(x)  # perfectly monotonic, strongly nonlinear (concave)
    df = pd.DataFrame({"x": x, "y": y})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    corr = [f for f in result.findings if f.finding_type == FindingType.CORRELATION]
    methods = {f.payload.method for f in corr}
    assert "spearman" in [m.value for m in methods]


# ---- outliers ----


def test_outliers_reported_as_a_cluster_not_a_per_row_dump():
    rng = np.random.default_rng(RNG_SEED)
    n = 200
    values = rng.normal(50, 5, n)
    values[:15] = 1000.0  # a clear, distinct outlier cluster
    df = pd.DataFrame({"amount": values})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    outlier_findings = [f for f in result.findings if f.finding_type == FindingType.OUTLIER_CLUSTER]
    assert len(outlier_findings) == 1
    payload = outlier_findings[0].payload
    assert payload.count == 15
    assert len(payload.example_indices) <= ExplorationConfig().outlier_max_examples
    assert len(payload.example_indices) < payload.count  # capped, not a full dump


# ---- trend + seasonality ----


def test_pure_noise_with_datetime_index_produces_no_trend_and_records_insufficient_fit():
    rng = np.random.default_rng(RNG_SEED)
    n = 200
    df = pd.DataFrame({"d": _dates(n), "y": rng.normal(0, 1, n)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    assert not [f for f in result.findings if f.finding_type == FindingType.TREND]
    assert any("insufficient" in s.reason or "R-squared" in s.reason for s in result.skipped)


def test_trend_reported_with_slope_and_r_squared_for_a_clear_linear_series():
    n = 200
    df = pd.DataFrame({"d": _dates(n), "y": np.linspace(0, 100, n)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    trend_findings = [f for f in result.findings if f.finding_type == FindingType.TREND]
    assert len(trend_findings) == 1
    finding = trend_findings[0]
    assert finding.payload.direction == TrendDirection.INCREASING
    assert finding.evidence.r_squared >= 0.99
    assert finding.payload.seasonality_detected is False


def test_seasonality_is_flagged_separately_from_a_directional_trend():
    rng = np.random.default_rng(RNG_SEED)
    n = 365 * 3
    t = np.arange(n)
    y = 10 * np.sin(2 * np.pi * t / 365) + 0.05 * t + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"d": _dates(n), "y": y})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    trend_findings = [f for f in result.findings if f.finding_type == FindingType.TREND]
    assert len(trend_findings) == 1
    payload = trend_findings[0].payload
    assert payload.seasonality_detected is True
    assert payload.seasonality_period is not None
    # the underlying pattern is still a genuine slow rise - seasonality
    # must be an additional flag, not a replacement for direction/slope.
    assert payload.direction == TrendDirection.INCREASING


# ---- distribution shape ----


def test_distribution_shape_reports_normality_skew_and_modality():
    rng = np.random.default_rng(RNG_SEED)
    n = 500
    df = pd.DataFrame({"amount": rng.normal(0, 1, n)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    shape_findings = [f for f in result.findings if f.finding_type == FindingType.DISTRIBUTION_SHAPE]
    assert len(shape_findings) == 1
    payload = shape_findings[0].payload
    assert payload.normality_indication in (NormalityIndication.LIKELY_NORMAL, NormalityIndication.LIKELY_NON_NORMAL)
    assert payload.modality_hint == ModalityHint.UNIMODAL
    assert payload.skew is not None


# ---- missing patterns ----


def test_missing_pattern_detects_nulls_that_co_occur():
    n = 200
    df = pd.DataFrame({"a": range(n), "b": range(n)}).astype(float)
    df.loc[:49, "a"] = np.nan
    df.loc[:49, "b"] = np.nan  # every null in a is also null in b, and vice versa

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    missing_findings = [f for f in result.findings if f.finding_type == FindingType.MISSING_PATTERN]
    assert len(missing_findings) == 1
    payload = missing_findings[0].payload
    assert payload.relationship == MissingPatternRelationship.CO_OCCURRING
    assert payload.support == 50


def test_missing_pattern_detects_one_directional_containment():
    n = 200
    df = pd.DataFrame({"a": range(n), "b": range(n)}).astype(float)
    df.loc[:19, "a"] = np.nan  # a's nulls are a strict subset of b's
    df.loc[:59, "b"] = np.nan

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    missing_findings = [f for f in result.findings if f.finding_type == FindingType.MISSING_PATTERN]
    assert len(missing_findings) == 1
    payload = missing_findings[0].payload
    assert payload.relationship == MissingPatternRelationship.ONE_DIRECTIONAL
    assert payload.narrower_column == "a"
    assert payload.broader_column == "b"
    assert payload.support == 20


# ---- cardinality note ----


def test_cardinality_note_flags_an_identifier_like_column():
    n = 200
    df = pd.DataFrame({"uid": [f"id-{i}" for i in range(n)], "status": (["open", "closed"] * (n // 2))})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    notes = [f for f in result.findings if f.finding_type == FindingType.CARDINALITY_NOTE]
    assert len(notes) == 1
    assert notes[0].columns == ["uid"]
    assert notes[0].payload.note == CardinalityNoteKind.NEAR_UNIQUE


def test_cardinality_note_skipped_below_minimum_rows():
    df = pd.DataFrame({"uid": [f"id-{i}" for i in range(5)]})  # below the min-rows floor

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    assert not [f for f in result.findings if f.finding_type == FindingType.CARDINALITY_NOTE]


# ---- summary stats per column kind ----


def test_summary_stats_cover_numeric_categorical_and_datetime_columns():
    n = 60
    df = pd.DataFrame(
        {
            "amount": np.linspace(1.0, 100.0, n),
            "city": (["New York", "Los Angeles", "Chicago"] * 20),
            "order_date": _dates(n),
        }
    )

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    stats = {f.columns[0]: f.payload for f in result.findings if f.finding_type == FindingType.SUMMARY_STAT}
    assert stats["amount"].kind == ColumnKind.NUMERIC
    assert stats["amount"].mean is not None and stats["amount"].q1 is not None
    assert stats["city"].kind == ColumnKind.CATEGORICAL
    assert stats["city"].cardinality == 3
    assert stats["city"].top_frequencies
    assert stats["order_date"].kind == ColumnKind.DATETIME
    assert stats["order_date"].inferred_frequency == "D"


# ---- scale guard: row cap with seeded sampling ----


def test_row_cap_triggers_seeded_sampling_recorded_on_evidence():
    rng = np.random.default_rng(RNG_SEED)
    n = 120
    a = rng.normal(0, 1, n)
    df = pd.DataFrame({"a": a, "b": a * 2 + rng.normal(0, 0.1, n)})
    config = ExplorationConfig(row_cap=50, row_cap_seed=123)

    result = ExplorationEngine(config).run(df, run_id="r", data_quality_context=_empty_quality_context())

    corr = [f for f in result.findings if f.finding_type == FindingType.CORRELATION]
    assert corr
    for finding in corr:
        assert finding.evidence.sampling_seed == 123
        assert finding.evidence.sample_size <= 50


def test_row_cap_not_recorded_when_cap_not_hit():
    rng = np.random.default_rng(RNG_SEED)
    n = 40
    a = rng.normal(0, 1, n)
    df = pd.DataFrame({"a": a, "b": a * 2 + rng.normal(0, 0.1, n)})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=_empty_quality_context())

    corr = [f for f in result.findings if f.finding_type == FindingType.CORRELATION]
    assert corr
    assert all(f.evidence.sampling_seed is None for f in corr)


# ---- data_quality_context is carried, not interpreted ----


def test_data_quality_context_is_carried_through_unmodified():
    dqc = DataQualityContext(
        total_events=3,
        resolution_counts={ResolutionKind.AUTO_FIXED: 2, ResolutionKind.AUTO_FIX_REVERTED: 1},
        active_baseline_provisional=True,
    )
    df = pd.DataFrame({"amount": [1.0, 2.0, 3.0]})

    result = ExplorationEngine().run(df, run_id="r", data_quality_context=dqc)

    assert result.data_quality_context == dqc


# ---- schema hygiene: closed enums, no causal vocabulary ----

_CAUSAL_WORDS = ("driver", "impact", "effect", "cause", "caused", "causes", "causing", "because")


def test_no_causal_vocabulary_anywhere_in_the_schema():
    schema_text = ExplorationFindings.model_json_schema()

    def _walk(node) -> list[str]:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and any(word in key.lower() for word in _CAUSAL_WORDS):
                    found.append(key)
                found += _walk(value)
        elif isinstance(node, list):
            for item in node:
                found += _walk(item)
        elif isinstance(node, str):
            if any(word in node.lower() for word in _CAUSAL_WORDS):
                found.append(node)
        return found

    hits = _walk(schema_text)
    assert hits == []


def test_finding_type_is_a_closed_enum():
    with pytest.raises(ValueError):
        FindingType("not_a_real_finding_type")
