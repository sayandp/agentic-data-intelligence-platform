"""Phase 7 Parts 2/3/4/5 - pure unit tests against app/modeling/*, with no
DB, no HTTP, no LLM, and no real model fitting: leakage prevention, split
selection, baseline predictors, and deterministic task-type/aggregation
selection, exercised directly against synthetic frames.
"""

from __future__ import annotations

import pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold, TimeSeriesSplit

from app.exploration.findings import CardinalityNoteKind, CardinalityNotePayload, DataQualityContext, Evidence, ExplorationFindings, Finding, FindingType
from app.modeling.baselines import majority_class_predict, mean_predict, naive_forecast, seasonal_naive_forecast
from app.modeling.config import ModelingConfig
from app.modeling.leakage import select_features
from app.modeling.models import AggregationMethod, SplitStrategy, TaskType
from app.modeling.splitting import get_splitter, split_strategy_for_task
from app.modeling.task_selection import build_time_series, choose_aggregation, infer_forecast_frequency, select_task_type


def _findings_with_near_unique(column: str, row_count: int) -> ExplorationFindings:
    finding = Finding(
        id="cardinality_note-0",
        finding_type=FindingType.CARDINALITY_NOTE,
        columns=[column],
        payload=CardinalityNotePayload(column=column, cardinality=row_count, row_count=row_count, unique_ratio=1.0, note=CardinalityNoteKind.NEAR_UNIQUE),
        evidence=Evidence(sample_size=row_count),
    )
    return ExplorationFindings(run_id="run-1", findings=[finding], data_quality_context=DataQualityContext(total_events=0))


# ---------------------------------------------------------------------------
# Part 3: leakage prevention.
# ---------------------------------------------------------------------------


def test_injected_target_copy_is_dropped_and_recorded():
    df = pd.DataFrame({"target": [float(i) for i in range(150)], "feature": [float(i) * 2 for i in range(150)]})
    df["target_copy"] = df["target"]
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "target_copy" not in kept
    reasons = {e.column: e.reason for e in excluded}
    assert "target_copy" in reasons
    assert "correlat" in reasons["target_copy"].lower()


def test_injected_categorical_target_copy_is_dropped_via_agreement_rate():
    df = pd.DataFrame({"target": ["a" if i % 2 == 0 else "b" for i in range(150)], "feature": [i % 3 for i in range(150)]})
    df["target_copy"] = df["target"]
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "target_copy" not in kept
    assert any("agreement" in e.reason for e in excluded if e.column == "target_copy")


def test_identifier_column_dropped_via_high_cardinality_ratio():
    df = pd.DataFrame({"target": [i % 2 for i in range(150)], "id": [f"row-{i}" for i in range(150)]})
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "id" not in kept
    assert any("identifier" in e.reason for e in excluded if e.column == "id")


def test_identifier_flagged_by_exploration_findings_dropped_even_at_lower_cardinality():
    df = pd.DataFrame({"target": [i % 2 for i in range(150)], "code": [i % 80 for i in range(150)]})
    findings = _findings_with_near_unique("code", 150)
    kept, excluded = select_features(df, "target", None, findings, ModelingConfig())
    assert "code" not in kept
    assert any("Exploration findings" in e.reason for e in excluded if e.column == "code")


def test_constant_column_dropped():
    df = pd.DataFrame({"target": [i % 2 for i in range(150)], "const": [1] * 150})
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "const" not in kept
    assert any("constant" in e.reason for e in excluded if e.column == "const")


def test_high_null_column_dropped():
    df = pd.DataFrame({"target": [i % 2 for i in range(150)], "sparse": [None if i % 3 != 0 else i for i in range(150)]})
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "sparse" not in kept
    assert any("null rate" in e.reason for e in excluded if e.column == "sparse")


def test_future_timestamp_dropped_for_time_indexed_task():
    event_time = pd.date_range("2024-01-01", periods=150, freq="D")
    later_time = event_time + pd.Timedelta(days=5)
    df = pd.DataFrame({"target": range(150), "event_time": event_time, "later_time": later_time})
    kept, excluded = select_features(df, "target", "event_time", None, ModelingConfig())
    assert "later_time" not in kept
    assert any("future information" in e.reason for e in excluded if e.column == "later_time")


def test_normal_informative_feature_kept():
    df = pd.DataFrame({"target": [i % 2 for i in range(150)], "feature": [i % 3 for i in range(150)]})
    kept, excluded = select_features(df, "target", None, None, ModelingConfig())
    assert "feature" in kept
    assert excluded == [] or all(e.column != "feature" for e in excluded)


# ---------------------------------------------------------------------------
# Part 4: splitting is chosen by task type; time data structurally cannot
# receive a shuffled split.
# ---------------------------------------------------------------------------


def test_forecast_splitter_is_timeseriessplit_with_no_shuffle_option():
    splitter = get_splitter(TaskType.FORECAST, ModelingConfig())
    assert isinstance(splitter, TimeSeriesSplit)
    # Asserted on the splitter object, not the score. TimeSeriesSplit
    # inherits a `shuffle` attribute that is always False, but - unlike
    # KFold/StratifiedKFold - its __init__ signature has no `shuffle`
    # parameter at all: passing shuffle=True raises TypeError before a
    # splitter is even constructed. A shuffled split on time-ordered data
    # cannot be configured into existence through this splitter, structurally,
    # not by convention.
    assert splitter.shuffle is False
    import pytest

    with pytest.raises(TypeError):
        TimeSeriesSplit(n_splits=3, shuffle=True)


def test_classification_splitter_is_stratified_kfold():
    splitter = get_splitter(TaskType.CLASSIFICATION, ModelingConfig())
    assert isinstance(splitter, StratifiedKFold)
    assert splitter.shuffle is True


def test_regression_splitter_is_kfold():
    splitter = get_splitter(TaskType.REGRESSION, ModelingConfig())
    assert isinstance(splitter, KFold)
    assert splitter.shuffle is True


def test_split_strategy_matches_task_type():
    assert split_strategy_for_task(TaskType.FORECAST) == SplitStrategy.FORWARD_CHAINING
    assert split_strategy_for_task(TaskType.CLASSIFICATION) == SplitStrategy.STRATIFIED_KFOLD
    assert split_strategy_for_task(TaskType.REGRESSION) == SplitStrategy.KFOLD


# ---------------------------------------------------------------------------
# Part 2 (data-shape half): task type and aggregation from shape alone.
# ---------------------------------------------------------------------------


def test_datetime_indexed_numeric_target_is_forecast():
    dates = pd.date_range("2020-01-01", periods=100, freq="D")
    df = pd.DataFrame({"amount": range(100), "order_date": dates})
    task_type, dt_col = select_task_type(df, "amount", ModelingConfig())
    assert task_type == TaskType.FORECAST
    assert dt_col == "order_date"


def test_low_cardinality_categorical_target_is_classification():
    df = pd.DataFrame({"label": ["a", "b"] * 50, "feature": range(100)})
    task_type, dt_col = select_task_type(df, "label", ModelingConfig())
    assert task_type == TaskType.CLASSIFICATION
    assert dt_col is None


def test_continuous_numeric_no_time_index_is_regression():
    df = pd.DataFrame({"amount": [float(i) for i in range(100)], "feature": range(100)})
    task_type, _ = select_task_type(df, "amount", ModelingConfig())
    assert task_type == TaskType.REGRESSION


def test_high_cardinality_target_with_no_time_axis_is_unsupported():
    df = pd.DataFrame({"id": [f"x{i}" for i in range(100)], "feature": range(100)})
    task_type, _ = select_task_type(df, "id", ModelingConfig())
    assert task_type == TaskType.UNSUPPORTED


def test_choose_aggregation_numeric_target_is_sum_nonnumeric_is_count():
    df = pd.DataFrame({"amount": [1.0, 2.0], "order_id": ["a", "b"]})
    assert choose_aggregation(df, "amount") == AggregationMethod.SUM
    assert choose_aggregation(df, "order_id") == AggregationMethod.COUNT


def test_build_time_series_counts_orders_per_month():
    dates = pd.to_datetime(["2020-01-05", "2020-01-20", "2020-02-01"])
    df = pd.DataFrame({"order_id": ["o1", "o2", "o3"], "order_date": dates})
    config = ModelingConfig(forecast_monthly_span_days=0)  # force monthly grouping for this tiny span
    series = build_time_series(df, "order_id", "order_date", AggregationMethod.COUNT, config)
    assert series.tolist() == [2, 1]


def test_infer_forecast_frequency_picks_monthly_for_long_span():
    dates = pd.date_range("2020-01-01", periods=800, freq="D")
    assert infer_forecast_frequency(pd.Series(dates), ModelingConfig()) == "MS"


def test_infer_forecast_frequency_picks_daily_for_short_span():
    dates = pd.date_range("2020-01-01", periods=10, freq="D")
    assert infer_forecast_frequency(pd.Series(dates), ModelingConfig()) == "D"


# ---------------------------------------------------------------------------
# Part 5: trivial baselines.
# ---------------------------------------------------------------------------


def test_naive_forecast_repeats_last_value():
    train = pd.Series([1.0, 2.0, 3.0])
    assert naive_forecast(train, 3).tolist() == [3.0, 3.0, 3.0]


def test_seasonal_naive_forecast_cycles_last_season():
    train = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = seasonal_naive_forecast(train, 4, season_length=2)
    assert result.tolist() == [3.0, 4.0, 3.0, 4.0]


def test_seasonal_naive_forecast_none_without_full_season():
    train = pd.Series([1.0])
    assert seasonal_naive_forecast(train, 2, season_length=5) is None


def test_majority_class_predict():
    y = pd.Series(["a", "a", "b"])
    assert list(majority_class_predict(y, 3)) == ["a", "a", "a"]


def test_mean_predict():
    y = pd.Series([1.0, 2.0, 3.0])
    assert mean_predict(y, 2).tolist() == [2.0, 2.0]
