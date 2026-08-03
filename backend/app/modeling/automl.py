"""Part 2 (model-selection half) + Part 6 (report only out-of-sample
scores): candidate model families are small and fixed per task type, scored
by cross-validated, held-out performance ONLY - a training score is never
computed as a thing to report. The splitter (app/modeling/splitting.py) is
chosen by task type before any candidate is ever fit, and every candidate's
score is recorded, not just the winner's (the comparison table is itself a
report artifact).

Feature associations are computed from the WINNING family's fitted
coefficients/importances, refit once on the full kept data after CV picks
the winner - never presented as anything but association (Part 6, see
app/modeling/models.py's module docstring).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import f1_score, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from app.modeling.baselines import SEASON_LENGTH_BY_FREQ, majority_class_predict, mean_predict, naive_forecast, seasonal_naive_forecast
from app.modeling.config import ModelingConfig
from app.modeling.models import (
    BaselineScore,
    CandidateScore,
    ClassDistribution,
    FeatureAssociation,
    ForecastPoint,
    ForecastSeries,
    ModelFamily,
    PredictionInterval,
    TaskType,
)
from app.modeling.splitting import get_splitter


@dataclass
class TrainingOutcome:
    winner_family: ModelFamily
    winner_hyperparameters: dict
    metric: str
    out_of_sample_score: float
    candidate_scores: list[CandidateScore]
    baseline_scores: list[BaselineScore]
    row_count_trained_on: int
    feature_associations: list[FeatureAssociation] = field(default_factory=list)
    class_distribution: ClassDistribution | None = None
    prediction_interval: PredictionInterval | None = None
    forecast_series: ForecastSeries | None = None


def _hyperparameters_for(family: ModelFamily, config: ModelingConfig) -> dict:
    if family == ModelFamily.LOGISTIC_REGRESSION:
        return {"max_iter": 1000, "random_state": config.seed}
    if family == ModelFamily.RANDOM_FOREST_CLASSIFIER:
        return {"n_estimators": 100, "random_state": config.seed}
    if family == ModelFamily.RANDOM_FOREST_REGRESSOR:
        return {"n_estimators": 100, "random_state": config.seed}
    if family == ModelFamily.ARIMA:
        return {"order": [1, 1, 1]}
    return {}


def _encode_features(df: pd.DataFrame, feature_columns: list[str]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Deterministic, no-randomness preprocessing: numeric columns keep
    their values with median imputation; everything else is one-hot encoded
    with an explicit missing-value category. Returns the encoded frame plus
    a dummy-column -> original-column map so feature associations can be
    reported per original column, not per one-hot dummy."""
    X = pd.DataFrame(index=df.index)
    dummy_to_original: dict[str, str] = {}
    for col in feature_columns:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            X[col] = series.fillna(series.median())
            dummy_to_original[col] = col
        else:
            filled = series.astype(str).where(series.notna(), "__missing__")
            dummies = pd.get_dummies(filled, prefix=col)
            for dcol in dummies.columns:
                X[dcol] = dummies[dcol]
                dummy_to_original[dcol] = col
    return X, dummy_to_original


def _feature_associations(raw: dict[str, float], dummy_to_original: dict[str, str]) -> list[FeatureAssociation]:
    grouped: dict[str, list[float]] = {}
    for dcol, value in raw.items():
        original = dummy_to_original.get(dcol, dcol)
        grouped.setdefault(original, []).append(abs(float(value)))
    associations = [FeatureAssociation(column=col, association_strength=float(np.mean(vals))) for col, vals in grouped.items()]
    associations.sort(key=lambda a: (-a.association_strength, a.column))
    return associations


def _model_raw_importances(model, columns: list[str]) -> dict[str, float]:
    if hasattr(model, "feature_importances_"):
        return dict(zip(columns, model.feature_importances_))
    coef = np.asarray(model.coef_)
    if coef.ndim > 1:
        coef = np.abs(coef).mean(axis=0)
    return dict(zip(columns, coef))


def evaluate_classification(df: pd.DataFrame, feature_columns: list[str], target_column: str, config: ModelingConfig) -> TrainingOutcome:
    working = df.dropna(subset=[target_column])
    y = working[target_column].astype(str)
    row_count = len(working)

    class_counts = y.value_counts()
    class_distribution = ClassDistribution(
        class_counts={str(k): int(v) for k, v in class_counts.items()},
        majority_class_share=float(class_counts.iloc[0] / row_count) if row_count else 0.0,
    )

    n_splits = max(2, min(config.n_splits, int(class_counts.min())))
    split_config = dataclasses.replace(config, n_splits=n_splits)
    splitter = get_splitter(TaskType.CLASSIFICATION, split_config)

    X, dummy_to_original = _encode_features(working, feature_columns)
    X_arr = X.to_numpy()
    y_arr = y.to_numpy()

    candidates = {
        ModelFamily.LOGISTIC_REGRESSION: lambda: LogisticRegression(random_state=config.seed, max_iter=1000),
        ModelFamily.RANDOM_FOREST_CLASSIFIER: lambda: RandomForestClassifier(random_state=config.seed, n_estimators=100),
    }
    per_family_scores: dict[ModelFamily, list[float]] = {family: [] for family in candidates}
    baseline_fold_scores: list[float] = []

    for train_idx, test_idx in splitter.split(X_arr, y_arr):
        X_train, X_test = X_arr[train_idx], X_arr[test_idx]
        y_train, y_test = y_arr[train_idx], y_arr[test_idx]
        for family, factory in candidates.items():
            model = factory()
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            per_family_scores[family].append(f1_score(y_test, preds, average="weighted", zero_division=0))
        baseline_preds = majority_class_predict(pd.Series(y_train), len(y_test))
        baseline_fold_scores.append(f1_score(y_test, baseline_preds, average="weighted", zero_division=0))

    candidate_scores = [
        CandidateScore(model_family=family, metric="f1_weighted", out_of_sample_score=float(np.mean(scores)))
        for family, scores in per_family_scores.items()
    ]
    baseline_scores = [BaselineScore(model_family=ModelFamily.MAJORITY_CLASS, metric="f1_weighted", out_of_sample_score=float(np.mean(baseline_fold_scores)))]

    winner = max(candidate_scores, key=lambda c: c.out_of_sample_score)

    final_model = candidates[winner.model_family]()
    final_model.fit(X_arr, y_arr)
    feature_associations = _feature_associations(_model_raw_importances(final_model, list(X.columns)), dummy_to_original)

    return TrainingOutcome(
        winner_family=winner.model_family,
        winner_hyperparameters=_hyperparameters_for(winner.model_family, config),
        metric="f1_weighted",
        out_of_sample_score=winner.out_of_sample_score,
        candidate_scores=candidate_scores,
        baseline_scores=baseline_scores,
        row_count_trained_on=row_count,
        feature_associations=feature_associations,
        class_distribution=class_distribution,
    )


def evaluate_regression(df: pd.DataFrame, feature_columns: list[str], target_column: str, config: ModelingConfig) -> TrainingOutcome:
    working = df.dropna(subset=[target_column])
    y = working[target_column].astype(float)
    row_count = len(working)

    n_splits = max(2, min(config.n_splits, row_count - 1))
    split_config = dataclasses.replace(config, n_splits=n_splits)
    splitter = get_splitter(TaskType.REGRESSION, split_config)

    X, dummy_to_original = _encode_features(working, feature_columns)
    X_arr = X.to_numpy()
    y_arr = y.to_numpy()

    candidates = {
        ModelFamily.LINEAR_REGRESSION: lambda: LinearRegression(),
        ModelFamily.RANDOM_FOREST_REGRESSOR: lambda: RandomForestRegressor(random_state=config.seed, n_estimators=100),
    }
    per_family_scores: dict[ModelFamily, list[float]] = {family: [] for family in candidates}
    baseline_fold_scores: list[float] = []

    for train_idx, test_idx in splitter.split(X_arr):
        X_train, X_test = X_arr[train_idx], X_arr[test_idx]
        y_train, y_test = y_arr[train_idx], y_arr[test_idx]
        for family, factory in candidates.items():
            model = factory()
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            per_family_scores[family].append(float(np.sqrt(mean_squared_error(y_test, preds))))
        baseline_preds = mean_predict(pd.Series(y_train), len(y_test))
        baseline_fold_scores.append(float(np.sqrt(mean_squared_error(y_test, baseline_preds))))

    candidate_scores = [
        CandidateScore(model_family=family, metric="rmse", out_of_sample_score=float(np.mean(scores)))
        for family, scores in per_family_scores.items()
    ]
    baseline_scores = [BaselineScore(model_family=ModelFamily.MEAN, metric="rmse", out_of_sample_score=float(np.mean(baseline_fold_scores)))]

    winner = min(candidate_scores, key=lambda c: c.out_of_sample_score)  # lower RMSE is better

    final_model = candidates[winner.model_family]()
    final_model.fit(X_arr, y_arr)
    feature_associations = _feature_associations(_model_raw_importances(final_model, list(X.columns)), dummy_to_original)

    return TrainingOutcome(
        winner_family=winner.model_family,
        winner_hyperparameters=_hyperparameters_for(winner.model_family, config),
        metric="rmse",
        out_of_sample_score=winner.out_of_sample_score,
        candidate_scores=candidate_scores,
        baseline_scores=baseline_scores,
        row_count_trained_on=row_count,
        feature_associations=feature_associations,
    )


def _rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)) ** 2)))


def _fit_predict_prophet(train_series: pd.Series, horizon: int, freq: str) -> np.ndarray:
    from prophet import Prophet

    train_df = pd.DataFrame({"ds": train_series.index, "y": train_series.to_numpy()})
    model = Prophet()
    model.fit(train_df)
    future = model.make_future_dataframe(periods=horizon, freq=freq, include_history=False)
    forecast = model.predict(future)
    return forecast["yhat"].to_numpy()[:horizon]


def _fit_predict_arima(train_series: pd.Series, horizon: int):
    from statsmodels.tsa.arima.model import ARIMA

    model = ARIMA(train_series.to_numpy(), order=(1, 1, 1))
    fitted = model.fit()
    return np.asarray(fitted.forecast(steps=horizon))


def _full_refit_forecast(
    series: pd.Series, family: ModelFamily, freq: str, chart_horizon: int
) -> tuple[PredictionInterval | None, ForecastSeries | None]:
    """Refits the WINNING family once on the FULL series - same
    "refit-on-everything-once-the-winner-is-chosen" pattern this module
    already uses for feature associations (evaluate_classification/
    evaluate_regression) - to produce BOTH the single-step-ahead
    PredictionInterval (Part 6) and the longer, chart-ready forecast band
    the Predict page renders (dashboard UX pass Part 1). One fit per
    family, not two: the interval is just the forecast band's first point.
    """
    historical = [ForecastPoint(date=idx.isoformat(), value=float(val)) for idx, val in series.items()]

    if family == ModelFamily.PROPHET:
        from prophet import Prophet

        train_df = pd.DataFrame({"ds": series.index, "y": series.to_numpy()})
        model = Prophet()
        model.fit(train_df)
        future = model.make_future_dataframe(periods=chart_horizon, freq=freq, include_history=False)
        forecast = model.predict(future)
        points = [
            ForecastPoint(date=row.ds.isoformat(), value=float(row.yhat), lower=float(row.yhat_lower), upper=float(row.yhat_upper))
            for row in forecast.itertuples()
        ]
    elif family == ModelFamily.ARIMA:
        from statsmodels.tsa.arima.model import ARIMA

        model = ARIMA(series.to_numpy(), order=(1, 1, 1))
        fitted = model.fit()
        forecast_result = fitted.get_forecast(steps=chart_horizon)
        means = np.asarray(forecast_result.predicted_mean)
        conf = forecast_result.conf_int(alpha=0.2)
        conf_arr = conf.to_numpy() if hasattr(conf, "to_numpy") else np.asarray(conf)
        future_dates = pd.date_range(series.index[-1], periods=chart_horizon + 1, freq=freq)[1:]
        points = [
            ForecastPoint(date=future_dates[i].isoformat(), value=float(means[i]), lower=float(conf_arr[i][0]), upper=float(conf_arr[i][1]))
            for i in range(chart_horizon)
        ]
    else:
        return None, None

    interval = PredictionInterval(lower=points[0].lower, upper=points[0].upper, confidence_level=0.8) if points else None
    forecast_series = ForecastSeries(historical=historical, forecast=points)
    return interval, forecast_series


def evaluate_forecast(series: pd.Series, freq: str, config: ModelingConfig) -> TrainingOutcome:
    n_periods = len(series)
    n_splits = max(2, min(config.n_splits, n_periods - 1))
    splitter = TimeSeriesSplit(n_splits=n_splits)

    season_length = SEASON_LENGTH_BY_FREQ.get(freq, 0)

    per_family_scores: dict[ModelFamily, list[float]] = {ModelFamily.PROPHET: [], ModelFamily.ARIMA: []}
    naive_fold_scores: list[float] = []
    seasonal_fold_scores: list[float] = []

    values = series.to_numpy()

    for train_pos, test_pos in splitter.split(values):
        train_series = series.iloc[train_pos]
        test_series = series.iloc[test_pos]
        horizon = len(test_series)
        actual = test_series.to_numpy()

        prophet_preds = _fit_predict_prophet(train_series, horizon, freq)
        per_family_scores[ModelFamily.PROPHET].append(_rmse(actual, prophet_preds))

        arima_preds = _fit_predict_arima(train_series, horizon)
        per_family_scores[ModelFamily.ARIMA].append(_rmse(actual, arima_preds))

        naive_preds = naive_forecast(train_series, horizon)
        naive_fold_scores.append(_rmse(actual, naive_preds))

        seasonal_preds = seasonal_naive_forecast(train_series, horizon, season_length)
        if seasonal_preds is not None:
            seasonal_fold_scores.append(_rmse(actual, seasonal_preds))

    candidate_scores = [
        CandidateScore(model_family=family, metric="rmse", out_of_sample_score=float(np.mean(scores)))
        for family, scores in per_family_scores.items()
        if scores
    ]
    baseline_scores = [BaselineScore(model_family=ModelFamily.NAIVE, metric="rmse", out_of_sample_score=float(np.mean(naive_fold_scores)))]
    if seasonal_fold_scores:
        baseline_scores.append(
            BaselineScore(model_family=ModelFamily.SEASONAL_NAIVE, metric="rmse", out_of_sample_score=float(np.mean(seasonal_fold_scores)))
        )

    winner = min(candidate_scores, key=lambda c: c.out_of_sample_score)
    chart_horizon = min(config.forecast_chart_horizon, max(1, n_periods // 2))
    prediction_interval, forecast_series = _full_refit_forecast(series, winner.model_family, freq, chart_horizon)

    return TrainingOutcome(
        winner_family=winner.model_family,
        winner_hyperparameters=_hyperparameters_for(winner.model_family, config),
        metric="rmse",
        out_of_sample_score=winner.out_of_sample_score,
        candidate_scores=candidate_scores,
        baseline_scores=baseline_scores,
        row_count_trained_on=n_periods,
        prediction_interval=prediction_interval,
        forecast_series=forecast_series,
    )
