"""Turning a crop-statistics export into a table at a known grain.

Deterministic. Every transformation is recorded and reported - a number a
reader cannot trace back to a formula and a grain is a number they cannot act
on.

THREE THINGS THIS FILE IS CAREFUL ABOUT.

ZERO AREA. Yield is production divided by area, and a district-crop with zero
recorded area is common in real crop statistics (a crop listed but not sown).
Dividing produces `inf`, which propagates silently through a mean and emerges
as a plausible-looking average. Every such row is counted, excluded from the
derived column as NULL, and REPORTED - never an infinity, never a silent NaN.

LABEL NORMALISATION IS RECORDED. Real season labels arrive as "Kharif     ",
"kharif" and "Kharif"; districts as "PALAKKAD" and "Palakkad". Collapsing them
is necessary to group at all, and doing it silently would mean a reader cannot
tell whether two districts merged because they are the same place or because
the normaliser was too aggressive. Every collapse is listed.

THE GRAIN IS STATED. A yield averaged over district-crop-season is a different
number from one averaged over district-crop, and both look reasonable. The
grain is configurable and travels with the output.

Generic data quality - nulls, drift, type coercion - is NOT this pack's job.
It has already run (app/validation/) before anything here executes.
"""

from __future__ import annotations

import pandas as pd

from app.agriculture.config import AgricultureConfig
from app.agriculture.findings import DerivedMetric, PreprocessingReport
from app.analytics.roles import ColumnRole, RoleDetection

#: How each measure is combined when rows collapse to the grain. Sums for
#: extensive quantities, mean for intensive ones - summing a rainfall figure
#: across districts would produce a number with no physical meaning.
AGGREGATION: dict[ColumnRole, str] = {
    ColumnRole.AREA_CULTIVATED: "sum",
    ColumnRole.PRODUCTION_QUANTITY: "sum",
    ColumnRole.RAINFALL: "mean",
    ColumnRole.MARKET_PRICE: "mean",
    ColumnRole.CROP_YIELD: "mean",
}

#: The derived yield column's name. Distinct from any source column so it can
#: never collide with a yield the export already carried.
DERIVED_YIELD = "derived_yield"


def _normalise_labels(work: pd.DataFrame, column: str, role: ColumnRole) -> list[dict]:
    """Trim and title-case a label column, recording every collapse."""
    original = work[column].astype("string")
    cleaned = original.str.strip().str.replace(r"\s+", " ", regex=True).str.title()

    collapses: list[dict] = []
    grouped: dict[str, set[str]] = {}
    for before, after in zip(original.dropna(), cleaned.dropna()):
        grouped.setdefault(str(after), set()).add(str(before))
    for after, befores in sorted(grouped.items()):
        if len(befores) > 1 or any(b != after for b in befores):
            collapses.append(
                {
                    "role": role.value,
                    "column": column,
                    "normalised_to": after,
                    "from": sorted(befores),
                }
            )

    work[column] = cleaned
    return collapses


def prepare(
    df: pd.DataFrame, detection: RoleDetection, config: AgricultureConfig
) -> tuple[pd.DataFrame, PreprocessingReport, dict[ColumnRole, str]]:
    """The prepared frame, a report of every transformation, and the resolved
    role -> column mapping the rules read."""
    assigned = {role: candidate.column for role, candidate in detection.assigned().items()}
    rows_in = len(df)
    work = df.copy()
    normalisations: list[dict] = []

    # 1. Label columns, before anything groups by them.
    if config.normalise_labels:
        for role in (ColumnRole.DISTRICT, ColumnRole.CROP, ColumnRole.SEASON):
            column = assigned.get(role)
            if column and column in work.columns:
                normalisations.extend(_normalise_labels(work, column, role))

    # 2. Aggregate to the stated grain. Crop year, when present, is part of
    #    the grain even though it is not in the configured tuple: collapsing
    #    2018 and 2019 into one row would silently average across years and
    #    make every history-based rule compare a district with itself.
    grain_roles = [r for r in config.grain if assigned.get(r)]
    if assigned.get(ColumnRole.CROP_YEAR):
        grain_roles = [*grain_roles, ColumnRole.CROP_YEAR]
    grain_columns = [assigned[r] for r in grain_roles]

    aggregations: dict[str, str] = {}
    for role, how in AGGREGATION.items():
        column = assigned.get(role)
        if column and column in work.columns and column not in grain_columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
            aggregations[column] = how

    if grain_columns and aggregations:
        work = work.groupby(grain_columns, observed=True, dropna=False).agg(aggregations).reset_index()

    # 3. Derive yield where the export did not carry one.
    derived: list[DerivedMetric] = []
    area_col = assigned.get(ColumnRole.AREA_CULTIVATED)
    production_col = assigned.get(ColumnRole.PRODUCTION_QUANTITY)
    existing_yield = assigned.get(ColumnRole.CROP_YIELD)

    if existing_yield and existing_yield in work.columns:
        work[DERIVED_YIELD] = pd.to_numeric(work[existing_yield], errors="coerce")
        derived.append(
            DerivedMetric(
                metric=DERIVED_YIELD,
                formula=f"`{existing_yield}` as supplied",
                source_columns=[existing_yield],
                undefined_row_count=int(work[DERIVED_YIELD].isna().sum()),
                undefined_reason="the export carried its own yield column; it is used as supplied, not recomputed",
            )
        )
    elif area_col and production_col and {area_col, production_col} <= set(work.columns):
        area = pd.to_numeric(work[area_col], errors="coerce")
        production = pd.to_numeric(work[production_col], errors="coerce")

        # ZERO AREA. Never an infinity, never a silent NaN: the rows are
        # counted and reported, and the yield is absent for them.
        zero_area = int((area == 0).sum())
        undefined_area = int(area.isna().sum())
        safe_area = area.where(area > 0)
        work[DERIVED_YIELD] = production / safe_area

        undefined = int(work[DERIVED_YIELD].isna().sum())
        reasons = []
        if zero_area:
            reasons.append(
                f"{zero_area} row(s) recorded zero area cultivated - yield is undefined for them and is "
                "reported as absent rather than as an infinite or zero value"
            )
        if undefined_area:
            reasons.append(f"{undefined_area} row(s) had no area value at all")
        derived.append(
            DerivedMetric(
                metric=DERIVED_YIELD,
                formula=f"`{production_col}` / `{area_col}`",
                source_columns=[production_col, area_col],
                undefined_row_count=undefined,
                undefined_reason="; ".join(reasons) if reasons else None,
            )
        )

    report = PreprocessingReport(
        rows_in=rows_in,
        rows_out=int(len(work)),
        grain=[assigned[r] for r in grain_roles],
        aggregations=aggregations,
        normalisations=normalisations,
        derived=derived,
    )
    return work, report, assigned
