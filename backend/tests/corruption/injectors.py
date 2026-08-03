"""Labelled corruption injectors.

Each injector takes (clean DataFrame, seed) and returns (corrupted_df,
ground_truth). Injectors never mutate the input frame - callers get a fresh,
independently corrupted copy every time, which is what lets CorruptionSuite
apply each corruption type in isolation against the same clean baseline.

Every injector accepts `seed` even when its own logic is deterministic
(rename_column, drop_column, ...): it's recorded on the ground truth either
way so every corrupted dataset in an evaluation run is reproducible from its
seed alone, and so the signature is uniform across the whole registry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tests.corruption.ground_truth import CorruptionGroundTruth


def _first_column(df: pd.DataFrame) -> str:
    if df.columns.empty:
        raise ValueError("DataFrame has no columns to corrupt")
    return df.columns[0]


def _first_numeric_column(df: pd.DataFrame) -> str:
    numeric_columns = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_columns:
        raise ValueError("DataFrame has no numeric column to corrupt")
    return numeric_columns[0]


def _first_categorical_column(df: pd.DataFrame) -> str:
    categorical_columns = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if not categorical_columns:
        raise ValueError("DataFrame has no categorical column to corrupt")
    return categorical_columns[0]


def rename_column(
    df: pd.DataFrame, seed: int, column: str | None = None, new_name: str | None = None
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Renames a column, as if an upstream schema change relabeled a field."""
    column = column or _first_column(df)
    new_name = new_name or f"{column}_renamed"

    corrupted = df.rename(columns={column: new_name})
    truth = CorruptionGroundTruth(
        corruption_type="rename_column",
        target_columns=[column],
        parameters={"column": column, "new_name": new_name},
        expected_detection="schema_conformance",
        expected_risk_level="low",
        seed=seed,
        notes="Old name goes missing, new name is unexpected; both are schema_conformance failures.",
    )
    return corrupted, truth


def change_dtype(
    df: pd.DataFrame, seed: int, column: str | None = None, prefix: str = "$"
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Casts a numeric column to string, as a dirty currency-formatted export would.

    A plain `.astype(str)` (e.g. "10.5") round-trips straight back to float64
    the moment it's written to CSV and re-read, since pandas re-infers pure
    numeric text on load - the corruption would vanish before FileConnector
    ever saw it. The default prefix ("$", as real "$10.50"-style dirty exports
    produce) forces the column to stay string-typed after a CSV round trip -
    and, not incidentally, forces safe_type_cast to reject the fix (a currency
    symbol isn't a "clean" numeric string; stripping it is an interpretive
    judgment call, not a safe mechanical cast).

    Pass prefix="" for a genuinely clean numeric string. That variant can't
    survive a *CSV* round trip (plain "10.5" gets re-inferred as float on
    read), but it's the shape a real "numbers stored as text" Excel
    corruption takes - see scripts/part6_evaluation.py for the fixture that
    demonstrates safe_type_cast's actual auto-fixable path.
    """
    column = column or _first_numeric_column(df)

    corrupted = df.copy()
    corrupted[column] = corrupted[column].map(lambda v: f"{prefix}{v}")
    truth = CorruptionGroundTruth(
        corruption_type="change_dtype",
        target_columns=[column],
        parameters={
            "column": column,
            "prefix": prefix,
            "from_dtype": str(df[column].dtype),
            "to_dtype": str(corrupted[column].dtype),
        },
        expected_detection="schema_conformance",
        expected_risk_level="low",
        seed=seed,
        notes="Column stays present; only its dtype disagrees with the baseline.",
    )
    return corrupted, truth


def inject_nulls(
    df: pd.DataFrame,
    seed: int,
    column: str | None = None,
    rate: float = 0.3,
    tolerance: float = 0.05,
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Blanks out `rate` fraction of a column's rows, simulating an upstream feed gap."""
    column = column or _first_column(df)
    rng = np.random.default_rng(seed)

    corrupted = df.copy()
    n_to_null = int(round(len(corrupted) * rate))
    null_positions = rng.choice(len(corrupted), size=n_to_null, replace=False) if n_to_null else []
    corrupted.iloc[null_positions, corrupted.columns.get_loc(column)] = np.nan

    truth = CorruptionGroundTruth(
        corruption_type="inject_nulls",
        target_columns=[column],
        parameters={"column": column, "rate": rate, "tolerance": tolerance},
        expected_detection="null_threshold",
        expected_risk_level="high" if rate > tolerance else "low",
        seed=seed,
        notes="Risk depends on whether the injected rate clears the null-tolerance band above baseline.",
    )
    return corrupted, truth


def drop_column(
    df: pd.DataFrame, seed: int, column: str | None = None
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Removes a column entirely, as a broken upstream SELECT/export would."""
    column = column or _first_column(df)

    corrupted = df.drop(columns=[column])
    truth = CorruptionGroundTruth(
        corruption_type="drop_column",
        target_columns=[column],
        parameters={"column": column},
        expected_detection="schema_conformance",
        expected_risk_level="high",
        seed=seed,
        notes="Data is gone, not just mislabeled; must never be silently repaired.",
    )
    return corrupted, truth


def shift_distribution(
    df: pd.DataFrame,
    seed: int,
    column: str | None = None,
    mode: str = "offset",
    factor: float | None = None,
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Scales or offsets a numeric column, as a unit-conversion bug would."""
    column = column or _first_numeric_column(df)
    if mode not in ("offset", "scale"):
        raise ValueError(f"mode must be 'offset' or 'scale', got {mode!r}")

    corrupted = df.copy()
    non_null = corrupted[column].dropna()
    std = float(non_null.std()) or 1.0
    mean = float(non_null.mean())

    if mode == "offset":
        factor = factor if factor is not None else 10.0 * std
        corrupted[column] = corrupted[column] + factor
    else:
        factor = factor if factor is not None else 5.0
        corrupted[column] = corrupted[column] * factor

    truth = CorruptionGroundTruth(
        corruption_type="shift_distribution",
        target_columns=[column],
        parameters={"column": column, "mode": mode, "factor": factor, "baseline_mean": mean, "baseline_std": std},
        expected_detection="distribution_drift",
        expected_risk_level="high",
        seed=seed,
        notes="Same schema and null rate as baseline; only the numeric distribution has moved.",
    )
    return corrupted, truth


def inject_whitespace_case(
    df: pd.DataFrame,
    seed: int,
    column: str | None = None,
    rate: float = 0.5,
) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Adds stray whitespace/case variance to a categorical column, e.g. 'NYC' vs ' nyc '."""
    column = column or _first_categorical_column(df)
    rng = np.random.default_rng(seed)

    corrupted = df.copy()
    non_null_idx = corrupted.index[corrupted[column].notna()]
    n_to_corrupt = int(round(len(non_null_idx) * rate))
    targets = rng.choice(non_null_idx, size=n_to_corrupt, replace=False) if n_to_corrupt else []

    def _mangle(value: str) -> str:
        variant = rng.integers(0, 3)
        if variant == 0:
            return f"  {value}  "
        if variant == 1:
            return str(value).upper()
        return str(value).lower()

    for idx in targets:
        corrupted.at[idx, column] = _mangle(corrupted.at[idx, column])

    truth = CorruptionGroundTruth(
        corruption_type="inject_whitespace_case",
        target_columns=[column],
        parameters={"column": column, "rate": rate},
        expected_detection="categorical_drift",
        expected_risk_level="low",
        seed=seed,
        notes=(
            "Not caught by any Part 3 rule family (schema/null/drift/encoding all pass); "
            "a known detection gap until a categorical-drift rule exists."
        ),
    )
    return corrupted, truth


def truncate_rows(df: pd.DataFrame, seed: int, fraction: float = 0.3) -> tuple[pd.DataFrame, CorruptionGroundTruth]:
    """Drops the tail of the dataset, as an interrupted export/transfer would."""
    keep = int(round(len(df) * (1 - fraction)))
    corrupted = df.iloc[:keep].reset_index(drop=True)

    truth = CorruptionGroundTruth(
        corruption_type="truncate_rows",
        target_columns=list(df.columns),
        parameters={"fraction": fraction, "original_row_count": len(df), "corrupted_row_count": keep},
        expected_detection="schema_conformance",
        expected_risk_level="high",
        seed=seed,
        notes="Caught via the row-count-drop check inside schema_conformance, not the column/dtype checks.",
    )
    return corrupted, truth
