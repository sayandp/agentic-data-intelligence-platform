"""Role detection parses each column once, and looks_numeric stops early.

Run 1 - a 1,067,371-row retail export - sat "completed" for 16 minutes before
its report existed, 9 of them with no exploration findings at all. The cost
was role detection: `_numeric_non_null` was called 129 times for an 8-column
frame, once per scorer per column, and each call re-parsed every value in pure
Python. Detection measured 16.7s at 50k rows, 66s at 200k and 350s at the full
file before; 0.58s, 2.45s and 12.4s after. The whole pipeline on that file, in
isolation: 806s before, 147s after.

Both changes are meant to leave every output identical. That was checked
against HEAD over 46 CSVs (5,905 candidates, 0 differences); these tests keep
it true without a second checkout.

Timing is deliberately NOT asserted: a wall-clock bound fails on a busy CI
machine and passes on a fast one whatever the code does. The CALL COUNT is
the property that regressed, and it is deterministic.
"""

from __future__ import annotations

import random

import pandas as pd

from app.analytics import roles
from app.numeric_text import looks_numeric, parse_number
from app.semantic_roles import detect_for_run


def _mixed_frame(rows: int = 400) -> pd.DataFrame:
    rng = random.Random(3)
    return pd.DataFrame(
        {
            "Invoice": [f"INV{rng.randint(1000, 9999)}" for _ in range(rows)],
            "Description": [rng.choice(["RED MUG", "BLUE BOWL", "tea set of 3"]) for _ in range(rows)],
            "Spend": [f"${rng.uniform(1, 900):,.2f}" for _ in range(rows)],
            "CTR": [f"{rng.uniform(0, 9):.2f}%" for _ in range(rows)],
            "Quantity": [rng.randint(1, 40) for _ in range(rows)],
            "Price": [round(rng.uniform(0.5, 30), 2) for _ in range(rows)],
            "Crop_Year": [rng.randint(2015, 2022) for _ in range(rows)],
            "Country": [rng.choice(["UK", "France", "EIRE"]) for _ in range(rows)],
        }
    )


def _count_parses(monkeypatch) -> list[int]:
    calls = [0]
    real = roles._numeric_non_null_uncached

    def counting(series):
        calls[0] += 1
        return real(series)

    monkeypatch.setattr(roles, "_numeric_non_null_uncached", counting)
    return calls


def test_each_column_is_parsed_at_most_once_per_detection_pass(monkeypatch):
    frame = _mixed_frame()
    calls = _count_parses(monkeypatch)

    detect_for_run(frame)

    # Was 129 for an 8-column frame before the per-pass cache.
    assert calls[0] <= len(frame.columns), (
        f"{calls[0]} parses for {len(frame.columns)} columns - a column is being re-parsed per scorer"
    )


def test_the_cache_does_not_outlive_its_detection_pass(monkeypatch):
    """A cache keyed on a Series' id() is only sound while that Series is
    alive. It must be gone when detect() returns, so a later pass - or any
    caller outside detect() - never reads an entry for a different object."""
    frame = _mixed_frame()
    detect_for_run(frame)
    assert roles._PASS_CACHE.get() is None

    calls = _count_parses(monkeypatch)
    roles._numeric_non_null(frame["Spend"])
    roles._numeric_non_null(frame["Spend"])
    assert calls[0] == 2, "outside a detection pass, nothing may be cached"


def test_the_cache_is_dropped_even_when_detection_raises(monkeypatch):
    def boom(self, df):
        raise RuntimeError("scorer failure")

    monkeypatch.setattr(roles.SemanticColumnDetector, "_detect", boom)
    try:
        detect_for_run(_mixed_frame())
    except RuntimeError:
        pass
    assert roles._PASS_CACHE.get() is None


def test_detection_output_is_unchanged_by_the_cache(monkeypatch):
    """The cache must change how often a column is parsed, never what the
    detector concludes. Compared against the same pass with caching
    disabled, candidate by candidate."""
    frame = _mixed_frame()

    def flatten(det):
        return sorted((c.column, c.role.value, round(c.score, 12), tuple(c.reasons)) for c in det.candidates)

    cached = flatten(detect_for_run(frame))

    monkeypatch.setattr(roles, "_numeric_non_null", roles._numeric_non_null_uncached)
    uncached = flatten(detect_for_run(frame))

    assert cached == uncached


def _looks_numeric_full_scan(series: pd.Series, minimum_parse_rate: float = 0.9) -> bool:
    """The implementation before the early exit, verbatim."""
    if pd.api.types.is_numeric_dtype(series):
        return True
    non_null = series.dropna()
    if non_null.empty:
        return False
    parsed = sum(1 for v in non_null if parse_number(v) is not None)
    return parsed / len(non_null) >= minimum_parse_rate


def test_looks_numeric_early_exit_gives_the_full_scan_answer():
    """Randomised, plus every parsed-count at several sizes and rates so
    both exits are exercised right at the threshold. Stopping early may only
    change how many values are parsed, never the answer."""
    rng = random.Random(11)
    numeric_like = ["$1,234.50", "12%", "7", "3.5", "1.234,56", "-4"]
    text_like = ["RED MUG", "n/a", "abc", "", "tea set"]
    checked = 0
    for _ in range(3000):
        n = rng.randint(1, 120)
        share = rng.random()
        values = [rng.choice(numeric_like) if rng.random() < share else rng.choice(text_like) for _ in range(n)]
        if rng.random() < 0.2:
            values[rng.randrange(n)] = None
        series = pd.Series(values, dtype="object")
        for rate in (0.9, 0.5, 0.95, 0.7, 1.0):
            assert looks_numeric(series, rate) == _looks_numeric_full_scan(series, rate), (values, rate)
            checked += 1

    # Exact boundaries: every k for sizes where rate * n is not exact.
    for n in (7, 10, 30, 70, 101):
        for k in range(n + 1):
            series = pd.Series(["5"] * k + ["x"] * (n - k), dtype="object")
            for rate in (0.9, 0.7, 0.33):
                assert looks_numeric(series, rate) == _looks_numeric_full_scan(series, rate), (n, k, rate)
                checked += 1
    assert checked > 15000


def test_looks_numeric_stops_parsing_once_the_answer_is_decided(monkeypatch):
    """The early exit exists for free text: a million-row Description column
    used to be parsed end to end to conclude it was not a number column."""
    import app.numeric_text as numeric_text

    calls = [0]
    real = numeric_text.parse_number

    def counting(v):
        calls[0] += 1
        return real(v)

    monkeypatch.setattr(numeric_text, "parse_number", counting)

    free_text = pd.Series(["RED MUG"] * 10_000, dtype="object")
    assert numeric_text.looks_numeric(free_text) is False
    # The 90% rate is out of reach after about 10% of values fail.
    assert calls[0] <= 1_100, f"parsed {calls[0]} of 10,000 values to reject a free-text column"

    # And the other exit: a formatted-number column is accepted once 90% of
    # it has parsed, without reading the last tenth.
    calls[0] = 0
    money = pd.Series(["$1,234.50"] * 10_000, dtype="object")
    assert numeric_text.looks_numeric(money) is True
    assert calls[0] <= 9_000, f"parsed {calls[0]} of 10,000 values to accept a number column"
