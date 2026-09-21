"""Recovering a number from a string a platform formatted for a human.

`$1,234.56`, `1.234,56`, `2.3%` and `10 000` are all numbers that arrived as
text because an export was written for a person to read. This is the ONE
implementation of turning them back into numbers.

It lives here, outside any agent package, because two callers need it and
they must not disagree:

    app/analytics/roles.py       to SCORE a column - can this be money? - by
                                 looking through the formatting, without ever
                                 modifying the frame
    app/marketing/preprocess.py  to actually TRANSFORM the column for analysis

A second copy of this logic is exactly the failure this project keeps
hitting, so there is one function and both import it.

NOT a data-cleaning path. The Data-Quality agent owns nulls, dtypes,
encoding and repair, and has already run by the time either caller uses
this. This is narrower: a formatted number is still a number, and reading it
is not the same as repairing the data.
"""

from __future__ import annotations

import re

import pandas as pd

#: Everything that is not a digit, a separator or a sign. Deliberately a
#: character class rather than a locale library: the goal is to recover a
#: number from a formatted string, not to interpret locale semantics the
#: export never declared.
_NOISE = re.compile(r"[^\d,.\-]")

#: A number written the European way - `1.234,56`. Recognised by the LAST
#: separator being a comma, which is unambiguous whatever the grouping.
_EU_DECIMAL = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d+$")


def parse_number(value: object) -> float | None:
    """One formatted value as a float, or None when it is not a number.

    None rather than NaN so a caller can tell "this cell was empty" from
    "this cell held something that is not a number at all".
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)

    raw = str(value).strip()
    if not raw:
        return None

    stripped = _NOISE.sub("", raw)
    if not stripped or stripped in {"-", ".", ",", "-."}:
        return None

    if _EU_DECIMAL.match(stripped):
        stripped = stripped.replace(".", "").replace(",", ".")
    else:
        # Anglo grouping: commas are thousands separators and drop out.
        stripped = stripped.replace(",", "")

    try:
        return float(stripped)
    except ValueError:
        return None


def coerce_numeric(series: pd.Series) -> pd.Series:
    """A possibly-formatted column as floats.

    Returns the series unchanged when it is already numeric, so callers can
    use this unconditionally without paying for a no-op conversion.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series
    return pd.Series([parse_number(v) for v in series], index=series.index, dtype="float64")


def looks_numeric(series: pd.Series, minimum_parse_rate: float = 0.9) -> bool:
    """Whether a text column is really a formatted number column.

    A parse rate rather than a single sample: one `$5.00` in a column of
    free text does not make it money, and a threshold is the honest way to
    say how sure we are.
    """
    if pd.api.types.is_numeric_dtype(series):
        return True
    non_null = series.dropna()
    if non_null.empty:
        return False
    # Stops as soon as the answer is decided, in either direction. The result
    # is exactly the full-scan result - only the number of values parsed
    # changes. It matters for free text: a million-row Description column
    # used to be parsed end to end to conclude, after the first few hundred
    # values, that it was not a number column.
    #
    # Every comparison is the ORIGINAL expression, `parsed / total >= rate`,
    # rather than a rearrangement like `parsed >= rate * total`. A search of
    # 10 rates across every size up to 4,000 found no input where the two
    # disagree, so this is not fixing a known rounding case - it is choosing
    # "the same answer" by construction over "the same answer on the inputs
    # someone searched". Both exits are sound because `parsed` only grows:
    # once the rate is reached it cannot be lost, and once even parsing every
    # remaining value could not reach it, it cannot be won.
    total = len(non_null)
    parsed = 0
    for seen, v in enumerate(non_null, start=1):
        if parse_number(v) is not None:
            parsed += 1
            if parsed / total >= minimum_parse_rate:
                return True
        elif (parsed + (total - seen)) / total < minimum_parse_rate:
            return False
    return parsed / total >= minimum_parse_rate
