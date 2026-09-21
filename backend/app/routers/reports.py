from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import AgentTrace, ExplorationFinding, Report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{run_id}")
def get_report(run_id: str, db: Session = Depends(get_db)):
    """run_id accepts a plain run_number ("17"/"#17") as well as the full
    UUID - see app/id_lookup.py::resolve_run. A number match is always
    exact (run_number is unique), so unlike the id itself there is no
    ambiguity path to handle. Everything below keys off run.id (the real
    UUID resolve_run found), never the raw path parameter."""
    run = resolve_run(db, run_id)

    record = db.query(Report).filter(Report.run_id == run.id).one_or_none()
    if record is None:
        # A COMPLETED run with no report is a different situation from an
        # incomplete one, and saying the same thing for both is worse than
        # saying nothing: app/graph/nodes.py::explore_node marks the run
        # "completed" BEFORE narrate_node runs, so there is a real window
        # where the run is completed and the report genuinely does not exist
        # yet. The old wording ("the Narrative Agent only runs once a run
        # reaches 'completed'") told a reader to wait for a state the run
        # was already in - it read as a contradiction and gave them nothing
        # to do.
        if run.status == "completed":
            detail = _completed_without_report(db, run)
        else:
            detail = (
                f"run {run.run_number or run.id} is {run.status!r}, so it has no report - the Narrative Agent only "
                "runs after a run completes. "
                + (
                    "Resolve what it is waiting on and the run continues from where it stopped."
                    if run.status == "awaiting_approval"
                    else "The Audit view shows which node it stopped at and why."
                )
            )
        raise HTTPException(status_code=404, detail=detail)

    return {
        "id": record.id,
        "run_id": record.run_id,
        "run_number": run.run_number,
        "generation_mode": record.generation_mode,
        "narrative_text": record.narrative_text,
        "chart_refs": record.chart_refs,
        "grounded_claims": record.grounded_claims_json,
        "post_check_results": record.post_check_results,
        "delivered_at": record.delivered_at.isoformat() if record.delivered_at else None,
    }


def _elapsed(since: datetime | None) -> str:
    if since is None:
        return "an unknown time"
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - since).total_seconds()))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _completed_without_report(db: Session, run) -> str:
    """What is actually known about a completed run that has no report.

    The previous message said "Exploration has finished (which is what marks a
    run completed)" - but that was inferred from the status, and the status
    does not mean that. explore_node sets "completed" at its START, before
    role detection, exploration or analytics run, because in this codebase
    "completed" means the run passed validation and its data is usable (Ask
    and Predict accept it from that moment). A run could therefore sit
    "completed" with no exploration at all, and be told "reload in a moment".
    Run 1, a 1,067,371-row retail export, was: it sat "completed" for 16
    minutes before its report was written, 9 of them before exploration had
    even finished, while role detection re-parsed every text column once per
    scorer.

    So this reads the ROWS rather than the status. Whether exploration
    finished is whether its findings were written; when is their own
    timestamp. Nothing here is inferred from "completed".

    It also stops pointing at the Audit view for something the Audit view
    cannot show. The explore and narrate nodes write no trace row of their
    own; the only rows are the ones exploration and the narrative write when
    they SUCCEED. For a run stuck inside exploration the Audit view shows
    nothing after ingestion, so sending a reader there to "see whether the
    narrative step ran" sent them to an empty page.
    """
    label = run.run_number or run.id
    exploration = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()

    # Nothing resumes an interrupted run: the app's startup does not re-enter
    # the graph for runs left mid-way, so a run cut off by a restart stays in
    # this state. That is worth saying, because it is the one case where
    # waiting longer can never help.
    restart_note = (
        "Nothing resumes an interrupted run, so if the backend was restarted since it started, it will not "
        "finish on its own - re-ingest the source to produce a report."
    )

    if exploration is None:
        return (
            f"run {label} has no report yet because its analysis has not finished. The run passed validation, "
            f"which is what 'completed' means here, {_elapsed(run.completed_at)} ago - but no exploration findings "
            "have been written, so the report cannot have started: exploration, business analytics and the "
            "domain packs all run first, and they take longer on large files. " + restart_note
        )

    # The narrate node itself can die. safe_node then records a trace row
    # under the NODE's name with edge "failed" and the error - and the run
    # stays "completed", so without this branch the reader would be told the
    # report is on its way when the step that writes it has already ended.
    # Found by the E2E run for this fix: two narrate nodes killed by a dropped
    # connection to the model provider.
    failed = (
        db.query(AgentTrace)
        .filter(AgentTrace.run_id == run.id, AgentTrace.agent_name == "narrate", AgentTrace.edge_taken == "failed")
        .order_by(AgentTrace.timestamp.desc())
        .first()
    )
    if failed is not None:
        try:
            error = json.loads(failed.output_summary or "{}")
            cause = f"{error.get('error_type', 'error')}: {error.get('error', '')}".strip(": ")
        except ValueError:
            cause = failed.output_summary or "no detail recorded"
        return (
            f"run {label} has no report, and will not get one on its own: the step that writes it failed "
            f"{_elapsed(failed.timestamp)} ago ({cause}). Exploration finished before that and its findings are "
            "intact. Re-ingest the source to produce a report; the Audit view shows this failure as a 'narrate' "
            "step."
        )

    return (
        f"run {label} has no report yet. Exploration finished {_elapsed(exploration.generated_at)} ago, and the "
        "Narrative Agent writes the report after it - with a language model configured that includes model "
        "calls, which can be slowed by rate limits. The Audit view has the exploration step; a narrative row "
        "appears there when the report is written. " + restart_note
    )
