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
    from app.models import ValidationEvent

    with session_local() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).all()
        return sorted((e.rule_failed.split(":")[0], e.column_name, e.risk_level, e.action_taken) for e in events)


def test_three_connectors_produce_equivalent_validation_events(client, tmp_path, monkeypatch):
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
            return df.to_json(orient="records")

        with _local_json_server(records_provider) as url:
            api_source_id = client.post(
                "/sources", json={"type": "api", "connection_config": {"url": url}}
            ).json()["id"]
            ingest_and_wait(client, api_source_id)
            state["mode"] = "corrupted"
            api_result = ingest_and_wait(client, api_source_id)

    # -- all three runs actually processed the rename and reached the same outcome --
    for result in (csv_result, sql_result, api_result):
        assert result["status"] == "completed"

    csv_summary = _event_summary(SessionLocal, csv_result["run_id"])
    sql_summary = _event_summary(SessionLocal, sql_result["run_id"])
    api_summary = _event_summary(SessionLocal, api_result["run_id"])

    assert csv_summary == sql_summary == api_summary
    assert csv_summary == [
        ("schema_conformance", "city", "low", "auto_fixed"),
        ("schema_conformance", "town", "low", "auto_fixed"),
    ]

    # -- only source_type and connector_metadata are allowed to differ --
    assert csv_result["metadata"]["source_type"] == "file"
    assert sql_result["metadata"]["source_type"] == "sql"
    assert api_result["metadata"]["source_type"] == "api"
    assert "declared_schema" in sql_result["metadata"]  # SQL-only: authoritative dtypes
    assert "pages_fetched" in api_result["metadata"]  # API-only: pagination bookkeeping

    # -- the repaired column name is identical across all three post-fix --
    for result in (csv_result, sql_result, api_result):
        assert "city" in result["metadata"]["column_types"]
        assert "town" not in result["metadata"]["column_types"]
