"""The single 'what kind of column is this' check every phase shares.

Profiling (Phase 2), Exploration (Phase 4), and Modeling (Phase 7) must
never be able to disagree about the kind of the same column in the same
run - Phase 7.5's own investigation found them doing exactly that, because
Modeling had grown a module-local heuristic nothing else used. There is
exactly one function that answers "what kind is this column", not one per
module, and it is a pure dtype check, nothing more.

By the time any caller above sees a DataFrame, app/datetime_coercion.py has
already run once, at connector fetch time, and settled every column's real
dtype (already-typed dtype, or a declared SQL type, or a parsed text
column that cleared its parseability floor). Detection/coercion heuristics
belong there; this module only ever reads the dtype that decision left
behind.
"""

from __future__ import annotations

from enum import Enum

import pandas as pd


class ColumnKind(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    DATETIME = "datetime"


def column_kind(series: pd.Series) -> ColumnKind:
    if pd.api.types.is_datetime64_any_dtype(series):
        return ColumnKind.DATETIME
    if pd.api.types.is_bool_dtype(series):
        return ColumnKind.CATEGORICAL
    if pd.api.types.is_numeric_dtype(series):
        return ColumnKind.NUMERIC
    return ColumnKind.CATEGORICAL
