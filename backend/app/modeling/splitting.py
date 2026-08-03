"""Part 4: splitting is chosen by task type, never by convention. Time-
indexed tasks use TimeSeriesSplit - forward-chaining, train on past, test on
future - which structurally has NO shuffle parameter at all, so a shuffled
split on time-ordered data cannot be configured into existence by accident;
there is nothing to flip. Classification uses StratifiedKFold (class
distribution preserved per fold); regression uses plain KFold. Both of the
latter shuffle deliberately, since their rows are NOT time-ordered and a
fixed random_state still makes the shuffle itself reproducible.
"""

from __future__ import annotations

from sklearn.model_selection import KFold, StratifiedKFold, TimeSeriesSplit

from app.modeling.config import ModelingConfig
from app.modeling.models import SplitStrategy, TaskType


def get_splitter(task_type: TaskType, config: ModelingConfig):
    if task_type == TaskType.FORECAST:
        return TimeSeriesSplit(n_splits=config.n_splits)
    if task_type == TaskType.CLASSIFICATION:
        return StratifiedKFold(n_splits=config.n_splits, shuffle=True, random_state=config.seed)
    if task_type == TaskType.REGRESSION:
        return KFold(n_splits=config.n_splits, shuffle=True, random_state=config.seed)
    raise ValueError(f"no splitter defined for task_type={task_type!r}")


def split_strategy_for_task(task_type: TaskType) -> SplitStrategy:
    if task_type == TaskType.FORECAST:
        return SplitStrategy.FORWARD_CHAINING
    if task_type == TaskType.CLASSIFICATION:
        return SplitStrategy.STRATIFIED_KFOLD
    if task_type == TaskType.REGRESSION:
        return SplitStrategy.KFOLD
    raise ValueError(f"no split strategy defined for task_type={task_type!r}")
