"""Numeral extraction and normalized comparison, shared by the number
fidelity and claim coverage post-checks (app/narrative/postchecks.py) -
one regex, one normalization rule, used both directions (does this prose
numeral exist in the claims? does this claim's numeral appear in the
prose?) so the two checks can never quietly disagree on what "the same
number" means.
"""

from __future__ import annotations

import re

# Negative lookbehind/lookahead keep this from matching INSIDE an
# identifier like 'claim-0' or 'attempt-2' - without it, the hyphen in a
# recommendation's own "(see claim-0)" back-reference reads as a minus
# sign, producing a false hallucinated-numeral hit on '-0'.
NUMERAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])-?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9_])")


def extract_raw_numerals(text: str) -> list[str]:
    """Every numeral-shaped token in `text`, unparsed - e.g. '0.31', '31%',
    '1,234.5'."""
    return NUMERAL_PATTERN.findall(text)


def normalized_candidates(token: str, decimals: int = 6) -> set[float]:
    """All the values a single numeral token could reasonably be
    "the same number" as - itself, and its percent/fraction counterpart
    (31% and 0.31 are the same claim stated two ways)."""
    cleaned = token.replace(",", "")
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return set()
    return {round(value, decimals), round(value / 100.0, decimals), round(value * 100.0, decimals)}


def value_forms(value: float, decimals: int = 6) -> set[float]:
    """The mirror of normalized_candidates, starting from an already-parsed
    number (a ClaimValue) rather than prose text."""
    return {round(value, decimals), round(value / 100.0, decimals), round(value * 100.0, decimals)}


def extract_numerals(text: str, decimals: int = 6) -> set[float]:
    """Every numeral in `text`, normalized to every formatting variant it
    could represent - the union across all tokens found."""
    values: set[float] = set()
    for token in extract_raw_numerals(text):
        values |= normalized_candidates(token, decimals)
    return values
