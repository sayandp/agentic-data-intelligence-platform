"""Every threshold the Modeling Agent uses, in one place and all
configurable - mirrors app/exploration/config.py's pattern rather than
scattering magic numbers through the leakage/splitting/baseline/automl
modules."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_LEAKAGE_CORRELATION_THRESHOLD = 0.98
DEFAULT_IDENTIFIER_CARDINALITY_RATIO = 0.95
DEFAULT_NULL_RATE_THRESHOLD = 0.5

DEFAULT_CLASSIFICATION_CARDINALITY_CEILING = 50

DEFAULT_MIN_ROWS = 100
# Measured in a DIFFERENT unit than min_rows by design: min_rows counts raw
# rows (one row = one training example, for classification/regression).
# min_periods_for_forecast counts AGGREGATED periods (one row = one
# month/week/day of history, after app/modeling/task_selection.py's
# time-series aggregation) - a forecast trains on the aggregated series, not
# the underlying transactions. 24 periods (two years of monthly data) is the
# "separate, higher floor" the spec calls for: it is a far more stringent
# bar than 100 raw rows once you account for how much underlying
# transaction volume 24 months of a real dataset represents, and it is also
# the practical minimum for TimeSeriesSplit to produce multiple meaningful
# folds plus a realistic shot at detecting yearly seasonality.
DEFAULT_MIN_PERIODS_FOR_FORECAST = 24

DEFAULT_N_SPLITS = 5
DEFAULT_SEED = 0

# A model's out-of-sample score must beat its baseline by at least this
# fraction (Part 5's "beat a trivial baseline by a configurable margin").
DEFAULT_BASELINE_MARGIN = 0.05
# Absolute score floors (Part 6) - below these, a model is escalated even if
# it beat its baseline. Classification F1 has a universal [0, 1] scale, so a
# floor is meaningful; regression RMSE's scale depends entirely on the
# target's own units, so no universal absolute floor is imposed - the
# baseline-beat margin is regression's primary gate.
DEFAULT_SCORE_FLOOR_CLASSIFICATION = 0.5
DEFAULT_SCORE_FLOOR_REGRESSION: float | None = None
# Majority class share above this is reported as severe imbalance (Part 6:
# "report the distribution and escalate rather than presenting a high
# score") - a dataset this skewed makes F1 easy to game by predicting the
# majority class alone.
DEFAULT_CLASS_IMBALANCE_MAJORITY_SHARE = 0.9

# Forecast aggregation frequency is chosen from the datetime column's span,
# not inferred from raw (typically irregular, per-transaction) timestamps -
# see app/modeling/task_selection.py::_infer_forecast_frequency.
DEFAULT_FORECAST_MONTHLY_SPAN_DAYS = 365
DEFAULT_FORECAST_WEEKLY_SPAN_DAYS = 60

# Predict page UX pass: how far past the historical series the forecast
# chart's line+band extends - a fixed, deterministic horizon (never
# LLM-chosen), independent of the CV fold horizons evaluate_forecast uses
# internally for scoring. 12 periods reads naturally as "a year ahead" for
# the common monthly-aggregation case without this needing to know which
# frequency a given series ended up at.
DEFAULT_FORECAST_CHART_HORIZON = 12


@dataclass
class ModelingConfig:
    leakage_correlation_threshold: float = DEFAULT_LEAKAGE_CORRELATION_THRESHOLD
    identifier_cardinality_ratio: float = DEFAULT_IDENTIFIER_CARDINALITY_RATIO
    null_rate_threshold: float = DEFAULT_NULL_RATE_THRESHOLD

    classification_cardinality_ceiling: int = DEFAULT_CLASSIFICATION_CARDINALITY_CEILING

    min_rows: int = DEFAULT_MIN_ROWS
    min_periods_for_forecast: int = DEFAULT_MIN_PERIODS_FOR_FORECAST

    n_splits: int = DEFAULT_N_SPLITS
    seed: int = DEFAULT_SEED

    baseline_margin: float = DEFAULT_BASELINE_MARGIN
    score_floor_classification: float | None = DEFAULT_SCORE_FLOOR_CLASSIFICATION
    score_floor_regression: float | None = DEFAULT_SCORE_FLOOR_REGRESSION
    class_imbalance_majority_share: float = DEFAULT_CLASS_IMBALANCE_MAJORITY_SHARE

    forecast_monthly_span_days: int = DEFAULT_FORECAST_MONTHLY_SPAN_DAYS
    forecast_weekly_span_days: int = DEFAULT_FORECAST_WEEKLY_SPAN_DAYS
    forecast_chart_horizon: int = DEFAULT_FORECAST_CHART_HORIZON
