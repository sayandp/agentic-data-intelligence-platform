"""The battery of ingest/approval scenarios shared between the
pre-migration golden capture test (tests/test_migration_golden_capture.py)
and the post-migration one (tests/test_graph_matches_golden.py). Both call
run_all_scenarios() against whatever router implementation is currently
wired into the app (pre- or post-graph) and get back an identically-shaped
capture dict, keyed by scenario name - this is what makes the two tests
comparable at all.

This module contains NO golden-file I/O (no read, no write) - see
tests/golden_compare.py for reading + comparing, and
scripts/record_golden_baseline.py for the one explicit, standalone place
recording is ever allowed to happen (Rule 2: never regenerate the golden
file to make a test pass).
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from tests.corruption import CorruptionSuite
from tests.fakes import diagnosis_override as _diagnosis

INGEST_POLL_TIMEOUT_SECONDS = 30.0
INGEST_POLL_INTERVAL_SECONDS = 0.02
# Separate, SHORT, bounded grace period for report generation specifically
# (see the "still_generating_report" comment below) - deliberately NOT the
# same budget as INGEST_POLL_TIMEOUT_SECONDS. A run whose narrate_node
# itself failed/degraded (test_graph_orchestration.py's
# test_node_failure_never_leaves_run_non_terminal[narrate] deliberately
# injects exactly this) stays "completed" with report.available=False
# FOREVER, indistinguishable from "hasn't gotten there yet" by the shape of
# this response alone - a short, separate grace period lets the common
# case (report shows up in well under a second with the fake LLM clients
# every other test uses) resolve fast, while a permanently-missing report
# still gives up promptly instead of burning the entire outer timeout.
REPORT_GRACE_PERIOD_SECONDS = 10.0


def ingest_and_wait(client, source_id: str) -> dict:
    """POST /ingest now returns {run_id, status: "running"} immediately and
    runs the graph via a FastAPI BackgroundTask (app/routers/ingest.py) -
    the fix for a real bug where a browser fetch() held the connection open
    for however long narrate_node's LLM calls took (measured: 0.3s to 60s+
    to what looked like an indefinite hang), because the run itself is
    marked "completed" in the DB before narrate_node even runs, misleading
    both a human checking the DB and this test suite about whether the
    request was actually done. Every scenario below still needs the SAME
    full result shape POST /ingest used to return synchronously - this
    polls GET /ingest/{run_id}/status (identical shape, same
    _serialize_run_response) until the run leaves "running" and returns
    that, so every existing assertion on the result dict is unchanged."""
    resp = client.post(f"/ingest/{source_id}")
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    deadline = time.monotonic() + INGEST_POLL_TIMEOUT_SECONDS
    completed_seen_at: float | None = None
    while True:
        body = client.get(f"/ingest/{run_id}/status").json()
        # app/graph/nodes.py::explore_node marks the run "completed" BEFORE
        # narrate_node (the actual report-generating LLM call) ever runs -
        # the pre-fix synchronous contract guaranteed the report was ready
        # by the time a caller saw "completed" (graph.invoke() didn't
        # return until narrate_node finished too); keep polling a little
        # longer here so that guarantee still holds for every caller of
        # this helper, rather than silently narrowing it to "the run
        # object is done" while GET /reports/{run_id} still 404s.
        still_generating_report = body["status"] == "completed" and not (body.get("report") or {}).get("available", True)
        if still_generating_report:
            if completed_seen_at is None:
                completed_seen_at = time.monotonic()
            if time.monotonic() - completed_seen_at > REPORT_GRACE_PERIOD_SECONDS:
                return body  # narrate_node degraded/failed - report will never come; return what we have
        elif body["status"] != "running":
            return body
        if time.monotonic() > deadline:
            raise TimeoutError(f"run {run_id} did not reach a stable terminal state within {INGEST_POLL_TIMEOUT_SECONDS}s")
        time.sleep(INGEST_POLL_INTERVAL_SECONDS)


def resolve_and_wait(client, resolve_id: str, decision: str, resolved_by: str) -> dict:
    """POST /approvals/{id}/resolve for a validation_event now returns
    {id, type, decision, status: "resolving", run_id} immediately and
    resumes the graph via a FastAPI BackgroundTask (app/routers/
    approvals.py::_resolve_validation_event) - the same fix ingest_and_wait
    above exists for, applied to the second place graph.invoke() used to
    run inline (resuming an escalation can hit narrate_node's LLM chain
    exactly like a fresh ingest can). Polls GET /ingest/{run_id}/status?
    resolve_id=<resolve_id> (find_resolution_response in app/routers/
    ingest.py) until that specific decision's outcome is recorded, and
    returns EXACTLY the shape POST /approvals/{id}/resolve used to return
    synchronously - every existing assertion on the result dict is
    unchanged, only how it's obtained changes."""
    resp = client.post(f"/approvals/{resolve_id}/resolve", json={"decision": decision, "resolved_by": resolved_by})
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    deadline = time.monotonic() + INGEST_POLL_TIMEOUT_SECONDS
    while True:
        body = client.get(f"/ingest/{run_id}/status", params={"resolve_id": resolve_id}).json()
        resolution = body.get("resolution")
        if resolution is not None:
            return resolution
        if time.monotonic() > deadline:
            raise TimeoutError(f"resolve {resolve_id} (decision={decision!r}) did not complete within {INGEST_POLL_TIMEOUT_SECONDS}s")
        time.sleep(INGEST_POLL_INTERVAL_SECONDS)


# One representative, deterministic diagnosis per corruption family - low
# risk + a matrix-permitted action where the ground truth says a fix should
# be attempted, ESCALATE where it says the risk is high. change_dtype is
# deliberately given a matrix-permitted, "correct-looking" diagnosis too -
# its own corruption (a "$"-prefixed currency string) makes safe_type_cast
# fail regardless, which is the point: not every "correct" diagnosis yields
# a usable fix.
DIAGNOSIS_BY_CORRUPTION: dict[str, Diagnosis] = {
    "rename_column": Diagnosis(
        cause_category=CauseCategory.RENAME, likely_cause="column relabeled upstream",
        suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN), risk_level=RiskLevel.LOW, confidence=0.95,
    ),
    "change_dtype": Diagnosis(
        cause_category=CauseCategory.DTYPE_CHANGE, likely_cause="numeric column arrived as text",
        suggested_fix=SuggestedFix(action=FixAction.SAFE_TYPE_CAST), risk_level=RiskLevel.LOW, confidence=0.95,
    ),
    "inject_nulls": Diagnosis(
        cause_category=CauseCategory.NULL_FLOOD, likely_cause="null rate well above baseline tolerance",
        suggested_fix=SuggestedFix(action=FixAction.ESCALATE), risk_level=RiskLevel.HIGH, confidence=0.9,
    ),
    "drop_column": Diagnosis(
        cause_category=CauseCategory.COLUMN_DROPPED, likely_cause="column absent from current schema",
        suggested_fix=SuggestedFix(action=FixAction.ESCALATE), risk_level=RiskLevel.HIGH, confidence=0.9,
    ),
    "shift_distribution": Diagnosis(
        cause_category=CauseCategory.DISTRIBUTION_SHIFT, likely_cause="numeric distribution has moved",
        suggested_fix=SuggestedFix(action=FixAction.ESCALATE), risk_level=RiskLevel.HIGH, confidence=0.9,
    ),
    "inject_whitespace_case": Diagnosis(
        cause_category=CauseCategory.WHITESPACE_CASE, likely_cause="stray whitespace/case variance",
        suggested_fix=SuggestedFix(action=FixAction.STRIP_WHITESPACE), risk_level=RiskLevel.LOW, confidence=0.9,
    ),
    "truncate_rows": Diagnosis(
        cause_category=CauseCategory.ROW_LOSS, likely_cause="row count dropped well below baseline",
        suggested_fix=SuggestedFix(action=FixAction.ESCALATE), risk_level=RiskLevel.HIGH, confidence=0.9,
    ),
}

LOW_CONFIDENCE_RENAME_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME, likely_cause="looks like a rename but I'm not sure",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN), risk_level=RiskLevel.LOW, confidence=0.5,
)


def _clean_df(n: int = 60) -> pd.DataFrame:
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago"]
    return pd.DataFrame({"id": range(n), "city": [cities[i % len(cities)] for i in range(n)], "amount": [10.0 + i for i in range(n)]})


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def _create_source(client, path: Path) -> str:
    return client.post("/sources", json={"type": "file", "connection_config": {"path": str(path)}}).json()["id"]


def capture_run(run_id: str) -> dict:
    from app.db import SessionLocal
    from app.models import AgentTrace, Baseline, ExplorationFinding, Report, Run, ValidationEvent

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        events = (
            db.query(ValidationEvent)
            .filter(ValidationEvent.run_id == run_id)
            .order_by(ValidationEvent.created_at, ValidationEvent.id)
            .all()
        )
        traces = db.query(AgentTrace).filter(AgentTrace.run_id == run_id).order_by(AgentTrace.timestamp, AgentTrace.id).all()
        baseline = (
            db.query(Baseline).filter(Baseline.source_id == run.source_id, Baseline.is_active.is_(True)).one_or_none()
        )
        exploration = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one_or_none()
        report = db.query(Report).filter(Report.run_id == run_id).one_or_none()

        return {
            "run_status": run.status,
            "fix_chain": run.fix_chain,
            "reveal_depth_reached": run.reveal_depth_reached,
            "baseline_active": {
                "exists": baseline is not None,
                "is_provisional": baseline.is_provisional if baseline else None,
                "row_count": baseline.profile_json.get("row_count") if baseline else None,
                "excluded_columns": [e["column"] for e in baseline.profile_json.get("excluded_columns", [])] if baseline else None,
            },
            "events": [
                {
                    "rule_failed": e.rule_failed,
                    "state": e.state,
                    "action_taken": e.action_taken,
                    "correlated": e.correlation_group_id is not None,
                }
                for e in events
            ],
            # Addition A: ordered node-visit sequence - a reordering under the
            # future graph is visible here, not silently absorbed by matching
            # end states alone.
            "agent_trace_sequence": [t.agent_name for t in traces],
            "exploration_present": exploration is not None,
            "report_present": report is not None,
            "report_generation_mode": report.generation_mode if report else None,
        }


def _resolve_id_for_run(client, run_id: str) -> str:
    pending = client.get("/approvals/pending").json()["validation_events"]
    match = next(g for g in pending if g["run_id"] == run_id)
    return match["resolve_id"]


def run_all_scenarios(client, tmp_path) -> dict[str, dict]:
    """Runs the full battery against whatever ingest/approvals
    implementation is currently wired into `client`'s app - pre-graph or
    post-graph - and returns the capture dict. Every inline assertion here
    is a fact about CORRECT behaviour (not merely "whatever happened"), so
    this function stays meaningful as a real regression check even when run
    against the post-migration graph, not just as a recorder."""
    golden: dict[str, dict] = {}
    suite = CorruptionSuite()

    # ------------------------------------------------------------------
    # 1. First-ever ingest, clean data: baseline established, provisional,
    #    no validation events at all (no baseline existed to compare against).
    # ------------------------------------------------------------------
    clean_path = tmp_path / "clean_first.csv"
    _write_csv(clean_path, _clean_df())
    source_clean = _create_source(client, clean_path)
    first = ingest_and_wait(client, source_clean)
    assert first["status"] == "completed"
    assert first["baseline"]["is_provisional"] is True
    cap = capture_run(first["run_id"])
    assert cap["run_status"] == "completed"
    assert cap["events"] == []
    assert cap["exploration_present"] is True
    golden["first_clean_ingest"] = cap

    # ------------------------------------------------------------------
    # 2. Second ingest, still clean: baseline exists, validate() runs and
    #    finds nothing - the validate/correlate traces still fire even
    #    though nothing failed.
    # ------------------------------------------------------------------
    second_clean = ingest_and_wait(client, source_clean)
    assert second_clean["status"] == "completed"
    assert second_clean["validation_failure_count"] == 0
    cap = capture_run(second_clean["run_id"])
    assert cap["events"] == []
    assert "validation" in cap["agent_trace_sequence"]
    assert "correlation" in cap["agent_trace_sequence"]
    golden["second_clean_ingest_noop"] = cap

    # ------------------------------------------------------------------
    # 3. Every corruption in the registry, each against its own fresh
    #    source/baseline, diagnosed exactly as mapped above.
    # ------------------------------------------------------------------
    base_df = _clean_df()
    corrupted_by_name = suite.apply_all(base_df, base_seed=100)
    run_id_by_corruption: dict[str, str] = {}

    for name, (corrupted_df, truth) in corrupted_by_name.items():
        path = tmp_path / f"{name}_clean.csv"
        _write_csv(path, base_df)
        source_id = _create_source(client, path)
        ingest_and_wait(client, source_id)  # establishes the baseline

        _write_csv(path, corrupted_df)
        with _diagnosis(DIAGNOSIS_BY_CORRUPTION[name]):
            second = ingest_and_wait(client, source_id)

        run_id_by_corruption[name] = second["run_id"]
        cap = capture_run(second["run_id"])
        cap["ground_truth_expected_risk_level"] = truth.expected_risk_level
        golden[f"corruption_{name}"] = cap

        if name == "rename_column":
            # matrix-permitted, correct diagnosis, nothing else in the
            # corrupted data to defeat it - auto-fixes cleanly.
            assert second["status"] == "completed", (name, cap)
            assert any(e["action_taken"] == "auto_fixed" for e in cap["events"]), (name, cap)
        else:
            # change_dtype's own corruption defeats safe_type_cast
            # regardless of a correct-looking diagnosis; the high-risk
            # corruptions have no matrix-permitted action for their gate to
            # even attempt; inject_whitespace_case's default rate=0.5 mixes
            # THREE mangle variants (whitespace-wrap, upper, lower) at
            # base_seed=100 - a single strip_whitespace action verifiably
            # cannot resolve the case-only variants, so post-condition
            # verification correctly reverts it. All four land on
            # awaiting_approval - a real, deterministic outcome, not a bug
            # in this capture.
            assert second["status"] == "awaiting_approval", (name, cap)

    # ------------------------------------------------------------------
    # 4. Resolve the escalated corruptions through all four decisions,
    #    plus one (truncate_rows) left genuinely pending.
    # ------------------------------------------------------------------

    # drop_column -> reject_data: terminates the run, nothing downstream.
    resolve_id = _resolve_id_for_run(client, run_id_by_corruption["drop_column"])
    result = resolve_and_wait(client, resolve_id, "reject_data", "carol")
    assert result["run_status"] == "failed"
    cap = capture_run(run_id_by_corruption["drop_column"])
    assert cap["run_status"] == "failed"
    assert cap["exploration_present"] is False  # nothing downstream ran
    assert cap["report_present"] is False
    golden["corruption_drop_column_reject_data"] = cap

    # shift_distribution -> accept_as_baseline: supersedes the baseline.
    resolve_id = _resolve_id_for_run(client, run_id_by_corruption["shift_distribution"])
    resolve_and_wait(client, resolve_id, "accept_as_baseline", "dana")
    cap = capture_run(run_id_by_corruption["shift_distribution"])
    assert cap["run_status"] == "completed"
    assert all(e["action_taken"] == "accepted_as_new_baseline" for e in cap["events"])
    golden["corruption_shift_distribution_accept_as_baseline"] = cap

    # inject_nulls -> reject_fix: run proceeds, no fix applied.
    resolve_id = _resolve_id_for_run(client, run_id_by_corruption["inject_nulls"])
    resolve_and_wait(client, resolve_id, "reject_fix", "bob")
    cap = capture_run(run_id_by_corruption["inject_nulls"])
    assert cap["run_status"] == "completed"
    assert not cap["fix_chain"]
    golden["corruption_inject_nulls_reject_fix"] = cap

    # change_dtype -> approve: the SAME currency-prefix issue defeats the
    # manually-approved retry too, landing on approved_fix_failed_verification.
    resolve_id = _resolve_id_for_run(client, run_id_by_corruption["change_dtype"])
    result = resolve_and_wait(client, resolve_id, "approve", "alice")
    assert result["applied"] is False
    cap = capture_run(run_id_by_corruption["change_dtype"])
    assert cap["run_status"] == "completed"  # rejected event, but the RUN still resumes/completes
    assert any(e["action_taken"] == "approved_fix_failed_verification" for e in cap["events"])
    golden["corruption_change_dtype_approve_fails_verification"] = cap

    # truncate_rows: left genuinely pending - a real awaiting_approval state
    # to compare a future resumed-graph capture against.
    cap = capture_run(run_id_by_corruption["truncate_rows"])
    assert cap["run_status"] == "awaiting_approval"
    golden["corruption_truncate_rows_left_pending"] = cap

    # ------------------------------------------------------------------
    # 5. Low-confidence rename: escalates without attempting a fix, then a
    #    human APPROVE succeeds (the fix was always safe, just under-confident).
    # ------------------------------------------------------------------
    rename_path = tmp_path / "low_conf_rename.csv"
    _write_csv(rename_path, base_df)
    rename_source = _create_source(client, rename_path)
    ingest_and_wait(client, rename_source)
    renamed_df = base_df.rename(columns={"city": "town"})
    _write_csv(rename_path, renamed_df)
    with _diagnosis(LOW_CONFIDENCE_RENAME_DIAGNOSIS):
        low_conf = ingest_and_wait(client, rename_source)
    assert low_conf["status"] == "awaiting_approval"
    resolve_id = _resolve_id_for_run(client, low_conf["run_id"])
    result = resolve_and_wait(client, resolve_id, "approve", "alice")
    assert result["applied"] is True
    cap = capture_run(low_conf["run_id"])
    assert cap["run_status"] == "completed"
    assert cap["fix_chain"]
    golden["low_confidence_rename_then_approved"] = cap

    # ------------------------------------------------------------------
    # 6. Connector warning (multi-sheet Excel) acknowledged.
    # ------------------------------------------------------------------
    import pytest as _pytest

    _pytest.importorskip("openpyxl")
    xlsx_path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame({"x": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [3, 4]}).to_excel(writer, sheet_name="second", index=False)
    excel_source = _create_source(client, xlsx_path)
    excel_run = ingest_and_wait(client, excel_source)
    warning_id = client.get("/approvals/pending").json()["connector_warnings"][0]["id"]
    resp = client.post(f"/approvals/{warning_id}/resolve", json={"decision": "acknowledge", "resolved_by": "erin"})
    assert resp.status_code == 200
    cap = capture_run(excel_run["run_id"])
    golden["connector_warning_acknowledged"] = cap

    # ------------------------------------------------------------------
    # 7. Provisional baseline: one confirmed, one rejected.
    # ------------------------------------------------------------------
    confirm_path = tmp_path / "confirm_baseline.csv"
    _write_csv(confirm_path, _clean_df())
    confirm_source = _create_source(client, confirm_path)
    confirm_run = ingest_and_wait(client, confirm_source)
    resp = client.post(f"/approvals/{confirm_run['baseline']['id']}/resolve", json={"decision": "approve", "resolved_by": "frank"})
    assert resp.status_code == 200
    golden["provisional_baseline_confirmed"] = capture_run(confirm_run["run_id"])

    reject_path = tmp_path / "reject_baseline.csv"
    _write_csv(reject_path, _clean_df())
    reject_source = _create_source(client, reject_path)
    reject_run = ingest_and_wait(client, reject_source)
    resp = client.post(f"/approvals/{reject_run['baseline']['id']}/resolve", json={"decision": "reject_data", "resolved_by": "frank"})
    assert resp.status_code == 200
    golden["provisional_baseline_rejected"] = capture_run(reject_run["run_id"])

    # ------------------------------------------------------------------
    # 8. FAILURE PATHS (addition A) - not only success fixtures.
    # ------------------------------------------------------------------

    # 8a. Baseline-sanity rejection: a genuinely pathological first ingest
    #     (95% null column) - the run still COMPLETES (this is a caught
    #     business case, not an exception), but with no active baseline.
    pathological = pd.DataFrame({"a": [1.0, 2.0] + [None] * 98})
    pathological_path = tmp_path / "pathological.csv"
    _write_csv(pathological_path, pathological)
    pathological_source = _create_source(client, pathological_path)
    pathological_run = ingest_and_wait(client, pathological_source)
    assert pathological_run["status"] == "completed"
    assert pathological_run["baseline"] is None
    assert pathological_run["baseline_rejected_reasons"]
    golden["baseline_sanity_rejection"] = capture_run(pathological_run["run_id"])

    # 8b. Connector raises: a source pointing at a file that doesn't exist.
    #     The top-level except in the background graph runner (or the
    #     graph's own degraded path) must still leave the run at a TERMINAL
    #     status ("failed"), never stuck at "running". DELIBERATE CHANGE
    #     from the pre-fix assertion (failed_resp.status_code == 500): POST
    #     /ingest can no longer know synchronously that the graph is about
    #     to fail - it returns 200 + {run_id, status: "running"} for EVERY
    #     valid source_id now, and the graph runs in the background (see
    #     app/routers/ingest.py's module docstring for why: this is the fix
    #     for a real hang, not a stylistic change). The property this
    #     scenario exists to verify - a connector failure ends at "failed",
    #     never stuck "running" - is unchanged and still checked, just
    #     observed by polling to the terminal state instead of an
    #     immediate HTTP status code that no longer applies.
    missing_source = _create_source(client, tmp_path / "does_not_exist.csv")
    failed_result = ingest_and_wait(client, missing_source)
    assert failed_result["status"] == "failed"
    failed_run_id = failed_result["run_id"]
    golden["connector_fetch_exception"] = capture_run(failed_run_id)
    assert golden["connector_fetch_exception"]["run_status"] == "failed"

    # Sanity: every captured scenario ended in a TERMINAL status - "running"
    # must never survive to the end of any scenario above (addition B's
    # concern).
    for key, value in golden.items():
        if not isinstance(value, dict) or "run_status" not in value:
            continue
        assert value["run_status"] in ("completed", "failed", "awaiting_approval"), (key, value["run_status"])

    return golden
