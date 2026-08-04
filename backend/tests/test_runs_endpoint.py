"""GET /runs - the run picker's backing list (app/routers/runs.py).

Exists so Ask/Predict can offer a human a run to CHOOSE instead of asking
them to type an identifier - the thing that let a valid run number read as
"run '48' not found". The only run listing before this was per-source
(GET /sources/{source_id}/runs), which would have meant one request per
source to populate a single dropdown.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import DataSource, Run
from app.routers.runs import source_label
from tests.golden_scenarios import ingest_and_wait


def _ingest_clean_source(client, tmp_path, name="sample.csv"):
    csv_path = tmp_path / name
    csv_path.write_text("id,city,amount\n" + "\n".join(f"{i},NYC,{i}.0" for i in range(60)) + "\n", encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    return ingest_and_wait(client, source_id)


def test_lists_a_completed_run_with_the_fields_a_picker_needs(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)

    rows = client.get("/runs").json()
    match = next(r for r in rows if r["id"] == result["run_id"])

    assert match["run_number"] == result["run_number"]
    assert match["status"] == "completed"
    assert match["completed_at"] is not None
    # The label is what makes an entry recognisable without a UUID.
    assert match["source_label"] == "sample.csv"


def test_defaults_to_completed_runs_only(client, tmp_path):
    """A running or failed run is never a valid Ask/Predict target, so
    offering one in the picker would be offering a guaranteed failure."""
    _ingest_clean_source(client, tmp_path)
    with SessionLocal() as db:
        source = DataSource(type="file", connection_config={"path": "never-finished.csv"})
        db.add(source)
        db.commit()
        db.refresh(source)
        db.add(Run(id="runs-endpoint-still-running", source_id=source.id, status="running", run_number=90001))
        db.commit()

    ids = [r["id"] for r in client.get("/runs").json()]
    assert "runs-endpoint-still-running" not in ids

    all_ids = [r["id"] for r in client.get("/runs", params={"status": "all"}).json()]
    assert "runs-endpoint-still-running" in all_ids


def test_newest_first(client, tmp_path):
    first = _ingest_clean_source(client, tmp_path, name="first.csv")
    second = _ingest_clean_source(client, tmp_path, name="second.csv")

    ids = [r["id"] for r in client.get("/runs").json()]
    assert ids.index(second["run_id"]) < ids.index(first["run_id"])


def test_limit_is_honoured_and_bounded(client, tmp_path):
    _ingest_clean_source(client, tmp_path)

    assert len(client.get("/runs", params={"limit": 1}).json()) <= 1
    # Out-of-range limits are rejected rather than silently clamped, so a
    # caller never believes it received more than it did.
    assert client.get("/runs", params={"limit": 0}).status_code == 422
    assert client.get("/runs", params={"limit": 10_000}).status_code == 422


def test_source_label_prefers_uploaded_filename_then_path_then_type():
    uploaded = DataSource(type="file", connection_config={"original_filename": "orders.csv", "path": "/tmp/abc123.csv"})
    assert source_label(uploaded) == "orders.csv"

    windows_path = DataSource(type="file", connection_config={"path": "D:\\main_project\\backend\\data\\demo_full.csv"})
    assert source_label(windows_path) == "demo_full.csv"

    posix_path = DataSource(type="file", connection_config={"path": "/data/orders.csv"})
    assert source_label(posix_path) == "orders.csv"

    sql = DataSource(type="sql", connection_config={"connection_string_env": "DB_URL", "query": "select 1"})
    assert source_label(sql) == "sql"

    assert source_label(None) == "unknown source"
