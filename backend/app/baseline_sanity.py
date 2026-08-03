"""Absolute sanity floors on a baseline profile - checks that need no
reference data, only the profile itself.

This is half of the trust-boundary mitigation for baselining (the other half
is Baseline.is_provisional + human confirmation via /approvals). Floors catch
the obviously-broken first-file case algorithmically; provisional-baseline
confirmation catches the subtler, plausible-looking case that needs a human.

Two different failure shapes, deliberately handled differently (Phase 7.5):
  - An identifier-shaped column (cardinality == row count - a primary key, a
    UUID) is NOT evidence the dataset is garbage. It is a column that
    should never have been profiled as a category in the first place -
    app/profiling.py::BaselineProfiler excludes it from profiling entirely
    and records it on profile["excluded_columns"], BEFORE this module ever
    sees the profile. There is no identifier-shaped floor here anymore;
    the whole class of over-rejection this used to cause on any
    transactional table with a primary key is gone by construction, not by
    a check that has to catch it after the fact.
  - Genuine data pathology - a column above the null ceiling, or a
    zero-variance numeric - still rejects the WHOLE baseline. Reviewed
    against the same over-reach identifier-cardinality had, and kept as-is
    deliberately: unlike an identifier, there is no sensible way to
    "exclude and continue" a column whose own data looks broken and still
    trust the rest of the file around it. A column that is 95%+ null, or a
    numeric column with literally zero variance across the whole file, is
    evidence about the FILE, not just that one column's shape.
"""

from __future__ import annotations

NULL_RATE_FLOOR = 0.95

# Below this many rows, "cardinality == row_count" is trivially true for
# almost any categorical column (e.g. 3 rows with 3 distinct city names) and
# isn't a meaningful identifier signal - there just isn't enough data yet to
# tell a real low-N category apart from an identifier. Used by
# app/profiling.py's identifier-shaped exclusion (kept here rather than
# moved, since app/exploration/config.py already depends on this constant's
# location for its own, unrelated cardinality-note floor).
MIN_ROWS_FOR_CARDINALITY_FLOOR = 10


class BaselineSanityError(ValueError):
    """Raised when a computed profile fails one or more sanity floors."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def check_sanity_floors(profile: dict, null_rate_floor: float = NULL_RATE_FLOOR) -> list[str]:
    """Returns a list of human-readable floor-violation reasons (empty if
    none) - genuine data pathology only. Identifier-shaped columns never
    appear here: they were already excluded from profile["columns"] before
    this ever runs."""
    reasons: list[str] = []

    for column, entry in profile.get("columns", {}).items():
        null_rate = entry.get("null_rate", 0.0)
        if null_rate > null_rate_floor:
            reasons.append(
                f"column '{column}' is {null_rate:.1%} null, above the {null_rate_floor:.0%} floor"
            )

        if entry.get("kind") == "numeric":
            std = entry.get("std")
            if std is not None and std == 0.0:
                reasons.append(f"numeric column '{column}' has zero variance")

    if not profile.get("columns") and profile.get("excluded_columns"):
        # Every column was identifier-shaped (or the frame had nothing but
        # such columns) - there is nothing left to baseline against.
        reasons.append("every column was excluded from profiling as identifier-shaped; nothing usable remains to baseline")

    return reasons


def assert_baseline_sane(profile: dict, null_rate_floor: float = NULL_RATE_FLOOR) -> None:
    reasons = check_sanity_floors(profile, null_rate_floor=null_rate_floor)
    if reasons:
        raise BaselineSanityError(reasons)
