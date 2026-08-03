"""End-to-end tests for Part 5 (gate + action executor) through the real
/ingest and /approvals endpoints. Each test swaps in a custom FakeLLMClient
via dependency override so the diagnosis returned is exactly what the test
needs - never a live call."""

from __future__ import annotations

from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from tests.fakes import diagnosis_override as _diagnosis
from tests.golden_scenarios import ingest_and_wait, resolve_and_wait


def _clean_csv_text(n: int = 20) -> str:
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago"]
    lines = ["id,city,amount"]
    for i in range(n):
        lines.append(f"{i},{cities[i % len(cities)]},{10.0 + i}")
    return "\n".join(lines) + "\n"


RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="column relabeled upstream",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,\
    confidence=0.95,
)

LOW_CONFIDENCE_RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="looks like a rename but I'm not sure",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.5,
)

WRONG_ACTION_FOR_DROP_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.DTYPE_CHANGE,
    likely_cause="wrong or manipulated diagnosis",
    suggested_fix=SuggestedFix(action=FixAction.SAFE_TYPE_CAST),
    risk_level=RiskLevel.LOW,
    confidence=0.99,
)

STRIP_WHITESPACE_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.WHITESPACE_CASE,
    likely_cause="stray whitespace",
    suggested_fix=SuggestedFix(action=FixAction.STRIP_WHITESPACE),
    risk_level=RiskLevel.LOW,
    confidence=0.9,
)


def test_rename_auto_fixes_end_to_end(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    assert second["status"] == "completed"
    assert second["metadata"]["column_types"].get("city") is not None  # repaired: back to the baseline name
    assert "town" not in second["metadata"]["column_types"]

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert len(events) == 2
        assert all(e.state == "auto_fixed" for e in events)
        assert all(e.action_taken == "auto_fixed" for e in events)
        assert all(e.reversal_json is not None for e in events)

        from app.models import Run

        run = db.get(Run, second["run_id"])
        assert run.fix_chain == [{"action": "rename_column", "spec": {"current_name": "town", "target_name": "city"}}]


def test_low_confidence_rename_escalates_without_attempting_fix(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    assert second["status"] == "awaiting_approval"
    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "awaiting_approval" for e in events)
        assert all(e.action_taken is None for e in events)  # never attempted
        assert all(e.gate_reasons_json for e in events)


def test_matrix_refuses_wrong_action_even_with_perfect_confidence(client, tmp_path):
    """The exact scenario from the addendum, through the real pipeline: a
    drop_column failure where the diagnosis claims safe_type_cast/low/0.99."""
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    # Drop "city" entirely - an uncorrelated missing_column.
    lines = ["id,amount"] + [f"{i},{10.0 + i}" for i in range(20)]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with _diagnosis(WRONG_ACTION_FOR_DROP_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    assert second["status"] == "awaiting_approval"
    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "awaiting_approval" for e in events)
        assert all(e.action_taken is None for e in events)
        assert any("not permitted for this rule" in reason for e in events for reason in (e.gate_reasons_json or []))


def test_post_condition_failure_reverts_and_escalates(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    # Case-only corruption: strip_whitespace cannot resolve it.
    corrupted = _clean_csv_text().upper().replace("ID,CITY,AMOUNT", "id,city,amount")
    csv_path.write_text(corrupted, encoding="utf-8")
    with _diagnosis(STRIP_WHITESPACE_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    assert second["status"] == "awaiting_approval"
    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        categorical_events = [e for e in events if e.rule_failed.startswith("categorical_drift")]
        assert categorical_events
        assert all(e.state == "awaiting_approval" for e in categorical_events)
        assert all(e.action_taken == "auto_fix_reverted" for e in categorical_events)
        assert all(e.reversal_json is not None for e in categorical_events)
        assert all(
            any("post-condition failed" in r for r in (e.gate_reasons_json or [])) for e in categorical_events
        )

        from app.models import Run

        run = db.get(Run, second["run_id"])
        assert not run.fix_chain  # a reverted fix never joins the chain


def test_approvals_pending_lists_awaiting_events_with_diagnosis(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        ingest_and_wait(client, source_id)

    pending = client.get("/approvals/pending").json()

    assert len(pending["validation_events"]) == 1  # the rename pair is ONE group
    group = pending["validation_events"][0]
    assert len(group["rules_failed"]) == 2
    assert group["diagnosis"]["cause_category"] == "rename"
    assert group["risk_level"] == "low"


def test_approve_applies_fix_and_resumes_run(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Run, ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "awaiting_approval"

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]

    result = resolve_and_wait(client, resolve_id, "approve", "alice")
    assert result["applied"] is True

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "resolved" for e in events)
        assert all(e.action_taken == "approved_and_applied" for e in events)
        assert all(e.resolved_by == "alice" for e in events)

        run = db.get(Run, second["run_id"])
        assert run.status == "completed"  # resuming the last pending event resumes the run
        assert run.fix_chain

    assert client.get("/approvals/pending").json()["validation_events"] == []


def test_reject_leaves_data_untouched_and_resumes_run(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Run, ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]

    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "bob"})
    assert resp.status_code == 200

    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "rejected" for e in events)
        # no FIX was applied, but action_taken records the decision made
        assert all(e.action_taken == "rejected_fix_data_acceptable" for e in events)

        run = db.get(Run, second["run_id"])
        assert run.status == "completed"
        assert not run.fix_chain


def test_approve_non_actionable_event_returns_422(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    lines = ["id,amount"] + [f"{i},{10.0 + i}" for i in range(20)]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with _diagnosis(WRONG_ACTION_FOR_DROP_DIAGNOSIS):
        ingest_and_wait(client, source_id)

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]

    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})
    assert resp.status_code == 422


def _numeric_csv_text(n: int, scale: float = 1.0) -> str:
    lines = ["id,amount"]
    for i in range(n):
        lines.append(f"{i},{(10.0 + i) * scale}")
    return "\n".join(lines) + "\n"


def test_reject_data_terminates_run_and_writes_no_downstream_state(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Run, ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "awaiting_approval"

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]

    result = resolve_and_wait(client, resolve_id, "reject_data", "carol")
    assert result["run_status"] == "failed"

    with SessionLocal() as db:
        run = db.get(Run, second["run_id"])
        assert run.status == "failed"
        assert not run.fix_chain  # nothing downstream was written

        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "rejected" for e in events)
        assert all(e.action_taken == "rejected_data_unfit" for e in events)
        assert all(e.reversal_json is None for e in events)  # nothing was ever applied to revert

    # A failed run must not resume via the pending-event bookkeeping either.
    assert client.get("/approvals/pending").json()["validation_events"] == []


def test_accept_as_baseline_supersedes_and_corruption_does_not_refire(client, tmp_path):
    """distribution_drift permits no auto-fix action at all (matrix denies
    everything for it) - accept_as_baseline is the only resolution path that
    can actually clear it, by declaring the shift legitimate."""
    from app.db import SessionLocal
    from app.models import Baseline, ValidationEvent

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_numeric_csv_text(80), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    first_baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]
    client.post(f"/approvals/{first_baseline_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    shifted = _numeric_csv_text(80, scale=10.0)
    csv_path.write_text(shifted, encoding="utf-8")
    second = ingest_and_wait(client, source_id)
    assert second["status"] == "awaiting_approval"

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]
    result = resolve_and_wait(client, resolve_id, "accept_as_baseline", "dana")
    new_baseline_id = result["new_baseline_id"]

    with SessionLocal() as db:
        baselines = db.query(Baseline).filter(Baseline.source_id == source_id).all()
        assert len(baselines) == 2
        old = next(b for b in baselines if b.id != new_baseline_id)
        assert old.is_active is False
        new = next(b for b in baselines if b.id == new_baseline_id)
        assert new.is_active is True
        assert new.is_provisional is False

        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == second["run_id"]).all()
        assert all(e.state == "resolved" for e in events)
        assert all(e.action_taken == "accepted_as_new_baseline" for e in events)

    # The SAME shifted data, ingested again, must no longer flag distribution_drift.
    csv_path.write_text(shifted, encoding="utf-8")
    third = ingest_and_wait(client, source_id)
    assert third["status"] == "completed"
    assert third["validation_failure_count"] == 0


def test_reject_data_is_not_a_valid_baseline_decision(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]

    resp = client.post(f"/approvals/{baseline_id}/resolve", json={"decision": "reject_fix", "resolved_by": "alice"})
    assert resp.status_code == 422

    resp2 = client.post(
        f"/approvals/{baseline_id}/resolve", json={"decision": "accept_as_baseline", "resolved_by": "alice"}
    )
    assert resp2.status_code == 422
