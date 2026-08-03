"""Part 5: every model is compared against a trivial baseline appropriate
to its task type, scored the exact same way (same fold, same metric) as the
candidates - a model that does not beat its baseline by
ModelingConfig.baseline_margin is never reported as a usable model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Season length (in periods) for each aggregation frequency
# app/modeling/task_selection.py can produce - how many periods make up one
# full seasonal cycle for seasonal-naive to look back.
SEASON_LENGTH_BY_FREQ = {"MS": 12, "W": 52, "D": 7}


def naive_forecast(train: pd.Series, horizon: int) -> np.ndarray:
    """Repeats the last observed value for the whole horizon."""
    last = float(train.iloc[-1]) if len(train) else 0.0
    return np.full(horizon, last, dtype=float)


def seasonal_naive_forecast(train: pd.Series, horizon: int, season_length: int) -> np.ndarray | None:
    """Repeats the value from one season back, cycling if the horizon
    exceeds the season length. None if there isn't even one full season of
    history to draw from - a seasonal-naive baseline with no season to draw
    on isn't a fair comparison and is simply omitted, not faked."""
    if season_length <= 0 or len(train) < season_length:
        return None
    tail = train.iloc[-season_length:].to_numpy()
    return np.array([tail[i % season_length] for i in range(horizon)], dtype=float)


def majority_class_predict(y_train: pd.Series, n: int) -> np.ndarray:
    majority = y_train.mode().iloc[0]
    return np.full(n, majority, dtype=object)


def mean_predict(y_train: pd.Series, n: int) -> np.ndarray:
    return np.full(n, float(y_train.mean()), dtype=float)
