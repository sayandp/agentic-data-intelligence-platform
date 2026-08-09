from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import Report

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
            detail = (
                f"the report for run {run.run_number or run.id} is still being written. Exploration has finished "
                "(which is what marks a run completed), and the Narrative Agent runs after that - reload in a moment. "
                "If it never appears, the Audit view shows whether the narrative step ran and what it returned."
            )
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
