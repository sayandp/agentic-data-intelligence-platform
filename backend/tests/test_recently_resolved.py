"""Dashboard UX pass:

Part 1 (SEQUENTIAL RUN NUMBERS) - app/routers/ingest.py::next_run_number
assigns a short, unique, sequential integer to every new Run, and
app/db.py::init_db backfills any pre-existing row (a table that predates the
column) in creation order.

Part 2 (RECENTLY RESOLVED) - GET /approvals/pending's `recently_resolved`
and `summary` fields, built entirely from data that already exists
(resolved_by/resolved_at, or the equivalent state/is_active fields) - a read,
never new backend state (beyond ValidationEvent.resolved_at itself, added
because the other three approval-bearing models already had the equivalent
column and ValidationEvent's absence of it was the one gap standing in the
way of an honest "when").
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.db import SessionLocal
from app.models import DataSource, Run, ValidationEvent
from tests.golden_scenarios import ingest_and_wait, resolve_and_wait


# ---------------------------------------------------------------------------
# Part 1: sequential run numbers
# ---------------------------------------------------------------------------


def _clean_csv_text(n: int = 20) -> str:
    lines = ["id,city,amount"]
    for i in range(n):
        lines.append(f"{i},NYC,{10.0 + i}")
    return "\n".join(lines) + "\n"


def test_run_number_assigned_and_sequential_across_ingests(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]

    first = ingest_and_wait(client, source_id)
    csv_path.write_text(_clean_csv_text(21), encoding="utf-8")
    second = ingest_and_wait(client, source_id)

    assert isinstance(first["run_number"], int)
    assert isinstance(second["run_number"], int)
    assert second["run_number"] == first["run_number"] + 1


def test_run_number_is_unique_across_sources(client, tmp_path):
    csv_a = tmp_path / "a.csv"
    csv_a.write_text(_clean_csv_text(), encoding="utf-8")
    csv_b = tmp_path / "b.csv"
    csv_b.write_text(_clean_csv_text(), encoding="utf-8")

    source_a = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_a)}}).json()["id"]
    source_b = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_b)}}).json()["id"]

    run_a = ingest_and_wait(client, source_a)
    run_b = ingest_and_wait(client, source_b)

    assert run_a["run_number"] != run_b["run_number"]


def test_pre_existing_runs_are_backfilled_a_run_number_in_creation_order():
    """Simulates the exact situation app/db.py::init_db's migration exists
    for: rows that were already in the table before run_number existed. It
    is enough to insert Run rows with run_number left NULL (as they would
    have been before the column was backfilled) and re-run init_db - the
    same function the app calls at every startup, idempotently."""
    from datetime import datetime, timedelta, timezone

    from app.db import init_db

    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused.csv"})
        db.add(source)
        db.commit()
        db.refresh(source)

        base = datetime.now(timezone.utc)
        older = Run(id="backfill-older", source_id=source.id, status="completed", started_at=base - timedelta(minutes=5))
        newer = Run(id="backfill-newer", source_id=source.id, status="completed", started_at=base)
        db.add_all([older, newer])
        db.commit()

        # Simulate a pre-migration row: run_number wasn't set at INSERT time.
        assert older.run_number is None
        assert newer.run_number is None

    init_db()  # the same call app/main.py's lifespan makes at every startup

    with SessionLocal() as db:
        older_after = db.get(Run, "backfill-older")
        newer_after = db.get(Run, "backfill-newer")
        assert older_after.run_number is not None
        assert newer_after.run_number is not None
        assert older_after.run_number < newer_after.run_number  # creation order preserved


# ---------------------------------------------------------------------------
# Part 2: recently resolved + summary
# ---------------------------------------------------------------------------


def _corrupt_csv_escalating_null_threshold(n: int = 60) -> str:
    # SAME columns as _clean_csv_text (id, city, amount) - only "amount"
    # gets corrupted (>5% null rate). Using different column names entirely
    # would also trip missing_column/unexpected_column failures alongside
    # null_threshold, which is not the single, stable escalation this test
    # needs.
    lines = ["id,city,amount"]
    for i in range(n):
        amount = "" if i % 3 == 0 else f"{10.0 + i}"
        lines.append(f"{i},NYC,{amount}")
    return "\n".join(lines) + "\n"


def _escalate_validation_event(client, tmp_path):
    clean = tmp_path / "orders.csv"
    clean.write_text(_clean_csv_text(60), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(clean)}}).json()["id"]
    first = ingest_and_wait(client, source_id)
    client.post(f"/approvals/{first['baseline']['id']}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    clean.write_text(_corrupt_csv_escalating_null_threshold(60), encoding="utf-8")
    second = ingest_and_wait(client, source_id)
    assert second["status"] == "awaiting_approval"
    resolve_id = client.get("/approvals/pending").json()["validation_events"][0]["resolve_id"]
    return second, resolve_id


def test_resolved_validation_event_appears_in_recently_resolved(client, tmp_path):
    second, resolve_id = _escalate_validation_event(client, tmp_path)

    resolve_and_wait(client, resolve_id, "reject_fix", "alice")

    pending = client.get("/approvals/pending").json()
    resolved = pending["recently_resolved"]["validation_events"]
    assert len(resolved) == 1
    entry = resolved[0]
    assert entry["run_id"] == second["run_id"]
    assert entry["run_number"] == second["run_number"]
    assert entry["decision"] == "rejected_fix_data_acceptable"
    assert entry["resolved_by"] == "alice"
    assert entry["resolved_at"] is not None
    # It must also have LEFT the pending list, not just been added to history.
    assert pending["validation_events"] == []


def test_resolved_provisional_baseline_appears_in_recently_resolved(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    result = ingest_and_wait(client, source_id)
    baseline_id = result["baseline"]["id"]

    client.post(f"/approvals/{baseline_id}/resolve", json={"decision": "approve", "resolved_by": "bob"})

    pending = client.get("/approvals/pending").json()
    resolved = pending["recently_resolved"]["provisional_baselines"]
    assert len(resolved) == 1
    assert resolved[0]["id"] == baseline_id
    assert resolved[0]["decision"] == "approve"
    assert resolved[0]["resolved_by"] == "bob"
    assert resolved[0]["resolved_at"] is not None
    assert pending["provisional_baselines"] == []


def test_acknowledged_connector_warning_appears_in_recently_resolved(client, tmp_path):
    pytest.importorskip("openpyxl")
    xlsx_path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame({"x": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [3, 4]}).to_excel(writer, sheet_name="second", index=False)
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(xlsx_path)}}).json()["id"]
    body = ingest_and_wait(client, source_id)
    warning_id = client.get("/approvals/pending").json()["connector_warnings"][0]["id"]

    client.post(f"/approvals/{warning_id}/resolve", json={"decision": "acknowledge", "resolved_by": "carol"})

    pending = client.get("/approvals/pending").json()
    resolved = pending["recently_resolved"]["connector_warnings"]
    assert len(resolved) == 1
    assert resolved[0]["run_id"] == body["run_id"]
    assert resolved[0]["run_number"] == body["run_number"]
    assert resolved[0]["decision"] == "acknowledged"
    assert resolved[0]["resolved_by"] == "carol"
    assert resolved[0]["resolved_at"] is not None
    assert pending["connector_warnings"] == []


def test_summary_counts_resolved_items_and_distinct_runs(client, tmp_path):
    assert client.get("/approvals/pending").json()["summary"] == {"total_resolved": 0, "distinct_runs": 0}

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    result = ingest_and_wait(client, source_id)
    client.post(f"/approvals/{result['baseline']['id']}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    summary = client.get("/approvals/pending").json()["summary"]
    assert summary["total_resolved"] == 1
    # A baseline resolution has no run_id (see the Baseline model) - it
    # contributes to total_resolved but never to distinct_runs.
    assert summary["distinct_runs"] == 0

    second, resolve_id = _escalate_validation_event(client, tmp_path)
    resolve_and_wait(client, resolve_id, "reject_fix", "alice")

    summary = client.get("/approvals/pending").json()["summary"]
    assert summary["total_resolved"] == 3  # 1 baseline (above) + 1 baseline confirm (in _escalate_validation_event) + 1 validation event
    assert summary["distinct_runs"] == 1  # only the escalated run has a resolved item with a run_id


def test_recently_resolved_never_includes_an_auto_fixed_event(client):
    """An auto-fix never went through this UI at all - it must never be
    mistaken for a human resolution just because its terminal state can
    also be RESOLVED (see app/state_machine.py: AUTO_FIXED -> RESOLVED is a
    valid edge that has nothing to do with a human decision). Filtering on
    resolved_by IS NOT NULL (see app/routers/approvals.py::list_pending) is
    what keeps this out - the auto-fix path never sets it."""
    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "unused.csv"})
        db.add(source)
        db.commit()
        db.refresh(source)
        run = Run(source_id=source.id, status="completed", run_number=999999)
        db.add(run)
        db.commit()
        db.refresh(run)

        db.add(
            ValidationEvent(
                run_id=run.id, rule_failed="missing_column:city", state="resolved", action_taken="auto_fixed", resolved_by=None
            )
        )
        db.commit()

    resolved = client.get("/approvals/pending").json()["recently_resolved"]["validation_events"]
    assert resolved == []
