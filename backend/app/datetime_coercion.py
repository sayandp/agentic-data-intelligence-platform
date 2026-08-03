"""Shared datetime detection/coercion, run exactly once - at connector fetch
time - so profiling, exploration, and modeling downstream never each run
their own heuristic and never disagree about the same column (the actual
Phase 7.5 defect: Modeling had grown a module-local
_looks_like_datetime nothing else shared). Every consumer downstream of a
connector's fetch() answers "is this a datetime column" with the single
dtype check in app/column_kind.py; this module is where that dtype gets
decided, not inferred piecemeal later.

DETECTION PRIORITY, in order:
  1. Already datetime64 - used as-is, no inference attempted.
  2. A declared SQL type is AUTHORITATIVE (app/connectors/sql_connector.py's
     own docstring: "dtype confidence is a property of the source format,
     not the data"). A column whose reflected SQL type is DATE/TIME/
     TIMESTAMP-shaped is coerced unconditionally - never gated behind a
     parse-rate threshold, because the source has already told us the type
     with certainty a heuristic never could.
  3. An object column with no type metadata at all (CSV, API JSON, an Excel
     cell pandas didn't already parse) is a parse-rate GUESS, and guesses
     get a floor: coerced only if parsing succeeds on nearly all non-null
     values (DEFAULT_MIN_PARSEABLE_RATIO). A column that partially parses -
     most values look like dates, some don't - is NOT a coercion decision,
     it's a data-quality signal (a genuinely corrupt/mixed-format column
     look identical to an "ambiguous type" if silently null'd out with
     errors="coerce"). That column is left untouched here and reported via
     `parse_attempts` for app/validation/engine.py's own rule to raise as a
     proper, diagnosable event - never silently coerced-and-nulled.

COERCION IS NEVER SILENT - `coercions` and `parse_attempts` are both meant
to be merged into DataContract.connector_metadata by every caller, per this
project's own prior history of un-loggable fallbacks (latin-1 decoding,
httpx params={}, read_excel ignoring cell formats, json default=str) each
having cost a debugging session before being made observable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

DEFAULT_MIN_PARSEABLE_RATIO = 0.99

# Mirrors app/connectors/sql_connector.py's own _TEMPORAL_MARKERS exactly -
# duplicated as a constant (not imported) so this module has zero dependency
# on any specific connector; SQL is simply the one caller with a
# declared_schema to pass in.
_TEMPORAL_SQL_MARKERS = ("DATE", "TIME")


def _is_declared_temporal(declared_type: str) -> bool:
    upper = declared_type.upper()
    return any(marker in upper for marker in _TEMPORAL_SQL_MARKERS)


@dataclass
class DatetimeNormalizationResult:
    data: pd.DataFrame
    # Columns actually coerced to datetime64 - [{"column", "method",
    # "parse_success_rate", ...}]. Always safe to merge verbatim into
    # connector_metadata["datetime_coercions"].
    coercions: list[dict] = field(default_factory=list)
    # Every object column examined for the parse-rate path (2 or 3 above),
    # coerced or not - {column: {"parse_rate": float, "coerced": bool}}.
    # app/validation/engine.py's datetime_partial_parse rule reads this
    # directly; a column with coerced=False and 0 < parse_rate < 1 is
    # exactly the "left alone, flagged instead" case.
    parse_attempts: dict[str, dict] = field(default_factory=dict)


def normalize_datetime_columns(
    df: pd.DataFrame,
    declared_schema: dict[str, str] | None = None,
    min_parseable_ratio: float = DEFAULT_MIN_PARSEABLE_RATIO,
) -> DatetimeNormalizationResult:
    declared_schema = declared_schema or {}
    working = df.copy(deep=False)
    coercions: list[dict] = []
    parse_attempts: dict[str, dict] = {}

    for column in df.columns:
        series = df[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            continue  # priority 1: already normalized, nothing to infer
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue  # never a datetime candidate, declared type or not

        declared_type = declared_schema.get(column)
        if declared_type is not None and _is_declared_temporal(declared_type):
            non_null = series.dropna()
            coerced = pd.to_datetime(series, errors="coerce")
            success_rate = float(coerced.notna().sum() / len(non_null)) if len(non_null) else 1.0
            working[column] = coerced
            coercions.append(
                {"column": column, "method": "declared_schema", "declared_type": declared_type, "parse_success_rate": success_rate}
            )
            continue  # priority 2: authoritative - never falls through to the parse-rate guess below

        non_null = series.dropna()
        if non_null.empty:
            continue
        # pandas' free-text date parser is liberal enough to accept a bare
        # month name ("jan") as a valid date (defaulting year/day) - which
        # would otherwise silently misclassify an ordinary categorical
        # column (three rows of "jan"/"feb") as a datetime column. Real
        # date representations - ISO, US, "Jan 5 2022" - all contain at
        # least one digit; a value with none is never even attempted, and
        # counts as a parse failure, not an excluded data point.
        str_values = non_null.astype(str)
        has_digit = str_values.str.contains(r"\d", regex=True)
        if not has_digit.any():
            continue
        parsed = pd.to_datetime(str_values.where(has_digit), errors="coerce", format="mixed")
        parse_rate = float(parsed.notna().mean())
        if parse_rate == 0.0:
            continue  # not date-like at all - not even worth recording as an attempt

        coerced = parse_rate >= min_parseable_ratio
        parse_attempts[column] = {"parse_rate": parse_rate, "coerced": coerced}
        if coerced:
            working[column] = pd.to_datetime(series, errors="coerce", format="mixed")
            coercions.append({"column": column, "method": "parsed_text", "parse_success_rate": parse_rate})
        # else: priority 3, partial parse - left untouched, deliberately.

    return DatetimeNormalizationResult(data=working, coercions=coercions, parse_attempts=parse_attempts)
