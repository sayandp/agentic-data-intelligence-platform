"""Deterministic fix execution - the third layer (detect -> diagnose -> act).

Every function here takes a plain, already-derived spec dict, never the raw
LLM diagnosis. Parameters that identify WHAT to fix (which column, which
dtype, which rename pair) come from ValidationEvent/baseline data - things
the detection layer already established independently of the model - not
from diagnosis.suggested_fix.parameters. The LLM's job ends at choosing
WHICH allowlisted action applies; this layer decides how to execute it
safely, and refuses rather than guesses when it can't guarantee correctness.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.diagnosis.models import FixAction

ALLOWLIST_ACTIONS: frozenset[FixAction] = frozenset(
    {FixAction.RENAME_COLUMN, FixAction.SAFE_TYPE_CAST, FixAction.STRIP_WHITESPACE, FixAction.NORMALIZE_CASE}
)


@dataclass
class FixOutcome:
    success: bool
    new_df: pd.DataFrame
    reversal_record: dict | None
    error: str | None = None


def apply_action(df: pd.DataFrame, action: FixAction, spec: dict, baseline_profile: dict | None = None) -> FixOutcome:
    if action == FixAction.RENAME_COLUMN:
        return _rename_column(df, spec)
    if action == FixAction.SAFE_TYPE_CAST:
        return _safe_type_cast(df, spec)
    if action == FixAction.STRIP_WHITESPACE:
        return _strip_whitespace(df, spec)
    if action == FixAction.NORMALIZE_CASE:
        return _normalize_case(df, spec, baseline_profile or {})
    raise ValueError(f"'{action}' is not an executable action")  # ESCALATE has no executor by design


def revert_action(df: pd.DataFrame, reversal_record: dict) -> pd.DataFrame:
    kind = reversal_record["kind"]
    if kind == "rename":
        return df.rename(columns={reversal_record["current_name"]: reversal_record["original_name"]})
    if kind == "column_values":
        reverted = df.copy()
        column = reversal_record["column"]
        original_values = reversal_record["original_values"]
        reverted[column] = [original_values[str(idx)] for idx in reverted.index]
        return reverted
    raise ValueError(f"unknown reversal record kind '{kind}'")


# -- rename_column: pure metadata swap, trivially and exactly reversible --


def _rename_column(df: pd.DataFrame, spec: dict) -> FixOutcome:
    current_name, target_name = spec["current_name"], spec["target_name"]
    if current_name not in df.columns:
        return FixOutcome(False, df, None, error=f"column '{current_name}' not present, cannot rename")
    new_df = df.rename(columns={current_name: target_name})
    reversal = {"kind": "rename", "current_name": target_name, "original_name": current_name}
    return FixOutcome(True, new_df, reversal)


# -- safe_type_cast: rejected outright unless it succeeds on 100% of non-null values --


def _safe_type_cast(df: pd.DataFrame, spec: dict) -> FixOutcome:
    column, target_dtype = spec["column"], spec["target_dtype"]
    if column not in df.columns:
        return FixOutcome(False, df, None, error=f"column '{column}' not present, cannot cast")

    series = df[column]
    non_null = series.dropna()
    try:
        casted_non_null = non_null.astype(target_dtype)
    except (ValueError, TypeError) as exc:
        return FixOutcome(
            False, df, None, error=f"cast to {target_dtype!r} failed on at least one non-null value: {exc}"
        )
    if casted_non_null.isna().any():
        # e.g. pd.to_numeric-style coercion that silently produced NaN instead
        # of raising - still a rejection, not a partial success.
        return FixOutcome(False, df, None, error=f"cast to {target_dtype!r} produced NaN on a non-null value")

    original_values = {str(idx): value for idx, value in series.items()}
    new_df = df.copy()
    new_df[column] = series.astype(target_dtype)  # cast the full column (nulls included) the same way
    reversal = {"kind": "column_values", "column": column, "original_values": original_values}
    return FixOutcome(True, new_df, reversal)


# -- strip_whitespace: literal, mechanical, nothing else --


def _strip_whitespace(df: pd.DataFrame, spec: dict) -> FixOutcome:
    column = spec["column"]
    if column not in df.columns:
        return FixOutcome(False, df, None, error=f"column '{column}' not present")

    original_values = {str(idx): value for idx, value in df[column].items()}
    new_df = df.copy()
    new_df[column] = df[column].map(lambda v: v.strip() if isinstance(v, str) else v)
    reversal = {"kind": "column_values", "column": column, "original_values": original_values}
    return FixOutcome(True, new_df, reversal)


# -- normalize_case: restores each value's baseline-canonical form (looked up
# by a strip+casefold key) rather than blindly upper/lowering, since there is
# no "correct" case without a reference; this also happens to fix any
# accompanying whitespace drift, which is a bonus, not a scope creep - the
# actual safety net is the post-condition recheck, not this function's
# thoroughness. --


def _normalize_case(df: pd.DataFrame, spec: dict, baseline_profile: dict) -> FixOutcome:
    column = spec["column"]
    if column not in df.columns:
        return FixOutcome(False, df, None, error=f"column '{column}' not present")

    baseline_values = list(baseline_profile.get("columns", {}).get(column, {}).get("top_values", {}).keys())
    if not baseline_values:
        return FixOutcome(False, df, None, error=f"no baseline top_values for '{column}' to normalize against")
    canonical_by_key = {v.strip().casefold(): v for v in baseline_values}

    def _canonicalize(value):
        if not isinstance(value, str):
            return value
        return canonical_by_key.get(value.strip().casefold(), value)

    original_values = {str(idx): value for idx, value in df[column].items()}
    new_df = df.copy()
    new_df[column] = df[column].map(_canonicalize)
    reversal = {"kind": "column_values", "column": column, "original_values": original_values}
    return FixOutcome(True, new_df, reversal)
