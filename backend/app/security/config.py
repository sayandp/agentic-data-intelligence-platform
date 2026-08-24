"""Every hardening limit, in one place, with the reasoning attached.

A limit hard-coded at its call site is a limit nobody can find, review, or
raise for a legitimately large file. These are read from the environment with
a stated default, so an operator can change one without editing code and a
reader can see all of them at once.

The numbers are chosen against what this system is: a local-first analysis
tool that a person points at their own data. They are generous enough that no
honest CSV hits them and tight enough that a single request cannot exhaust the
machine. None of them is a security boundary on its own - see SECURITY.md for
what is and is not defended.
"""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    """An unparseable override is a configuration error, not a reason to
    silently fall back to the default. A limit that quietly reverts is worse
    than no limit, because the operator believes it is in force."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


#: Largest upload accepted, in bytes. Enforced while streaming to disk, so an
#: oversized file is refused partway rather than after being written whole.
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 200 * 1024 * 1024)

#: Largest frame this system will analyse, in rows. Enforced by REFUSING, never
#: by truncating: silently analysing the first N rows of a larger file and
#: reporting the result as the dataset's profile is the exact silent-fallback
#: failure this codebase keeps eliminating. The refusal names the limit and
#: the env var that raises it.
MAX_INGEST_ROWS = _int_env("MAX_INGEST_ROWS", 2_000_000)

#: Largest number of columns. A frame with thousands of columns is almost
#: always a parse gone wrong (a delimiter mis-detected, a header row that is
#: really data) rather than a real dataset, and every per-column analysis in
#: the pipeline is at least linear in this.
MAX_INGEST_COLUMNS = _int_env("MAX_INGEST_COLUMNS", 4096)

#: Requests per window per client, and the window in seconds. Applies only to
#: mutating and expensive endpoints - see app/security/rate_limit.py.
RATE_LIMIT_REQUESTS = _int_env("RATE_LIMIT_REQUESTS", 60)
RATE_LIMIT_WINDOW_SECONDS = _int_env("RATE_LIMIT_WINDOW_SECONDS", 60)


def rate_limit_enabled() -> bool:
    """Off by default.

    This is a local-first tool: the normal deployment is one person on
    localhost, where the limiter would only ever throttle its own operator.
    Turning it on is an explicit decision made when the app is exposed to a
    network, which is the situation it is for. Read at request time rather
    than at import, so a test can enable it without reimporting the app.
    """
    return os.environ.get("RATE_LIMIT_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
