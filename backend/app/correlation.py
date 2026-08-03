"""Deterministic, rule-based grouping of validation failures within a run,
run BEFORE any diagnosis call (Part 4).

This decides what QUESTION to ask the Diagnostic Agent - it never decides
anything about the data itself. That keeps the three-layer separation intact:
detection (Part 3) stays deterministic, diagnosis (Part 4) stays the only
probabilistic layer, action (Part 5) stays a deterministic gate. Correlation
is a second deterministic layer sitting between detection and diagnosis, not
a fourth kind of authority.

Why this exists: a rename produces two independent events (missing_column +
unexpected_column). Fed to the Diagnostic Agent separately, neither event
contains the information needed to distinguish a rename from a coincidental
drop-and-add - "column X is missing" says nothing about whether the data
still exists under a different name. Grouping the pair into one diagnosis
call puts the answer inside the question.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.contract import DataContract
from app.validation.engine import SCHEMA_CONFORMANCE, ValidationFailure

RENAME_PAIR = "missing_unexpected_column_pair"

# A genuine rename preserves dtype and touches no data, so a real pair scores
# ~0. A dtype mismatch alone contributes more than this on its own, which is
# deliberate: renames essentially never change dtype, so any dtype
# disagreement should be enough to refuse the pairing outright.
DEFAULT_MAX_PROFILE_DISTANCE = 1.0
DTYPE_MISMATCH_PENALTY = 10.0


@dataclass
class CorrelatedGroup:
    members: list[ValidationFailure]
    correlation_rule: str | None = field(default=None)  # None => uncorrelated singleton

    @property
    def is_correlated(self) -> bool:
        return self.correlation_rule is not None


def _is_missing_column(failure: ValidationFailure) -> bool:
    return failure.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:missing_column:")


def _is_unexpected_column(failure: ValidationFailure) -> bool:
    return failure.rule_failed.startswith(f"{SCHEMA_CONFORMANCE}:unexpected_column:")


def _profile_distance(
    missing: ValidationFailure,
    unexpected: ValidationFailure,
    baseline_profile: dict,
    contract: DataContract,
) -> float:
    """Lower is more similar. Compares the missing column's baseline profile
    against the unexpected column's current profile on dtype, null rate, and
    (where applicable) cardinality - the same three signals a human would
    eyeball to sanity-check "is this the same column, just relabeled?"."""
    baseline_entry = baseline_profile.get("columns", {}).get(missing.column, {})
    current_series = contract.data[unexpected.column]

    distance = 0.0

    baseline_dtype = baseline_entry.get("dtype")
    current_dtype = str(current_series.dtype)
    if baseline_dtype != current_dtype:
        distance += DTYPE_MISMATCH_PENALTY

    baseline_null_rate = baseline_entry.get("null_rate", 0.0)
    current_null_rate = float(current_series.isna().mean()) if len(current_series) else 0.0
    distance += abs(baseline_null_rate - current_null_rate)

    baseline_cardinality = baseline_entry.get("cardinality")
    if baseline_cardinality is not None and not pd.api.types.is_numeric_dtype(current_series):
        current_cardinality = int(current_series.dropna().nunique())
        denom = max(baseline_cardinality, current_cardinality, 1)
        distance += abs(baseline_cardinality - current_cardinality) / denom

    return distance


def correlate_events(
    failures: list[ValidationFailure],
    baseline_profile: dict,
    contract: DataContract,
    max_profile_distance: float = DEFAULT_MAX_PROFILE_DISTANCE,
) -> list[CorrelatedGroup]:
    """Groups missing_column/unexpected_column pairs that look like renames.

    Candidate pairs are scored by profile similarity and matched greedily,
    closest pair first, so the most confident pairing wins when several
    missing/unexpected columns exist in the same run. A pair is only formed
    if its distance clears `max_profile_distance` - past that, both events
    are left uncorrelated rather than forced into a guess. This is exactly
    what stops a genuine drop-plus-unrelated-addition from being merged into
    a false "rename".
    """
    missing = [f for f in failures if _is_missing_column(f)]
    unexpected = [f for f in failures if _is_unexpected_column(f)]
    other = [f for f in failures if not _is_missing_column(f) and not _is_unexpected_column(f)]

    candidates = []
    for mi, m in enumerate(missing):
        for ui, u in enumerate(unexpected):
            distance = _profile_distance(m, u, baseline_profile, contract)
            if distance <= max_profile_distance:
                candidates.append((distance, mi, ui))
    candidates.sort(key=lambda c: c[0])

    used_missing: set[int] = set()
    used_unexpected: set[int] = set()
    groups: list[CorrelatedGroup] = []

    for _distance, mi, ui in candidates:
        if mi in used_missing or ui in used_unexpected:
            continue
        used_missing.add(mi)
        used_unexpected.add(ui)
        groups.append(CorrelatedGroup(members=[missing[mi], unexpected[ui]], correlation_rule=RENAME_PAIR))

    for mi, m in enumerate(missing):
        if mi not in used_missing:
            groups.append(CorrelatedGroup(members=[m]))
    for ui, u in enumerate(unexpected):
        if ui not in used_unexpected:
            groups.append(CorrelatedGroup(members=[u]))
    for f in other:
        groups.append(CorrelatedGroup(members=[f]))

    return groups
