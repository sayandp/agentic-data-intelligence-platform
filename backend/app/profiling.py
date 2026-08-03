"""Computes the 'normal' profile a later ingest is validated against.

A baseline profile is plain, JSON-serializable data (no DataFrames) so it can
be stored in Baseline.profile_json and read back without pandas-version
coupling.

Identifier-shaped columns (Phase 7.5): a column whose cardinality equals the
row count - a primary key, a UUID - is not evidence the dataset is garbage,
and profiling it as a category (a "top values" table over what is really a
per-row unique key) was never meaningful in the first place. Such a column is
EXCLUDED from profiling and recorded on profile["excluded_columns"], same
pattern as Phase 4's `skipped` list - the baseline is still created from
everything else. See app/baseline_sanity.py for what still rejects the whole
baseline (null ceiling, zero-variance numerics - genuine data pathology, not
a column-shape choice).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.baseline_sanity import MIN_ROWS_FOR_CARDINALITY_FLOOR
from app.column_kind import ColumnKind, column_kind

NUMERIC_BINS = 10
TOP_N_CATEGORIES = 10
MAX_SAMPLE_VALUES = 1000
SAMPLE_RANDOM_STATE = 0


def _looks_like_identifier(series: pd.Series, row_count: int) -> bool:
    """cardinality == row_count on a categorical/text column - a primary
    key, a UUID, an order id. Below MIN_ROWS_FOR_CARDINALITY_FLOOR this is
    trivially true for almost any column and isn't a meaningful signal."""
    if row_count < MIN_ROWS_FOR_CARDINALITY_FLOOR:
        return False
    non_null = series.dropna()
    if non_null.empty:
        return False
    return int(non_null.nunique()) == row_count


def _profile_numeric(series: pd.Series) -> dict:
    non_null = series.dropna().astype(float)
    if non_null.empty:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "histogram": {"bin_edges": [], "counts": []},
            "sample_values": [],
        }

    counts, bin_edges = np.histogram(non_null, bins=NUMERIC_BINS)
    sample_size = min(len(non_null), MAX_SAMPLE_VALUES)
    sample = non_null.sample(sample_size, random_state=SAMPLE_RANDOM_STATE)

    return {
        "min": float(non_null.min()),
        "max": float(non_null.max()),
        "mean": float(non_null.mean()),
        "std": float(non_null.std(ddof=0)),
        "histogram": {
            "bin_edges": [float(edge) for edge in bin_edges],
            "counts": [int(count) for count in counts],
        },
        "sample_values": [float(value) for value in sample.tolist()],
    }


def _profile_categorical(series: pd.Series) -> dict:
    non_null = series.dropna()
    top_values = non_null.astype(str).value_counts().head(TOP_N_CATEGORIES)
    return {
        "cardinality": int(non_null.nunique()),
        "top_values": {str(value): int(count) for value, count in top_values.items()},
    }


def _profile_datetime(series: pd.Series) -> dict:
    non_null = series.dropna()
    if non_null.empty:
        return {"min": None, "max": None, "span_days": None}
    minimum, maximum = non_null.min(), non_null.max()
    return {
        "min": minimum.isoformat(),
        "max": maximum.isoformat(),
        "span_days": (maximum - minimum).total_seconds() / 86400.0,
    }


class BaselineProfiler:
    """Computes a per-column statistical profile plus overall row count."""

    def profile(self, df: pd.DataFrame) -> dict:
        row_count = len(df)
        columns: dict[str, dict] = {}
        excluded_columns: list[dict] = []

        for column in df.columns:
            series = df[column]
            kind = column_kind(series)

            if kind == ColumnKind.CATEGORICAL and _looks_like_identifier(series, row_count):
                excluded_columns.append(
                    {
                        "column": column,
                        "reason": (
                            f"cardinality equals row count ({row_count}) - looks like an identifier "
                            "(e.g. a primary key), not a category; excluded from profiling rather than "
                            "causing the whole baseline to be rejected"
                        ),
                    }
                )
                continue

            entry = {
                "dtype": str(series.dtype),
                "null_rate": float(series.isna().mean()) if len(series) else 0.0,
                "kind": kind.value,
            }
            if kind == ColumnKind.NUMERIC:
                entry.update(_profile_numeric(series))
            elif kind == ColumnKind.DATETIME:
                entry.update(_profile_datetime(series))
            else:
                entry.update(_profile_categorical(series))
            columns[column] = entry

        return {"row_count": row_count, "columns": columns, "excluded_columns": excluded_columns}
