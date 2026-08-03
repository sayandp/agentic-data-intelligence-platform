"""Column-kind list helpers, used by every analysis module in this package.
The kind check itself lives in app/column_kind.py (shared with profiling and
modeling - see that module's docstring); this module only builds the
per-kind column lists exploration's analyses actually iterate over."""

from __future__ import annotations

import pandas as pd

from app.column_kind import ColumnKind, column_kind

__all__ = ["ColumnKind", "column_kind", "numeric_columns", "datetime_columns", "categorical_columns"]


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if column_kind(df[c]) == ColumnKind.NUMERIC]


def datetime_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if column_kind(df[c]) == ColumnKind.DATETIME]


def categorical_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if column_kind(df[c]) == ColumnKind.CATEGORICAL]
