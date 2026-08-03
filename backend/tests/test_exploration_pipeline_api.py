"""End-to-end tests for Phase 4's wiring: /ingest -> validation/repair ->
Exploration Agent -> exploration_findings row -> GET /findings/{run_id}.
Mirrors tests/test_action_executor_api.py's conventions - real endpoints,
FakeLLMClient only for the (unrelated) Diagnostic Agent dependency, no live
LLM call anywhere."""

from __future__ import annotations

from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from tests.fakes import diagnosis_override as _diagnosis
from tests.golden_scenarios import ingest_and_wait, resolve_and_wait


LOW_CONFIDENCE_RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="looks like a rename but I'm not sure",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.5,
)

RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="column relabeled upstream",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.95,
)


def _clean_csv_text(n: int = 60) -> str:
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago"]
    lines = ["id,city,amount"]
    for i in range(n):
        lines.append(f"{i},{cities[i % len(cities)]},{10.0 + i}")
    return "\n".join(lines) + "\n"


def _ingest_clean_source(client, tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    return source_id, csv_path


def test_clean_ingest_generates_persists_and_retrieves_findings(client, tmp_path):
    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    ingest_and_wait(client, source_id)  # establishes the baseline

    csv_path.write_text(_clean_csv_text(), encoding="utf-8")
    second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"
    assert second["findings"]["available"] is True
    assert second["findings"]["url"] == f"/findings/{second['run_id']}"

    resp = client.get(f"/findings/{second['run_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == second["run_id"]
    assert body["schema_version"] == 1
    findings = body["findings"]["findings"]
    assert findings  # something was actually computed

    correlations = [f for f in findings if f["finding_type"] == "correlation"]
    for corr in correlations:
        assert corr["evidence"]["sample_size"] > 0
        assert corr["evidence"]["p_value"] is not None

    # no causal vocabulary anywhere in the persisted payload
    causal_words = ("driver", "impact", "effect", "cause")
    payload_text = str(body["findings"]).lower()
    assert not any(word in payload_text for word in causal_words)


def test_getting_findings_for_unknown_run_404(client):
    resp = client.get("/findings/does-not-exist")
    assert resp.status_code == 404


def test_corrupted_ingest_that_escalates_has_no_findings_until_resolved(client, tmp_path):
    from app.db import SessionLocal
    from app.models import ExplorationFinding

    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    assert second["status"] == "awaiting_approval"
    assert second["findings"]["available"] is False
    assert second["findings"]["url"] is None

    resp = client.get(f"/findings/{second['run_id']}")
    assert resp.status_code == 404

    with SessionLocal() as db:
        assert db.query(ExplorationFinding).filter(ExplorationFinding.run_id == second["run_id"]).one_or_none() is None

    # Resolving the event resumes the run - exploration must generate now.
    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]
    resolve_resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "tester"})
    assert resolve_resp.status_code == 200

    final = client.get(f"/findings/{second['run_id']}")
    assert final.status_code == 200
    assert final.json()["findings"]["findings"]


def test_findings_computed_on_repaired_frame_not_the_raw_one(client, tmp_path):
    """The rename auto-fixes: exploration's summary_stat findings must be
    keyed on the baseline's column name ('city'), not the corrupted raw
    name ('town') that was actually on disk for this ingest."""
    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"  # high-confidence rename auto-fixes synchronously

    findings = client.get(f"/findings/{second['run_id']}").json()["findings"]["findings"]
    summary_columns = {f["columns"][0] for f in findings if f["finding_type"] == "summary_stat"}
    assert "city" in summary_columns
    assert "town" not in summary_columns


def test_data_quality_context_reflects_actual_resolutions(client, tmp_path):
    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    first_baseline_id = ingest_and_wait(client, source_id)["baseline"]["id"]
    client.post(f"/approvals/{first_baseline_id}/resolve", json={"decision": "approve", "resolved_by": "alice"})

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"

    findings = client.get(f"/findings/{second['run_id']}").json()["findings"]
    dqc = findings["data_quality_context"]
    assert dqc["total_events"] == 2  # missing_column + unexpected_column, one correlated pair
    assert dqc["resolution_counts"].get("auto_fixed") == 2
    assert dqc["active_baseline_provisional"] is False


def test_data_quality_context_flags_provisional_baseline_on_first_ingest(client, tmp_path):
    source_id, _csv_path = _ingest_clean_source(client, tmp_path)
    first = ingest_and_wait(client, source_id)
    assert first["status"] == "completed"
    assert first["baseline"]["is_provisional"] is True

    findings = client.get(f"/findings/{first['run_id']}").json()["findings"]
    assert findings["data_quality_context"]["active_baseline_provisional"] is True
    assert findings["data_quality_context"]["total_events"] == 0  # nothing to validate against yet


def test_no_validation_type_findings_duplicated_into_exploration_output(client, tmp_path):
    """Exploration must never re-report what ValidationEngine already
    detected (nulls, drift, schema) as its own finding - the rule-family
    vocabulary must not leak into the exploration payload at all."""
    from app.validation.engine import (
        CATEGORICAL_DRIFT,
        DISTRIBUTION_DRIFT,
        NULL_THRESHOLD,
        SCHEMA_CONFORMANCE,
    )

    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)
    assert second["status"] == "completed"

    findings_json = client.get(f"/findings/{second['run_id']}").json()["findings"]
    allowed_finding_types = {
        "summary_stat",
        "correlation",
        "outlier_cluster",
        "trend",
        "distribution_shape",
        "cardinality_note",
        "missing_pattern",
    }
    seen_types = {f["finding_type"] for f in findings_json["findings"]}
    assert seen_types <= allowed_finding_types
    for rule_family in (SCHEMA_CONFORMANCE, NULL_THRESHOLD, DISTRIBUTION_DRIFT, CATEGORICAL_DRIFT):
        assert rule_family not in str(findings_json["findings"])


def test_reject_data_run_never_gets_findings(client, tmp_path):
    source_id, csv_path = _ingest_clean_source(client, tmp_path)
    ingest_and_wait(client, source_id)

    csv_path.write_text(_clean_csv_text().replace("city", "town"), encoding="utf-8")
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        second = ingest_and_wait(client, source_id)

    pending = client.get("/approvals/pending").json()
    resolve_id = pending["validation_events"][0]["resolve_id"]
    result = resolve_and_wait(client, resolve_id, "reject_data", "tester")
    assert result["run_status"] == "failed"

    assert client.get(f"/findings/{second['run_id']}").status_code == 404


def test_wide_dataset_correlation_capped_with_explained_skips(client, tmp_path):
    lines = ["id," + ",".join(f"col{i}" for i in range(60))]
    for row in range(80):
        values = ",".join(str((row * (i + 1)) % 97) for i in range(60))
        lines.append(f"{row},{values}")
    csv_path = tmp_path / "wide.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    result = ingest_and_wait(client, source_id)
    assert result["status"] == "completed"

    findings_json = client.get(f"/findings/{result['run_id']}").json()["findings"]
    cap_skips = [s for s in findings_json["skipped"] if "cap" in s["reason"]]
    assert cap_skips
    assert all("50" in s["reason"] for s in cap_skips)
