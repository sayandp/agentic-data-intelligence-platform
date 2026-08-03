"""Verification-round fix: get_diagnostic_agent now degrades to None on
construction failure, the same pattern get_narrative_agent/get_query_agent/
get_modeling_agent already used (see app/diagnosis/dependency.py). This is
NOT a mocked absence of GEMINI_API_KEY - it overrides the dependency with
None directly, which is exactly what get_diagnostic_agent itself now
returns when construction fails; the two are equivalent by construction,
not merely similar.

The platform must be fully usable with no LLM configured at all: ingest
succeeds, validation still detects every failure, every detected failure
escalates to awaiting_approval (never auto-applied, never a 500), and
reports still generate from the deterministic template.
"""

from __future__ import annotations

from app.main import app
from app.routers.ingest import get_diagnostic_agent
from tests.golden_scenarios import _clean_df, _create_source, _write_csv, ingest_and_wait
from tests.corruption import CorruptionSuite


def _no_diagnostic_agent():
    return None


def test_clean_ingest_succeeds_with_no_llm_configured(client, tmp_path):
    app.dependency_overrides[get_diagnostic_agent] = _no_diagnostic_agent
    try:
        path = tmp_path / "clean.csv"
        _write_csv(path, _clean_df())
        source_id = _create_source(client, path)

        body = ingest_and_wait(client, source_id)
        assert body["status"] == "completed"
        assert body["baseline"]["is_provisional"] is True
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def test_corrupted_ingest_with_no_llm_detects_and_escalates_every_failure(client, tmp_path):
    app.dependency_overrides[get_diagnostic_agent] = _no_diagnostic_agent
    try:
        base_df = _clean_df()
        corrupted_df, _ = CorruptionSuite().apply(base_df, "inject_nulls", seed=1)
        path = tmp_path / "corrupt.csv"
        _write_csv(path, base_df)
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)

        _write_csv(path, corrupted_df)
        body = ingest_and_wait(client, source_id)
        # No LLM to diagnose with -> nothing is auto-fixed, everything
        # detected lands on awaiting_approval for a human to review.
        assert body["status"] == "awaiting_approval"
        assert body["validation_failure_count"] >= 1

        pending = client.get("/approvals/pending").json()
        group = next(g for g in pending["validation_events"] if g["run_id"] == body["run_id"])
        assert group["diagnosis"]["error"] == "no_llm_configured"
        assert group["action_taken"] is None  # never auto-applied
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def test_approve_is_refused_with_no_llm_since_there_is_no_suggested_fix(client, tmp_path):
    """A group escalated with no diagnosis has no suggested_fix.action to
    approve - 'approve' correctly refuses (422), same as any other
    diagnosis-less escalation (a parse failure, a quota exhaustion). The
    human-facing decisions that DO make sense (reject_fix, reject_data,
    accept_as_baseline) are unaffected - covered by the reject_fix path in
    the next test."""
    app.dependency_overrides[get_diagnostic_agent] = _no_diagnostic_agent
    try:
        base_df = _clean_df()
        corrupted_df, _ = CorruptionSuite().apply(base_df, "inject_nulls", seed=2)
        path = tmp_path / "corrupt2.csv"
        _write_csv(path, base_df)
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)
        _write_csv(path, corrupted_df)
        second = ingest_and_wait(client, source_id)

        pending = client.get("/approvals/pending").json()
        resolve_id = next(g for g in pending["validation_events"] if g["run_id"] == second["run_id"])["resolve_id"]

        resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "approve", "resolved_by": "tess"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def test_no_llm_escalation_still_resolves_and_run_completes(client, tmp_path):
    app.dependency_overrides[get_diagnostic_agent] = _no_diagnostic_agent
    try:
        base_df = _clean_df()
        corrupted_df, _ = CorruptionSuite().apply(base_df, "inject_nulls", seed=3)
        path = tmp_path / "corrupt3.csv"
        _write_csv(path, base_df)
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)
        _write_csv(path, corrupted_df)
        second = ingest_and_wait(client, source_id)

        pending = client.get("/approvals/pending").json()
        resolve_id = next(g for g in pending["validation_events"] if g["run_id"] == second["run_id"])["resolve_id"]

        resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": "reject_fix", "resolved_by": "tess"})
        assert resp.status_code == 200
        assert client.get(f"/audit/{second['run_id']}").json()["run_status"] == "completed"
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def test_report_generates_in_template_mode_with_no_llm(client, tmp_path):
    """No diagnostic agent AND no narrative agent (the conftest.py client
    fixture's own default) together - the report must still generate, just
    never as generation_mode='llm'."""
    app.dependency_overrides[get_diagnostic_agent] = _no_diagnostic_agent
    try:
        path = tmp_path / "clean_report.csv"
        _write_csv(path, _clean_df())
        source_id = _create_source(client, path)
        run = ingest_and_wait(client, source_id)
        assert run["status"] == "completed"
        assert run["report"]["generation_mode"] == "template"

        report = client.get(f"/reports/{run['run_id']}").json()
        assert report["generation_mode"] == "template"
        assert "## Data Quality Context" in report["narrative_text"]
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def test_startup_logs_llm_mode(capsys, monkeypatch):
    """app.main._log_llm_mode() prints the active provider or that none is
    configured - checked directly rather than via a real process boot,
    since that's what's actually reusable/testable here."""
    import app.main as main_module

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    main_module._log_llm_mode()
    out = capsys.readouterr().out
    assert "[startup] No LLM configured" in out
