"""Dashboard UX pass, Part 1 (SEQUENTIAL RUN NUMBERS): app/id_lookup.py::
resolve_run is what lets GET /reports/{run_id} and GET /audit/{run_id}
accept a plain run_number ("17" or "#17") as well as the full UUID. UUIDs
stay the only primary key stored anywhere - run_number is a second,
independently-unique column assigned once at creation
(app/routers/ingest.py::next_run_number). A number match is always exact
(the column is UNIQUE), so unlike the earlier prefix-lookup scheme this
replaced, there is no ambiguity path to test.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import SessionLocal
from app.id_lookup import resolve_run
from app.models import DataSource, Run
from tests.golden_scenarios import ingest_and_wait


def _make_source_and_run(db, run_id: str, run_number: int | None) -> None:
    source = DataSource(type="file", connection_config={"path": "unused.csv"})
    db.add(source)
    db.commit()
    db.refresh(source)
    db.add(Run(id=run_id, source_id=source.id, status="completed", run_number=run_number))
    db.commit()


def test_resolve_run_by_full_id_is_exact():
    with SessionLocal() as db:
        _make_source_and_run(db, "full-id-exact-0001", run_number=101)
        run = resolve_run(db, "full-id-exact-0001")
        assert run.id == "full-id-exact-0001"


def test_resolve_run_by_plain_number():
    with SessionLocal() as db:
        _make_source_and_run(db, "run-for-number-lookup", run_number=202)
        run = resolve_run(db, "202")
        assert run.id == "run-for-number-lookup"


def test_resolve_run_by_hash_prefixed_number():
    with SessionLocal() as db:
        _make_source_and_run(db, "run-for-hash-number", run_number=303)
        run = resolve_run(db, "#303")
        assert run.id == "run-for-hash-number"


def test_resolve_run_unknown_number_is_404():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            resolve_run(db, "999999")
        assert exc_info.value.status_code == 404


def test_resolve_run_unknown_id_is_404():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            resolve_run(db, "does-not-exist-at-all")
        assert exc_info.value.status_code == 404


def test_resolve_run_number_lookup_is_exact_never_a_substring_match():
    """A number is a whole-value equality match against a UNIQUE column -
    "1" must never incidentally match run_number=101 or =17, the way an
    id-prefix scheme could match more than one row."""
    with SessionLocal() as db:
        _make_source_and_run(db, "run-number-one", run_number=1)
        _make_source_and_run(db, "run-number-seventeen", run_number=17)
        run = resolve_run(db, "1")
        assert run.id == "run-number-one"


# ---- the same lookup, exercised through the real endpoints ----


def _ingest_clean_source(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,city,amount\n" + "\n".join(f"{i},NYC,{i}.0" for i in range(60)) + "\n", encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    return ingest_and_wait(client, source_id)


def test_get_report_accepts_a_plain_run_number(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)
    run_id, run_number = result["run_id"], result["run_number"]
    assert isinstance(run_number, int)

    resp = client.get(f"/reports/{run_number}")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id
    assert resp.json()["run_number"] == run_number


def test_get_audit_accepts_a_plain_run_number(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)
    run_id, run_number = result["run_id"], result["run_number"]

    resp = client.get(f"/audit/{run_number}")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id
    assert resp.json()["run_number"] == run_number


def test_get_report_unknown_run_number_is_404(client):
    resp = client.get("/reports/999999999")
    assert resp.status_code == 404


# ---- every run-taking surface accepts the SAME forms ----
#
# REGRESSION: app/query/pipeline.py::resolve_run (shared by POST /ask and
# POST /predict) did a bare db.get(Run, run_id) - primary key only - while
# Reports/Audit/ingest-status went through app/id_lookup.py. A run number
# typed on the Ask/Predict screens therefore failed with "run '48' not
# found" against a run that existed and whose report rendered fine. The
# asymmetry, not the message, was the defect: these tests pin every surface
# to the same accepted forms so one can't drift from the others again.


def test_ask_accepts_a_plain_run_number(client, tmp_path):
    """A 404 here means run RESOLUTION failed. With no query LLM configured
    (the `client` fixture's default) a resolvable run still returns 200 with
    an escalated answer - which is exactly the distinction being asserted."""
    result = _ingest_clean_source(client, tmp_path)
    run_number = result["run_number"]

    resp = client.post("/ask", json={"run_id": str(run_number), "question": "how many rows?"})
    assert resp.status_code == 200, resp.text


def test_ask_accepts_a_hash_prefixed_run_number(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)
    resp = client.post("/ask", json={"run_id": f"#{result['run_number']}", "question": "how many rows?"})
    assert resp.status_code == 200, resp.text


def test_ask_still_accepts_a_full_run_uuid(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)
    resp = client.post("/ask", json={"run_id": result["run_id"], "question": "how many rows?"})
    assert resp.status_code == 200, resp.text


def test_ask_unknown_run_number_is_still_404(client):
    resp = client.post("/ask", json={"run_id": "999999999", "question": "how many rows?"})
    assert resp.status_code == 404


def test_predict_accepts_a_plain_run_number(client, tmp_path):
    """POST /predict resolves the run synchronously before queueing the
    background task, so a 200 ack proves resolution succeeded."""
    result = _ingest_clean_source(client, tmp_path)
    resp = client.post("/predict", json={"run_id": str(result["run_number"]), "question": "forecast amount"})
    assert resp.status_code == 200, resp.text


def test_predict_accepts_a_hash_prefixed_run_number(client, tmp_path):
    result = _ingest_clean_source(client, tmp_path)
    resp = client.post("/predict", json={"run_id": f"#{result['run_number']}", "question": "forecast amount"})
    assert resp.status_code == 200, resp.text


def test_predict_unknown_run_number_is_still_404(client):
    resp = client.post("/predict", json={"run_id": "999999999", "question": "forecast amount"})
    assert resp.status_code == 404


def test_every_run_taking_surface_resolves_the_same_run_number(client, tmp_path):
    """The consistency guarantee itself: one run number, four surfaces, all
    of them resolving it to the same run rather than some accepting only a
    UUID."""
    result = _ingest_clean_source(client, tmp_path)
    run_id, run_number = result["run_id"], result["run_number"]

    assert client.get(f"/reports/{run_number}").json()["run_id"] == run_id
    assert client.get(f"/audit/{run_number}").json()["run_id"] == run_id
    assert client.get(f"/ingest/{run_number}/status").json()["run_id"] == run_id
    assert client.post("/ask", json={"run_id": str(run_number), "question": "how many rows?"}).status_code == 200
    assert client.post("/predict", json={"run_id": str(run_number), "question": "forecast amount"}).status_code == 200
