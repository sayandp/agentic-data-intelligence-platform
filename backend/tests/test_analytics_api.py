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


# ---- Confirmed column roles ----


def _vague_csv(path) -> str:
    """A table whose monetary column is real but unnameable by the detector.

    `widgets_moved` holds small whole numbers and matches no monetary name
    hint, so it scores as a quantity and lands below the usable floor for
    MONETARY - exactly the low-confidence case a human is meant to resolve.
    """
    frame = pd.DataFrame(
        {
            "cust": [f"c{i % 12}" for i in range(60)],
            "when": pd.date_range("2024-01-01", periods=60, freq="D").strftime("%Y-%m-%d"),
            "widgets_moved": [float(3 + (i % 7)) for i in range(60)],
        }
    )
    csv_path = path / "vague.csv"
    frame.to_csv(csv_path, index=False)
    return str(csv_path)


def _vague_source(client, tmp_path) -> str:
    return client.post(
        "/sources", json={"type": "file", "connection_config": {"path": _vague_csv(tmp_path)}}
    ).json()["id"]


def _applicable(body, analysis: str) -> bool:
    return [a for a in body["applicability"] if a["analysis"] == analysis][0]["applicable"]


def test_confirming_a_role_makes_a_not_applicable_analysis_run(client, tmp_path):
    """The whole point of surfacing a low-confidence candidate: a person can
    act on it, and the analyses it gates then run."""
    source_id = _vague_source(client, tmp_path)
    result = ingest_and_wait(client, source_id)

    before = client.get(f"/analytics/{result['run_number']}").json()
    assert not _applicable(before, "rfm"), "RFM must be ineligible while no monetary column is detected"
    assert "monetary" not in before["detected_roles"]["assigned"]

    confirmed = client.post(
        f"/analytics/{result['run_number']}/confirmed-roles",
        json={"role": "monetary", "column": "widgets_moved"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()

    assert _applicable(body, "rfm"), "confirming the monetary role must unlock the analyses that need it"
    assert body["detected_roles"]["assigned"]["monetary"]["column"] == "widgets_moved"
    # CONFIRMED, not HIGH: a reader can always tell a person's decision from
    # the detector's inference.
    assert body["detected_roles"]["assigned"]["monetary"]["confidence"] == "confirmed"
    assert [r for r in body["results"] if r["analysis"] == "rfm"][0]["ran"]

    # ...and the recompute is what a plain GET now returns, not a one-off.
    assert _applicable(client.get(f"/analytics/{result['run_number']}").json(), "rfm")


def test_a_confirmation_persists_across_a_second_ingest_of_the_same_source(client, tmp_path):
    """Confirmations are stored per SOURCE, so re-ingesting the same file
    does not ask the same question again."""
    source_id = _vague_source(client, tmp_path)
    first = ingest_and_wait(client, source_id)
    client.post(
        f"/analytics/{first['run_number']}/confirmed-roles",
        json={"role": "monetary", "column": "widgets_moved"},
    ).raise_for_status()

    second = ingest_and_wait(client, source_id)
    body = client.get(f"/analytics/{second['run_number']}").json()

    assert body["run_id"] != first["run_id"], "this must be a genuinely new run"
    assert body["detected_roles"]["assigned"]["monetary"]["column"] == "widgets_moved"
    assert body["detected_roles"]["assigned"]["monetary"]["confidence"] == "confirmed"
    assert _applicable(body, "rfm"), "the second ingest inherits the confirmation with no user action"


def test_a_confirmation_naming_a_column_the_run_lacks_is_rejected(client, tmp_path):
    source_id = _vague_source(client, tmp_path)
    result = ingest_and_wait(client, source_id)

    response = client.post(
        f"/analytics/{result['run_number']}/confirmed-roles",
        json={"role": "monetary", "column": "no_such_column"},
    )

    assert response.status_code == 400
    # The error names what IS available, so the next attempt can succeed.
    assert "widgets_moved" in response.json()["detail"]


def test_an_unknown_role_is_rejected_with_the_closed_set(client, tmp_path):
    source_id = _vague_source(client, tmp_path)
    result = ingest_and_wait(client, source_id)

    response = client.post(
        f"/analytics/{result['run_number']}/confirmed-roles",
        json={"role": "vibes", "column": "widgets_moved"},
    )

    assert response.status_code == 400
    assert "monetary" in response.json()["detail"]


def test_a_confirmation_can_be_withdrawn(client, tmp_path):
    """Confirming must not be a one-way door - a wrong answer has to be
    correctable, and detection alone decides the role again afterwards."""
    source_id = _vague_source(client, tmp_path)
    result = ingest_and_wait(client, source_id)
    client.post(
        f"/analytics/{result['run_number']}/confirmed-roles",
        json={"role": "monetary", "column": "widgets_moved"},
    ).raise_for_status()

    cleared = client.delete(f"/analytics/{result['run_number']}/confirmed-roles/monetary")

    assert cleared.status_code == 200, cleared.text
    assert not _applicable(cleared.json(), "rfm")
    assert "monetary" not in cleared.json()["detected_roles"]["assigned"]


def test_recomputing_after_a_confirmation_records_a_new_trace(client, tmp_path):
    """Overwriting the results without a trace would leave Audit showing a
    set of numbers that no longer exists."""
    source_id = _vague_source(client, tmp_path)
    result = ingest_and_wait(client, source_id)
    client.post(
        f"/analytics/{result['run_number']}/confirmed-roles",
        json={"role": "monetary", "column": "widgets_moved"},
    ).raise_for_status()

    with SessionLocal() as db:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.run_id == result["run_id"], AgentTrace.agent_name == "business_analytics")
            .all()
        )
        assert len(traces) == 2, "the recompute must be traced, not silent"
        assert any("confirmed_roles" in t.input_summary for t in traces)
        assert db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == result["run_id"]).count() == 1
