"""THE deliverable for Phase 3: the same seeded corruption, loaded through
three different connectors (CSV file, SQLite table via SQLConnector, and a
real local HTTP endpoint via APIConnector), must produce equivalent
validation_events - same rule families, same columns, same risk routing.
Only source_type and connector_metadata are allowed to differ.

This is what turns "every downstream agent is source-agnostic because
connectors emit a common data contract" from a claim in the abstract into a
demonstrated property. If this test needed ANY change to app/validation/,
app/correlation.py, app/diagnosis/, app/gate.py, app/actions.py,
app/repair.py, or app/profiling.py to pass, that would mean the contract
leaks source-specific assumptions - it needed none.
"""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.main import app
from app.routers.ingest import get_diagnostic_agent
from tests.corruption import CorruptionSuite
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache
from tests.golden_scenarios import ingest_and_wait

RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="column relabeled upstream; data intact under the new name",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.95,
)


@contextmanager
def _rename_aware_diagnosis():
    """Overrides the default (always-escalate) fake with one that recognizes
    a rename pair, so the equivalence run demonstrates the full auto-fix path
    identically across all three sources - not just "all three escalate"."""

    def _override():
        return DiagnosticAgent(
            llm_client=FakeLLMClient(default=RENAME_DIAGNOSIS), cache=InMemoryDiagnosisCache(), sleep=lambda _s: None
        )

    app.dependency_overrides[get_diagnostic_agent] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def _clean_orders(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago"]
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "city": rng.choice(cities, size=n),
            "amount": rng.normal(100.0, 20.0, size=n).round(2).clip(min=1.0),
            # A datetime column, deliberately. Each connector reaches
            # datetime64 by a DIFFERENT mechanism - the file and API paths by
            # parse-rate inference over text, SQL from the column's DECLARED
            # type - so agreement here is a real convergence rather than three
            # connectors passing the same bytes through.
            #
            # Added after a falsification: with no temporal column, sabotaging
            # SQL's declared-schema coercion changed nothing and the test
            # still passed. It was blind to a whole branch of the contract it
            # claims to cover.
            "ordered_at": pd.date_range("2026-01-01", periods=n, freq="h"),
        }
    )


class _JSONHandler(http.server.BaseHTTPRequestHandler):
    """Serves whatever `records_provider()` currently returns as a JSON
    array - a REAL local HTTP endpoint (loopback), not a mocked transport,
    so the APIConnector goes through its normal network path unmodified."""

    records_provider = None

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        body = self.__class__.records_provider().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr access logging
        pass


@contextmanager
def _local_json_server(records_provider):
    _JSONHandler.records_provider = staticmethod(records_provider)
    server = http.server.HTTPServer(("127.0.0.1", 0), _JSONHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/items"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _event_summary(session_local, run_id) -> list[tuple]:
    """The validation outcome as a SORTED STRUCTURE, never a formatted string.

    Rule FAMILY (before the first colon) rather than the full rule id, because
    the id carries the column name a second time and would make the comparison
    trivially true for the wrong reason.
    """
    from app.models import ValidationEvent

    with session_local() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).all()
        return sorted((e.rule_failed.split(":")[0], e.column_name, e.risk_level, e.action_taken) for e in events)


def _assert_all_equal(summaries: dict[str, list[tuple]]) -> None:
    """Equality across the three connectors, failing with the NAME of the one
    that diverged and the exact difference.

    A bare `a == b == c` reports "assert [...] == [...]" and leaves the reader
    to work out which connector was wrong; when this test fails it is because
    a connector's contract output changed, and which one is the whole answer.
    """
    reference_name, reference = next(iter(summaries.items()))
    for name, summary in summaries.items():
        if summary == reference:
            continue
        only_here = sorted(set(summary) - set(reference))
        only_there = sorted(set(reference) - set(summary))
        raise AssertionError(
            f"connector '{name}' diverged from '{reference_name}'.\n"
            f"  {name} produced but {reference_name} did not: {only_here}\n"
            f"  {reference_name} produced but {name} did not: {only_there}\n"
            f"  full {name}: {summary}\n"
            f"  full {reference_name}: {reference}"
        )


def _run_equivalence(client, tmp_path, monkeypatch) -> dict[str, list[tuple]]:
    from app.db import SessionLocal

    clean_df = _clean_orders()
    corrupted_df, truth = CorruptionSuite().apply(clean_df, "rename_column", seed=42, column="city", new_name="town")
    assert truth.expected_risk_level == "low"

    with _rename_aware_diagnosis():
        # -- CSV --
        csv_path = tmp_path / "orders.csv"
        clean_df.to_csv(csv_path, index=False)
        csv_source_id = client.post(
            "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
        ).json()["id"]
        ingest_and_wait(client, csv_source_id)
        corrupted_df.to_csv(csv_path, index=False)
        csv_result = ingest_and_wait(client, csv_source_id)

        # -- SQL (SQLite standing in for Postgres/MySQL/MSSQL - see
        # app/connectors/sql_connector.py's module docstring) --
        sql_env_var = "EQUIV_TEST_SQL_URL"
        monkeypatch.setenv(sql_env_var, f"sqlite:///{(tmp_path / 'orders.db').as_posix()}")
        engine = create_engine(f"sqlite:///{(tmp_path / 'orders.db').as_posix()}")
        clean_df.to_sql("orders", engine, if_exists="replace", index=False)
        sql_source_id = client.post(
            "/sources",
            json={
                "type": "sql",
                "connection_config": {"connection_string_env": sql_env_var, "query": "select * from orders"},
            },
        ).json()["id"]
        ingest_and_wait(client, sql_source_id)
        corrupted_df.to_sql("orders", engine, if_exists="replace", index=False)
        sql_result = ingest_and_wait(client, sql_source_id)
        engine.dispose()

        # -- API (real local HTTP endpoint, not a mocked transport) --
        state = {"mode": "clean"}

        def records_provider(state=state, clean_df=clean_df, corrupted_df=corrupted_df):
            df = clean_df if state["mode"] == "clean" else corrupted_df
            # date_format="iso" because pandas defaults datetimes to epoch
            # milliseconds, which no real JSON API would serve and which
            # would make this diverge for a fixture reason rather than a
            # contract one.
            return df.to_json(orient="records", date_format="iso")

        with _local_json_server(records_provider) as url:
            api_source_id = client.post(
                "/sources", json={"type": "api", "connection_config": {"url": url}}
            ).json()["id"]
            ingest_and_wait(client, api_source_id)
            state["mode"] = "corrupted"
            api_result = ingest_and_wait(client, api_source_id)

    # -- all three runs actually processed the rename and reached the same outcome --
    # Terminal state, naming the connector that diverged. A bare
    # `assert status == "completed"` reports the values and leaves the reader
    # to work out WHICH source produced the odd one, which is the whole
    # answer when this test fails.
    states = {"file": csv_result["status"], "sql": sql_result["status"], "api": api_result["status"]}
    diverged = {name: state for name, state in states.items() if state != "completed"}
    assert not diverged, (
        "connector(s) " + str(sorted(diverged)) + " did not reach the same terminal state as the others. "
        "all three: " + str(states) + " - every connector must drive the same run outcome "
        "for the same data and the same corruption."
    )

    csv_summary = _event_summary(SessionLocal, csv_result["run_id"])
    sql_summary = _event_summary(SessionLocal, sql_result["run_id"])
    api_summary = _event_summary(SessionLocal, api_result["run_id"])

    _assert_all_equal({"file": csv_summary, "sql": sql_summary, "api": api_summary})
    assert csv_summary == [
        ("schema_conformance", "city", "low", "auto_fixed"),
        ("schema_conformance", "town", "low", "auto_fixed"),
    ]

    # -- the DIFFERENCES, asserted explicitly --
    #
    # A test that only checked sameness would pass if all three connectors
    # silently degraded to the same wrong result - three empty contracts are
    # trivially equivalent. Each connector must still be demonstrably ITSELF,
    # carrying the metadata only it can produce.
    assert csv_result["metadata"]["source_type"] == "file"
    assert sql_result["metadata"]["source_type"] == "sql"
    assert api_result["metadata"]["source_type"] == "api"

    # File-only: encoding is detected, never declared.
    assert csv_result["metadata"]["detected_encoding"], "the file connector reported no detected encoding"
    assert csv_result["metadata"]["encoding_confidence"] is not None
    # SQL-only: the database declares its dtypes authoritatively.
    assert "declared_schema" in sql_result["metadata"]
    assert sql_result["metadata"]["declared_schema"], "the SQL connector reported an empty declared schema"
    # API-only: pagination bookkeeping.
    assert "pages_fetched" in api_result["metadata"]
    assert api_result["metadata"]["pages_fetched"] >= 1

    # And each connector's own marker must be ABSENT from the other two -
    # otherwise "connector-specific" would be a label rather than a fact.
    assert "declared_schema" not in csv_result["metadata"]
    assert "declared_schema" not in api_result["metadata"]
    assert "pages_fetched" not in csv_result["metadata"]
    assert "pages_fetched" not in sql_result["metadata"]
    assert not sql_result["metadata"].get("detected_encoding")
    assert not api_result["metadata"].get("detected_encoding")

    # -- the repaired column name is identical across all three post-fix --
    for result in (csv_result, sql_result, api_result):
        assert "city" in result["metadata"]["column_types"]
        assert "town" not in result["metadata"]["column_types"]

    # -- the same dtypes, reached by three different mechanisms --
    #
    # The file and API connectors infer a datetime from text by parse rate;
    # the SQL connector coerces from the column's DECLARED type. That they
    # agree is the substance of source-agnosticism: a downstream agent sees
    # one contract regardless of which of those three ran.
    dtypes = {
        "file": csv_result["metadata"]["column_types"],
        "sql": sql_result["metadata"]["column_types"],
        "api": api_result["metadata"]["column_types"],
    }
    for name, observed in dtypes.items():
        assert "ordered_at" in observed, f"connector '{name}' lost the datetime column entirely: {observed}"
        assert observed["ordered_at"].startswith("datetime64"), (
            f"connector '{name}' did not resolve `ordered_at` to a datetime - got "
            f"{observed['ordered_at']!r}. All three must converge on the same contract."
        )
    assert dtypes["file"] == dtypes["sql"] == dtypes["api"], (
        f"the three connectors disagree about column types.\n"
        + "\n".join(f"  {n}: {d}" for n, d in dtypes.items())
    )

    return {"file": csv_summary, "sql": sql_summary, "api": api_summary}


def test_three_connectors_produce_equivalent_validation_events(client, tmp_path, monkeypatch):
    """The same dataset and the same seeded corruption, through three real
    connectors, produce the same validation outcome."""
    _run_equivalence(client, tmp_path, monkeypatch)


#: Repeats of the whole three-connector run. Ingest order, dict iteration and
#: the correlation pass are all deterministic by design, but a single green
#: run cannot tell a guarantee from a coincidence of ordering.
EQUIVALENCE_REPEATS = 5


def test_the_equivalence_is_stable_across_repeated_runs(client, tmp_path, monkeypatch):
    observed: list[dict[str, list[tuple]]] = []
    for attempt in range(EQUIVALENCE_REPEATS):
        # A fresh directory per attempt: reusing one would let a leftover
        # SQLite file or CSV make later attempts pass for the wrong reason.
        attempt_dir = tmp_path / f"attempt-{attempt}"
        attempt_dir.mkdir()
        observed.append(_run_equivalence(client, attempt_dir, monkeypatch))

    first = observed[0]
    for attempt, summaries in enumerate(observed[1:], start=1):
        assert summaries == first, (
            f"attempt {attempt} produced a different outcome than attempt 0 - "
            f"the equivalence is not stable.\n  attempt 0: {first}\n  attempt {attempt}: {summaries}"
        )
