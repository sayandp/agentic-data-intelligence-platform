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
        raise HTTPException(
            status_code=404,
            detail=(
                f"no report for run '{run.id}' (status={run.status!r}) - the Narrative Agent only runs once a run "
                "reaches 'completed'; a run still awaiting_approval or failed has none"
            ),
        )

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
