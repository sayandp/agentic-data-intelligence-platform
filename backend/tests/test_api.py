from tests.golden_scenarios import ingest_and_wait


def test_create_source_invalid_type(client):
    resp = client.post("/sources", json={"type": "bogus", "connection_config": {}})
    assert resp.status_code == 400


def test_create_source_valid(client):
    resp = client.post("/sources", json={"type": "file", "connection_config": {"path": "x.csv"}})
    assert resp.status_code == 200
    assert "id" in resp.json()


def test_ingest_missing_source(client):
    resp = client.post("/ingest/does-not-exist")
    assert resp.status_code == 404


def test_ingest_end_to_end(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,name,amount\n1,foo,10.5\n2,bar,20.5\n", encoding="utf-8")

    create_resp = client.post(
        "/sources",
        json={"type": "file", "connection_config": {"path": str(csv_path)}},
    )
    source_id = create_resp.json()["id"]

    body = ingest_and_wait(client, source_id)

    assert body["status"] == "completed"
    assert "run_id" in body
    assert body["metadata"]["row_count"] == 2
    assert body["metadata"]["column_types"]["id"] == "int64"
    assert body["metadata"]["column_types"]["amount"] == "float64"
    assert body["metadata"]["source_type"] == "file"


def test_first_ingest_establishes_baseline_with_no_validation(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Baseline, ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,name,amount\n1,foo,10.5\n2,bar,20.5\n", encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]

    body = ingest_and_wait(client, source_id)

    assert body["validation_failure_count"] == 0

    with SessionLocal() as db:
        baselines = db.query(Baseline).filter(Baseline.source_id == source_id).all()
        assert len(baselines) == 1
        assert baselines[0].is_active is True
        assert baselines[0].profile_json["row_count"] == 2

        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == body["run_id"]).all()
        assert events == []


def test_second_clean_ingest_produces_no_validation_events(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,name,amount\n1,foo,10.5\n2,bar,20.5\n3,baz,15.0\n", encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]

    ingest_and_wait(client, source_id)  # establishes baseline
    second = ingest_and_wait(client, source_id)  # same shape/values, unchanged

    assert second["status"] == "completed"
    assert second["validation_failure_count"] == 0


def test_corrupted_second_ingest_logs_validation_events(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,name,amount\n1,foo,10.5\n2,bar,20.5\n3,baz,15.0\n", encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes baseline

    # Drop the "amount" column entirely on the next ingest.
    csv_path.write_text("id,name\n1,foo\n2,bar\n3,baz\n", encoding="utf-8")
    body = ingest_and_wait(client, source_id)

    # An uncorrelated missing_column has no entry in the gate's applicability
    # matrix, so no action is ever permitted for it regardless of what the
    # (fake, default-escalate) diagnosis says - the run halts for review.
    assert body["status"] == "awaiting_approval"
    assert body["validation_failure_count"] >= 1

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == body["run_id"]).all()
        rules = [e.rule_failed for e in events]
        assert any("missing_column:amount" in r for r in rules)
        assert all(e.state == "awaiting_approval" for e in events)
        assert all(e.diagnosis_json is not None and e.risk_level is not None for e in events)
        assert all(e.action_taken is None for e in events)  # never attempted, not reverted


def test_recompute_baseline_supersedes_previous(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Baseline

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("id,amount\n1,10.5\n2,20.5\n", encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes first baseline via auto-baseline

    csv_path.write_text("id,amount\n1,10.5\n2,20.5\n3,30.5\n4,40.5\n", encoding="utf-8")
    resp = client.post(f"/sources/{source_id}/baseline")

    assert resp.status_code == 200
    assert resp.json()["row_count"] == 4

    with SessionLocal() as db:
        baselines = db.query(Baseline).filter(Baseline.source_id == source_id).order_by(Baseline.created_at).all()
        assert len(baselines) == 2
        assert baselines[0].is_active is False
        assert baselines[1].is_active is True
        assert baselines[1].profile_json["row_count"] == 4


def test_ingest_failure_marks_run_failed(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Run

    missing_path = tmp_path / "does_not_exist.csv"
    create_resp = client.post(
        "/sources",
        json={"type": "file", "connection_config": {"path": str(missing_path)}},
    )
    source_id = create_resp.json()["id"]

    # DELIBERATE CHANGE from the pre-fix assertion (ingest_resp.status_code
    # == 500): POST /ingest can no longer know synchronously that the graph
    # is about to fail - it returns 200 + {run_id, status: "running"} for
    # every valid source_id now, and the graph runs in the background (see
    # app/routers/ingest.py's module docstring - this is the fix for a real
    # hang, not a stylistic change). The property this test exists to
    # verify - a connector failure ends at "failed", never stuck "running"
    # - is unchanged and still checked below, just observed by polling to
    # the terminal state instead of an immediate HTTP status code that no
    # longer applies.
    result = ingest_and_wait(client, source_id)
    assert result["status"] == "failed"

    with SessionLocal() as db:
        runs = db.query(Run).filter(Run.source_id == source_id).all()
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].completed_at is not None


def _clean_csv_text(n: int = 20) -> str:
    cities = ["NYC", "LA", "SF", "Chicago"]
    lines = ["id,city,amount"]
    for i in range(n):
        lines.append(f"{i},{cities[i % len(cities)]},{10.0 + i}")
    return "\n".join(lines) + "\n"


def test_first_ingest_baseline_is_provisional_and_listed_pending(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]

    body = ingest_and_wait(client, source_id)

    assert body["baseline"]["is_provisional"] is True
    baseline_id = body["baseline"]["id"]

    pending = client.get("/approvals/pending").json()
    assert baseline_id in [b["id"] for b in pending["provisional_baselines"]]


def test_approve_provisional_baseline_clears_pending(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]

    resp = client.post(f"/approvals/{baseline_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    assert resp.status_code == 200
    assert resp.json() == {"id": baseline_id, "type": "baseline", "decision": "approve"}
    pending = client.get("/approvals/pending").json()
    assert baseline_id not in [b["id"] for b in pending["provisional_baselines"]]


def test_validation_events_flagged_against_provisional_baseline(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes provisional baseline, left unconfirmed

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    second = ingest_and_wait(client, source_id)

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert events
        assert all(e.against_provisional_baseline for e in events)


def test_reject_provisional_baseline_lets_next_ingest_retry(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Baseline

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    first_baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]

    client.post(f"/approvals/{first_baseline_id}/resolve", json={"decision": "reject_data", "resolved_by": "alice"})

    second = ingest_and_wait(client, source_id)

    assert second["baseline"]["id"] != first_baseline_id
    assert second["baseline"]["is_provisional"] is True
    assert second["validation_failure_count"] == 0  # no active baseline existed to validate against

    with SessionLocal() as db:
        baselines = db.query(Baseline).filter(Baseline.source_id == source_id).all()
        assert len(baselines) == 2
        rejected = next(b for b in baselines if b.id == first_baseline_id)
        assert rejected.is_active is False
        assert rejected.resolved_by == "alice"


def test_garbage_first_ingest_rejects_baseline_and_can_retry(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Baseline

    csv_path = tmp_path / "garbage.csv"
    rows = "\n".join(f"{i},7.0" for i in range(20))  # amount has zero variance
    csv_path.write_text(f"id,amount\n{rows}\n", encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]

    body = ingest_and_wait(client, source_id)

    assert body["status"] == "completed"  # ingestion itself still succeeded
    assert body["baseline"] is None
    assert body["baseline_rejected_reasons"]
    assert "zero variance" in body["baseline_rejected_reasons"][0]

    with SessionLocal() as db:
        assert db.query(Baseline).filter(Baseline.source_id == source_id).count() == 0

    # A later, healthy ingest should still be able to establish a baseline.
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    second = ingest_and_wait(client, source_id)
    assert second["baseline"] is not None
    assert second["baseline"]["is_provisional"] is True


def test_resolve_unknown_id_returns_404(client):
    resp = client.post("/approvals/does-not-exist/resolve", json={"decision": "approve", "resolved_by": "alice"})
    assert resp.status_code == 404


def test_resolve_already_resolved_baseline_conflicts(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]
    client.post(f"/approvals/{baseline_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    second = client.post(f"/approvals/{baseline_id}/resolve", json={"decision": "approve", "resolved_by": "bob"})

    assert second.status_code == 409


def test_recomputed_baseline_is_not_provisional(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)

    resp = client.post(f"/sources/{source_id}/baseline")

    assert resp.json()["is_provisional"] is False


def test_recompute_baseline_rejects_garbage(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)

    rows = "\n".join(f"{i},7.0" for i in range(20))
    csv_path.write_text(f"id,amount\n{rows}\n", encoding="utf-8")
    resp = client.post(f"/sources/{source_id}/baseline")

    assert resp.status_code == 422
    assert "reasons" in resp.json()["detail"]


def test_multi_sheet_excel_warning_surfaces_as_validation_event_on_first_ingest(client, tmp_path):
    import pandas as pd
    import pytest

    from app.db import SessionLocal
    from app.models import ValidationEvent

    pytest.importorskip("openpyxl")

    xlsx_path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame({"x": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [3, 4]}).to_excel(writer, sheet_name="second", index=False)

    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(xlsx_path)}}
    ).json()["id"]

    body = ingest_and_wait(client, source_id)

    # First-ever ingest for this source: no baseline exists yet, but the
    # connector warning still surfaces - it doesn't depend on one.
    assert body["baseline"]["is_provisional"] is True
    assert body["validation_failure_count"] == 1

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == body["run_id"]).all()
        assert len(events) == 1
        assert events[0].rule_failed.startswith("connector_warning")
        assert "2 sheets" in events[0].detail_json["message"]
        assert events[0].against_provisional_baseline is False


# ---------------------------------------------------------------------------
# Phase 7.5 Part 3: connector warnings get a real destination - visible
# through an HTTP surface, acknowledgeable to a terminal state, never
# blocking the run, and included in the run's audit trail. Previously they
# were written at state="detected" and never advanced - not diagnosed, not
# gated, not returned by any endpoint, visible only by querying the
# database directly.
# ---------------------------------------------------------------------------


def _ingest_multi_sheet_excel(client, tmp_path):
    import pandas as pd
    import pytest

    pytest.importorskip("openpyxl")

    xlsx_path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame({"x": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [3, 4]}).to_excel(writer, sheet_name="second", index=False)

    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(xlsx_path)}}).json()["id"]
    body = ingest_and_wait(client, source_id)
    return source_id, body


def test_connector_warning_never_blocks_the_run(client, tmp_path):
    _source_id, body = _ingest_multi_sheet_excel(client, tmp_path)
    assert body["status"] == "completed"  # a multi-sheet warning is informational, never a reason to pause


def test_connector_warning_retrievable_via_approvals_pending(client, tmp_path):
    _source_id, body = _ingest_multi_sheet_excel(client, tmp_path)

    pending = client.get("/approvals/pending").json()
    assert len(pending["connector_warnings"]) == 1
    entry = pending["connector_warnings"][0]
    assert entry["run_id"] == body["run_id"]
    assert "2 sheets" in entry["message"]


def test_connector_warning_acknowledgeable_to_a_terminal_state(client, tmp_path):
    _source_id, body = _ingest_multi_sheet_excel(client, tmp_path)
    warning_id = client.get("/approvals/pending").json()["connector_warnings"][0]["id"]

    resolve_resp = client.post(f"/approvals/{warning_id}/resolve", json={"decision": "acknowledge", "resolved_by": "alice"})
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["decision"] == "acknowledge"

    pending = client.get("/approvals/pending").json()
    assert pending["connector_warnings"] == []

    from app.db import SessionLocal
    from app.models import ValidationEvent

    with SessionLocal() as db:
        event = db.get(ValidationEvent, warning_id)
        assert event.state == "resolved"
        assert event.action_taken == "acknowledged"
        assert event.resolved_by == "alice"


def test_connector_warning_wrong_decision_rejected(client, tmp_path):
    _source_id, body = _ingest_multi_sheet_excel(client, tmp_path)
    warning_id = client.get("/approvals/pending").json()["connector_warnings"][0]["id"]

    resp = client.post(f"/approvals/{warning_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})
    assert resp.status_code == 422


def test_connector_warning_appears_in_run_audit_trail(client, tmp_path):
    _source_id, body = _ingest_multi_sheet_excel(client, tmp_path)

    audit = client.get(f"/runs/{body['run_id']}/audit-trail").json()
    assert audit["run_id"] == body["run_id"]
    rules_failed = [e["rule_failed"] for e in audit["events"]]
    assert any(r.startswith("connector_warning") for r in rules_failed)


def test_audit_trail_unknown_run_is_404(client):
    assert client.get("/runs/does-not-exist/audit-trail").status_code == 404


def test_rename_corruption_events_share_correlation_group(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes baseline

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    second = ingest_and_wait(client, source_id)

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert len(events) == 2
        group_ids = {e.correlation_group_id for e in events}
        assert len(group_ids) == 1
        assert None not in group_ids
        assert all(e.correlation_rule == "missing_unexpected_column_pair" for e in events)


def test_unrelated_drop_and_add_do_not_share_correlation_group(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes baseline

    # Drop "city" (categorical) and add an unrelated numeric column - a dtype
    # mismatch that should keep the correlator from pairing them.
    lines = ["id,amount,extra_metric"]
    for i in range(20):
        lines.append(f"{i},{10.0 + i},{i * 1.5}")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    second = ingest_and_wait(client, source_id)

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert len(events) == 2
        assert all(e.correlation_group_id is None for e in events)
        assert all(e.correlation_rule is None for e in events)
