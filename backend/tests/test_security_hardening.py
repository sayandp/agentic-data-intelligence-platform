"""Part 3 hardening: size limits, content/extension agreement, rate limiting.

Each of these is a REFUSAL, and the tests are written around that word. A cap
that truncates, a sniffer that guesses, or a limiter that quietly lets a
request through when its bookkeeping fails would all convert a protection into
confidently wrong output - which is the failure this codebase keeps finding.
"""

import io

import pandas as pd
import pytest

from app.contract import DataContract, FrameTooLargeError, SourceType
from app.security import config as security_config
from app.security.file_type import detect_mismatch
from app.security.rate_limit import FixedWindowLimiter, is_local_address, reset_limiter
from tests.golden_scenarios import ingest_and_wait


# ---- size limits ----


def test_an_oversized_frame_cannot_be_constructed_at_all(monkeypatch):
    """Structure over check: the limit lives on the one type every connector
    produces and every agent consumes, so there is no path that skips it."""
    monkeypatch.setattr(security_config, "MAX_INGEST_ROWS", 10)
    monkeypatch.setattr("app.contract.MAX_INGEST_ROWS", 10)

    with pytest.raises(FrameTooLargeError) as exc:
        DataContract(pd.DataFrame({"a": range(11)}), SourceType.FILE, "s")

    assert "11 rows" in str(exc.value)
    assert "MAX_INGEST_ROWS" in str(exc.value)


def test_the_row_limit_refuses_rather_than_truncating(monkeypatch):
    """The distinction the whole limit turns on. Analysing the first N rows
    and reporting that as the dataset's profile would be a baseline, a null
    rate and a Pareto band computed over part of the data with nothing saying
    so."""
    monkeypatch.setattr("app.contract.MAX_INGEST_ROWS", 10)

    with pytest.raises(FrameTooLargeError):
        DataContract(pd.DataFrame({"a": range(500)}), SourceType.FILE, "s")


def test_a_frame_at_exactly_the_limit_is_accepted(monkeypatch):
    """Off-by-one on a refusal is a refusal of legitimate data."""
    monkeypatch.setattr("app.contract.MAX_INGEST_ROWS", 10)

    contract = DataContract(pd.DataFrame({"a": range(10)}), SourceType.FILE, "s")

    assert contract.row_count == 10


def test_an_absurdly_wide_frame_is_refused_and_told_why(monkeypatch):
    """Thousands of columns is almost always a mis-detected delimiter, and
    saying so is more useful than reporting a column count."""
    monkeypatch.setattr("app.contract.MAX_INGEST_COLUMNS", 5)

    with pytest.raises(FrameTooLargeError) as exc:
        DataContract(pd.DataFrame({f"c{i}": [1] for i in range(6)}), SourceType.FILE, "s")

    assert "parse gone wrong" in str(exc.value)


def test_an_oversized_source_fails_the_run_with_the_reason_recorded(client, tmp_path, monkeypatch):
    """End to end: the refusal must reach the person as a failed run naming
    the limit, not as a stack trace that reads like a parsing bug."""
    monkeypatch.setattr("app.contract.MAX_INGEST_ROWS", 5)

    csv = tmp_path / "too_big.csv"
    csv.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(50)) + "\n")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv)}}
    ).json()["id"]

    result = ingest_and_wait(client, source_id)

    assert result["status"] == "failed"
    audit = client.get(f"/audit/{result['run_id']}").json()
    errors = [t for t in audit["trace"] if t.get("edge_taken") == "failed"]
    assert errors, "a failed run must record why it failed"
    assert "ingest limit" in str(errors[0]["output"])


# ---- content / extension agreement ----


def test_an_xlsx_renamed_to_csv_is_refused():
    """The case that actually happens. Without this it reaches pandas as a
    text parse of a ZIP archive."""
    mismatch = detect_mismatch(b"PK\x03\x04\x14\x00\x00\x00", ".csv")

    assert mismatch is not None
    assert "ZIP" in mismatch.message()
    assert ".csv" in mismatch.message()


def test_a_csv_renamed_to_xlsx_is_refused():
    mismatch = detect_mismatch(b"id,name\n1,alice\n", ".xlsx")

    assert mismatch is not None
    assert "text file" in mismatch.message()


def test_a_legacy_xls_renamed_to_xlsx_is_refused_and_named_correctly():
    """Both are Excel, and telling the person WHICH Excel is the difference
    between an actionable message and 'invalid file'."""
    mismatch = detect_mismatch(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", ".xlsx")

    assert mismatch is not None
    assert "legacy" in mismatch.message()


def test_a_real_csv_and_a_real_xlsx_pass():
    assert detect_mismatch(b"id,name\n1,alice\n", ".csv") is None
    assert detect_mismatch(b"PK\x03\x04\x14\x00", ".xlsx") is None
    assert detect_mismatch(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xls") is None


def test_a_utf16_csv_is_not_mistaken_for_a_binary():
    """A regression guard on the sniffer's own heuristic. UTF-16 text is full
    of NUL bytes, and the connector reads it correctly - refusing it here
    would break files that work today."""
    utf16 = "id,name\n1,alice\n".encode("utf-16")

    assert detect_mismatch(utf16, ".csv") is None


def test_binary_junk_claiming_to_be_csv_is_refused():
    assert detect_mismatch(b"\x89PNG\r\n\x1a\n\x00\x00\x00", ".csv") is not None


def test_upload_refuses_a_mismatched_file_and_stores_nothing(client, tmp_path, monkeypatch):
    """The refusal must also clean up: a rejected upload that leaves its bytes
    on disk has stored data the system then claims it never accepted."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    response = client.post(
        "/sources/upload",
        files={"file": ("sales.csv", io.BytesIO(b"PK\x03\x04\x14\x00\x00\x00rest of a zip"), "text/csv")},
    )

    assert response.status_code == 400
    assert "does not match its .csv extension" in response.json()["detail"]
    assert list(upload_dir.glob("*")) == [], "a refused upload must not leave its bytes on disk"


def test_upload_accepts_a_real_csv(client, tmp_path, monkeypatch):
    """The other half: the guard must not refuse ordinary files."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))

    response = client.post(
        "/sources/upload",
        files={"file": ("sales.csv", io.BytesIO(b"id,amount\n1,10\n2,20\n"), "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["id"]


# ---- rate limiting ----


def test_loopback_callers_are_exempt_in_every_form():
    """A string comparison against "127.0.0.1" silently fails to exempt the
    IPv6 forms, throttling the local operator on exactly the setups that use
    them."""
    for host in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "127.0.0.5"):
        assert is_local_address(host), f"{host} should be exempt"

    for host in ("203.0.113.7", "10.0.0.4", "", None, "not-an-address"):
        assert not is_local_address(host), f"{host} should not be exempt"


def test_the_limiter_allows_up_to_the_limit_then_refuses():
    limiter = FixedWindowLimiter(limit=3, window_seconds=60)

    assert [limiter.check("client", now=100.0)[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("client", now=100.0)

    assert allowed is False
    assert retry_after > 0, "a refusal must say when to come back"


def test_the_window_frees_up():
    limiter = FixedWindowLimiter(limit=2, window_seconds=60)
    limiter.check("client", now=100.0)
    limiter.check("client", now=100.0)

    assert limiter.check("client", now=100.0)[0] is False
    assert limiter.check("client", now=161.0)[0] is True


def test_clients_have_separate_budgets():
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    limiter.check("a", now=100.0)

    assert limiter.check("a", now=100.0)[0] is False
    assert limiter.check("b", now=100.0)[0] is True


def test_the_limiter_is_off_by_default(client, tmp_path, monkeypatch):
    """A local-first tool whose limiter throttled its own operator would be
    turned off everywhere, including where it is needed."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    reset_limiter()

    for _ in range(5):
        response = client.post(
            "/sources/upload",
            files={"file": ("sales.csv", io.BytesIO(b"id,amount\n1,10\n"), "text/csv")},
        )
        assert response.status_code == 200


def test_reads_are_never_limited(client, monkeypatch):
    """Throttling the status poll the dashboard makes every second would break
    the UI while protecting nothing expensive."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    reset_limiter()

    for _ in range(100):
        assert client.get("/sources").status_code == 200


def test_a_bad_limit_override_is_an_error_not_a_silent_default(monkeypatch):
    """A limit that quietly reverts to its default is worse than no limit,
    because the operator believes it is in force."""
    monkeypatch.setenv("MAX_INGEST_ROWS", "not-a-number")

    with pytest.raises(ValueError, match="must be an integer"):
        security_config._int_env("MAX_INGEST_ROWS", 100)

    monkeypatch.setenv("MAX_INGEST_ROWS", "-5")
    with pytest.raises(ValueError, match="must be positive"):
        security_config._int_env("MAX_INGEST_ROWS", 100)


def test_an_enabled_limiter_actually_returns_429_to_a_remote_caller(client, monkeypatch):
    """The central claim, asserted through the real middleware rather than
    against the limiter object.

    Every other rate-limit test here exercises FixedWindowLimiter directly,
    which proves the bookkeeping and nothing about whether the middleware is
    registered, matches the right paths, or reads the client address. Those
    are three separate ways for the limiter to be present and inert."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setattr("app.security.rate_limit._limiter", FixedWindowLimiter(limit=3, window_seconds=60))

    # TestClient presents host "testclient", which is not loopback - so it is
    # subject to the limit exactly as a remote caller would be.
    statuses = [client.post("/ingest/does-not-exist").status_code for _ in range(5)]

    assert 429 in statuses, f"the limiter never engaged (statuses: {statuses})"
    assert statuses.index(429) == 3, f"limited at the wrong request (statuses: {statuses})"

    refused = client.post("/ingest/does-not-exist")
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers, "a 429 must tell the caller when to retry"
    assert "rate limit exceeded" in refused.json()["detail"]


def test_a_loopback_caller_is_not_limited_even_when_the_limiter_is_on(monkeypatch):
    """The exemption, at the middleware rather than in is_local_address. This
    is what lets the limit stay ON in a deployment that needs it without
    throttling the operator driving the dashboard from the same machine."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security.rate_limit import FixedWindowLimiter as _Limiter

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setattr("app.security.rate_limit._limiter", _Limiter(limit=2, window_seconds=60))

    # client_host is what request.client.host reports - here, loopback.
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        statuses = [local.post("/ingest/does-not-exist").status_code for _ in range(6)]

    assert 429 not in statuses, f"a local caller was throttled (statuses: {statuses})"
