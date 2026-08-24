"""A fixed-window rate limiter for the expensive endpoints, exempting local
callers.

WHY LOCALHOST IS EXEMPT. This is a local-first tool. The normal deployment is
one person running start.ps1 and driving the dashboard from the same machine,
where every request arrives from 127.0.0.1. A limiter that throttled that
would only ever get in the way of its own operator, and the first thing anyone
would do is turn it off - which is worse than not having it, because it would
be off everywhere including the one deployment that needs it. Exempting local
callers is what lets the limit stay ON when the app is exposed.

WHAT IT PROTECTS AGAINST. One remote client tying up the process with repeated
ingests or model-backed questions. Each of those is seconds to minutes of CPU
and, for the model paths, real money.

WHAT IT DOES NOT PROTECT AGAINST, stated plainly because a limiter invites
more confidence than it earns:

  - A distributed source. The window is per client address; N addresses get N
    windows. This is not DDoS protection and cannot be.
  - A spoofed or proxied address. The client address is taken from the
    connection, never from X-Forwarded-For, because a header the client
    controls is a header the client can use to get a fresh bucket. Behind a
    reverse proxy every request therefore appears to come from the proxy and
    shares one window - which is a real limitation, not a bug, and is the
    reason the proxy should do this instead.
  - Anything after the request is admitted. A single permitted ingest of a
    large file still costs what it costs; MAX_INGEST_ROWS is what bounds that.

Counters are in memory, so they reset when the process restarts and are not
shared between workers. For a single-process local tool that is the honest
scope; a multi-worker deployment needs a shared store, and SECURITY.md says so
rather than this pretending otherwise.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security.config import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    rate_limit_enabled,
)

#: Path prefixes the limiter applies to: the ones that start real work.
#: Reads (GET /runs, GET /audit, the status polls the dashboard makes every
#: second) are deliberately NOT limited - throttling the poll that shows
#: progress would break the UI while protecting nothing expensive.
LIMITED_PREFIXES = ("/ingest", "/sources/upload", "/ask", "/predict", "/export", "/analytics")


def is_local_address(host: str | None) -> bool:
    """True for loopback callers, who are exempt.

    Compared as a parsed address rather than a string, so "127.0.0.1",
    "::1" and "::ffff:127.0.0.1" are all recognised - a string comparison
    against "127.0.0.1" silently fails to exempt the IPv6 forms and would
    throttle the local operator on exactly the setups that use them.
    """
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    # A v4-mapped v6 address (::ffff:127.0.0.1) is loopback in v4 terms.
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


class FixedWindowLimiter:
    """Counts requests per client per window. Thread-safe: uvicorn runs
    endpoints in a threadpool, so two requests can hit this at once."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, seconds until the window frees up)."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = [t for t in self._hits[key] if t > cutoff]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                retry_after = max(1, int(hits[0] + self.window_seconds - now) + 1)
                return False, retry_after
            hits.append(now)
            self._hits[key] = hits
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter = FixedWindowLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def reset_limiter() -> None:
    """Test-only: clears every window so one test's requests cannot exhaust
    the next test's budget."""
    _limiter.reset()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not rate_limit_enabled():
            return await call_next(request)
        if not request.url.path.startswith(LIMITED_PREFIXES):
            return await call_next(request)

        host = request.client.host if request.client else None
        if is_local_address(host):
            return await call_next(request)

        allowed, retry_after = _limiter.check(host or "unknown")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"rate limit exceeded: more than {RATE_LIMIT_REQUESTS} requests to "
                        f"{request.url.path} and similar endpoints in {RATE_LIMIT_WINDOW_SECONDS}s. "
                        f"Retry in {retry_after}s."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
