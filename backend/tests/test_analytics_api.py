"""Persistence, the graph wiring, and GET /analytics/{run_id}."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.db import SessionLocal
from app.models import AgentTrace, BusinessAnalysis
from tests.golden_scenarios import ingest_and_wait


def _business_csv(path, rows: int = 400, seed: int = 0) -> str:
    """A table with every role present, so most analyses are applicable."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "order_id": [f"T{i:05d}" for i in range(rows)],
            "customer_id": rng.integers(1, 70, rows),
            "order_date": (pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 300, rows), unit="D")).strftime(
                "%Y-%m-%d"
            ),
            "quantity": rng.integers(1, 5, rows),
            "revenue": np.round(rng.lognormal(3, 0.7, rows), 2),
        }
    )
    csv_path = path / "business.csv"
    frame.to_csv(csv_path, index=False)
    return str(csv_path)


def _ingest(client, tmp_path):
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": _business_csv(tmp_path)}}
    ).json()["id"]
    return ingest_and_wait(client, source_id)


def test_analytics_is_produced_and_persisted_by_an_ingest(client, tmp_path):
    result = _ingest(client, tmp_path)

    with SessionLocal() as db:
        record = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == result["run_id"]).one_or_none()
        assert record is not None, "an ingest of a business table must produce analytics"
        assert record.schema_version >= 1
        assert record.findings_json["results"]


def test_analytics_writes_an_agent_trace_like_every_other_agent(client, tmp_path):
    result = _ingest(client, tmp_path)

    with SessionLocal() as db:
        trace = (
            db.query(AgentTrace)
            .filter(AgentTrace.run_id == result["run_id"], AgentTrace.agent_name == "business_analytics")
            .one_or_none()
        )
        assert trace is not None, "this agent must appear on the Audit screen like the others"
        assert "analyses_run" in trace.output_summary


def test_get_analytics_accepts_a_run_number_like_every_other_run_endpoint(client, tmp_path):
    result = _ingest(client, tmp_path)

    by_number = client.get(f"/analytics/{result['run_number']}")
    by_uuid = client.get(f"/analytics/{result['run_id']}")

    assert by_number.status_code == 200, by_number.text
    assert by_uuid.status_code == 200
    assert by_number.json()["run_id"] == by_uuid.json()["run_id"] == result["run_id"]


def test_analytics_response_carries_the_not_applicable_list(client, tmp_path):
    """The refusals are the point - a user must never have to guess whether
    an analysis found nothing or was never eligible."""
    result = _ingest(client, tmp_path)
    body = client.get(f"/analytics/{result['run_number']}").json()

    assert len(body["applicability"]) == 7
    assert body["detected_roles"]["assigned"], "the roles that were filled must be visible"

    not_applicable = [a for a in body["applicability"] if not a["applicable"]]
    assert not_applicable, "a one-row-per-order table cannot support market basket"
    for entry in not_applicable:
        assert entry["missing_requirements"], "a refusal must always name its unmet requirement"

    for result_entry in body["results"]:
        assert result_entry["ran"] or result_entry["not_run_reason"]


def test_analytics_404s_for_an_unknown_run(client):
    assert client.get("/analytics/999999999").status_code == 404


def test_analytics_is_idempotent(client, tmp_path):
    """A second call must not produce a second row - same rule exploration
    and narrative already follow."""
    from app.analytics.pipeline import run_business_analytics_for_run
    from app.models import Run

    result = _ingest(client, tmp_path)
    with SessionLocal() as db:
        run = db.get(Run, result["run_id"])
        frame = pd.DataFrame({"customer_id": ["a", "b"], "revenue": [1.0, 2.0]})
        first = run_business_analytics_for_run(db, run, frame)
        db.commit()
        assert db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).count() == 1
        assert first is not None
