"""Exponential backoff with jitter, shared between anything that retries a
flaky remote call: the Diagnostic Agent's LLM requests (Phase 2 Part 4) and
the API connector's HTTP requests (Phase 3). Same retry math, different
callers - each keeps its own retry loop since the exceptions/status codes
being caught differ, but the delay calculation is not duplicated.
"""

from __future__ import annotations

import random

DEFAULT_BASE_BACKOFF_SECONDS = 1.0


def backoff_delay_seconds(
    attempt: int, base_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS, rng: random.Random | None = None
) -> float:
    """`attempt` is 1-indexed (the attempt that just failed). Returns seconds
    to sleep before the next attempt: base * 2**(attempt-1), plus up to that
    much jitter, so retries from concurrent callers don't all wake up at once."""
    rng = rng or random.Random()
    base = base_seconds * (2 ** (attempt - 1))
    return base + rng.uniform(0, base)
