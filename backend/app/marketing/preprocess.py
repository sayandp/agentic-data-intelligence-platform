"""Ad-export quirks, and nothing else.

SCOPE BOUNDARY. Nulls, dtypes, encoding, delimiters, drift and repair are
the Data-Quality agent's job and are already done by the time this runs -
this consumes the REPAIRED frame. Two cleaning paths that must agree is the
failure this project keeps hitting, so nothing here re-cleans anything.

What IS here is the handful of things specific to ad-platform exports that
no generic agent could know about:

  - `$1,234.56` and `1 234,56 €` are money that arrived as text, because the
    platform formatted it for a human
  - a CTR column that is sometimes 0.023 and sometimes 2.3 depending on which
    export button was pressed
  - a trailing "Total" row that already contains the sum of every row above
    it, which would double every number on the page if summed
  - a grain that varies with whatever the user picked in the platform UI

Every one of those is a property of ad exports, not of CSVs in general.

NO LLM. Deterministic: same input, same output, always.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analytics.roles import ColumnRole, RoleDetection
from app.numeric_text import coerce_numeric
from app.marketing.config import MarketingConfig
from app.marketing.findings import DerivedMetric, PreprocessingReport

def parse_money_series(series: pd.Series) -> tuple[pd.Series, dict | None]:
    """A money column that arrived as text, as numbers.

    Returns the parsed series and a note describing what was done, or None
    when the column was already numeric. The note is what lets a reader tell
    a value that was in the file from one this module reconstructed.

    Parsing itself is app/numeric_text.py - the same function the role
    detector scores with, so a column cannot be read as money by one and as
    text by the other.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series, None

    sample = series.dropna()
    if sample.empty:
        return coerce_numeric(series), None

    parsed = coerce_numeric(series)
    note = {
        "column": str(series.name),
        "transformation": "parsed currency-formatted text to a number",
        "example_in": str(sample.iloc[0]),
        "example_out": float(parsed.dropna().iloc[0]) if not parsed.dropna().empty else None,
        "unparsed_row_count": int(parsed.isna().sum() - series.isna().sum()),
    }
    return parsed, note


def parse_ratio_series(series: pd.Series, percent_threshold: float) -> tuple[pd.Series, dict | None]:
    """A CTR column, as a FRACTION, whichever way the platform wrote it.

    `2.3%`, `2.3` and `0.023` all mean the same rate. The convention is
    decided per COLUMN from its median, never per value: a single row cannot
    tell you which convention it is in, and mixing the two inside one column
    would silently rescale part of the data.
    """
    if pd.api.types.is_numeric_dtype(series):
        numeric = series.astype("float64")
        had_percent_sign = False
    else:
        had_percent_sign = bool(series.astype("string").str.strip().str.endswith("%").any())
        numeric = coerce_numeric(series)

    non_null = numeric.dropna()
    if non_null.empty:
        return numeric, None

    median = float(non_null.median())
    is_percent = had_percent_sign or median > percent_threshold
    if not is_percent:
        return numeric, ({"column": str(series.name), "transformation": "read as a fraction; left unchanged"} if had_percent_sign is False else None)

    return numeric / 100.0, {
        "column": str(series.name),
        "transformation": "read as percentage points and divided by 100",
        "basis": (
            "column carried a % sign"
            if had_percent_sign
            else f"median {median:.4g} is above the {percent_threshold:.4g} fraction/percent threshold"
        ),
    }


def drop_summary_rows(df: pd.DataFrame, campaign_column: str | None, labels: tuple[str, ...]) -> tuple[pd.DataFrame, list[str], int]:
    """Remove the platform's own total rows.

    Meta and Google both append one. It already contains the sum of every
    row above it, so leaving it in doubles every total on the page - and it
    looks like an ordinary row to anything generic, because it IS one.
    """
    if campaign_column is None or campaign_column not in df.columns:
        return df, [], 0

    lowered = df[campaign_column].astype("string").str.strip().str.casefold()
    wanted = {label.casefold() for label in labels}
    mask = lowered.isin(wanted)
    matched = sorted({str(v) for v in df.loc[mask, campaign_column].unique()})
    return df.loc[~mask].copy(), matched, int(mask.sum())


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division that yields NO VALUE where the denominator is zero.

    Never an infinity, never a silent NaN standing in for one. An ad set
    with zero conversions has no CPA - that is a fact about the ad set, and
    the caller reports it as such.
    """
    # numpy NaN rather than pd.NA: these are float64 columns, and pd.NA
    # propagates as an object dtype that float() then refuses.
    safe_denominator = denominator.astype("float64").replace(0.0, np.nan)
    result = numerator.astype("float64") / safe_denominator
    return result.replace([np.inf, -np.inf], np.nan)


def prepare(
    df: pd.DataFrame, detection: RoleDetection, config: MarketingConfig
) -> tuple[pd.DataFrame, PreprocessingReport, dict[ColumnRole, str]]:
    """The repaired frame, as an ad-performance table at a known grain.

    Returns the prepared frame, a report of every transformation applied,
    and the resolved role->column mapping the rules will read.
    """
    assigned = {role: candidate.column for role, candidate in detection.assigned().items()}
    rows_in = len(df)
    normalisations: list[dict] = []

    work = df.copy()

    # 1. Platform summary rows, before anything is summed.
    campaign_col = assigned.get(ColumnRole.CAMPAIGN_ID)
    work, matched_labels, summary_dropped = drop_summary_rows(work, campaign_col, config.summary_row_labels)

    # 2. Money columns that arrived formatted for a human.
    for role in (ColumnRole.SPEND, ColumnRole.CONVERSION_VALUE):
        column = assigned.get(role)
        if column and column in work.columns:
            work[column], note = parse_money_series(work[column])
            if note:
                normalisations.append(note)

    # 3. The CTR convention, decided per column.
    ctr_col = assigned.get(ColumnRole.CTR)
    if ctr_col and ctr_col in work.columns:
        work[ctr_col], note = parse_ratio_series(work[ctr_col], config.ctr_percent_detection_threshold)
        if note:
            normalisations.append(note)

    # 4. Counts, in case they arrived with thousands separators.
    for role in (ColumnRole.IMPRESSIONS, ColumnRole.CLICKS, ColumnRole.CONVERSIONS):
        column = assigned.get(role)
        if column and column in work.columns and not pd.api.types.is_numeric_dtype(work[column]):
            work[column], note = parse_money_series(work[column])
            if note:
                note["transformation"] = "parsed thousands-separated text to a number"
                normalisations.append(note)

    # 5. One consistent grain, whatever the export was set to.
    work = _aggregate_to_grain(work, assigned, config)

    # 6. Derived metrics, each recorded with its formula.
    work, derived = _derive_metrics(work, assigned)

    report = PreprocessingReport(
        grain=config.grain,
        rows_in=rows_in,
        rows_out=len(work),
        summary_rows_excluded=summary_dropped,
        summary_row_labels_matched=matched_labels,
        normalisations=normalisations,
        derived_metrics=derived,
    )
    return work, report, assigned


def _aggregate_to_grain(df: pd.DataFrame, assigned: dict[ColumnRole, str], config: MarketingConfig) -> pd.DataFrame:
    """Sum the additive columns to one row per ad set per day.

    Only additive measures are summed. A ratio is NOT summed - CTR and
    frequency are recomputed from their components afterwards, because the
    mean of a ratio is not the ratio of the means and a summed CTR is
    meaningless.
    """
    date_col = assigned.get(ColumnRole.EVENT_DATE)
    campaign_col = assigned.get(ColumnRole.CAMPAIGN_ID)
    if date_col is None or date_col not in df.columns:
        return df

    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce").dt.floor("D")

    keys = [k for k in (campaign_col, date_col) if k and k in work.columns]
    if not keys:
        return work

    additive = [
        assigned[role]
        for role in (ColumnRole.SPEND, ColumnRole.IMPRESSIONS, ColumnRole.CLICKS, ColumnRole.CONVERSIONS, ColumnRole.CONVERSION_VALUE)
        if assigned.get(role) and assigned[role] in work.columns
    ]
    # Frequency is a ratio and cannot be summed; the mean across rows of the
    # same ad set and day is the closest honest aggregate.
    averaged = [c for c in [assigned.get(ColumnRole.FREQUENCY)] if c and c in work.columns]

    if not additive and not averaged:
        return work

    spec = {**{c: "sum" for c in additive}, **{c: "mean" for c in averaged}}
    grouped = work.groupby(keys, dropna=False, as_index=False).agg(spec)
    return grouped


def _derive_metrics(df: pd.DataFrame, assigned: dict[ColumnRole, str]) -> tuple[pd.DataFrame, list[DerivedMetric]]:
    """CPA, ROAS and CTR from their components, each with its formula
    recorded and its zero-denominator rows counted."""
    work = df.copy()
    derived: list[DerivedMetric] = []

    spend = assigned.get(ColumnRole.SPEND)
    conversions = assigned.get(ColumnRole.CONVERSIONS)
    value = assigned.get(ColumnRole.CONVERSION_VALUE)
    clicks = assigned.get(ColumnRole.CLICKS)
    impressions = assigned.get(ColumnRole.IMPRESSIONS)

    if spend in work.columns and conversions in work.columns:
        work["cpa"] = _safe_divide(work[spend], work[conversions])
        undefined = int(work["cpa"].isna().sum())
        derived.append(
            DerivedMetric(
                metric="cpa",
                formula=f"`{spend}` / `{conversions}`",
                source_columns=[spend, conversions],
                undefined_row_count=undefined,
                undefined_reason=(
                    f"{undefined} row(s) recorded zero conversions - cost per acquisition is undefined "
                    "for them and is reported as absent rather than as a number"
                )
                if undefined
                else None,
            )
        )

    if value in work.columns and spend in work.columns:
        work["roas"] = _safe_divide(work[value], work[spend])
        undefined = int(work["roas"].isna().sum())
        derived.append(
            DerivedMetric(
                metric="roas",
                formula=f"`{value}` / `{spend}`",
                source_columns=[value, spend],
                undefined_row_count=undefined,
                undefined_reason=f"{undefined} row(s) recorded zero spend - return on ad spend is undefined for them"
                if undefined
                else None,
            )
        )

    # CTR is RECOMPUTED from clicks and impressions after aggregation even
    # when the export carried its own column: an exported CTR is a ratio at
    # the export's grain, and this table is at a different one.
    if clicks in work.columns and impressions in work.columns:
        work["ctr"] = _safe_divide(work[clicks], work[impressions])
        undefined = int(work["ctr"].isna().sum())
        derived.append(
            DerivedMetric(
                metric="ctr",
                formula=f"`{clicks}` / `{impressions}`",
                source_columns=[clicks, impressions],
                undefined_row_count=undefined,
                undefined_reason=f"{undefined} row(s) recorded zero impressions - click-through rate is undefined for them"
                if undefined
                else None,
            )
        )

    return work, derived
