"""GET /analytics/{run_id} - the Business Analytics Agent's HTTP surface.

Accepts a run NUMBER or a full UUID via the one shared matcher every other
run-taking endpoint uses (app/id_lookup.py), so this screen can never drift
from Reports/Audit on what identifies a run.

Returns the applicability report alongside the results. A 200 with several
`ran: false` entries is the normal, informative case - not an error - because
"this analysis was never eligible, and here is the requirement it needed" is
the answer to the most common question a user has on this page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import BusinessAnalysis

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/{run_id}")
def get_analytics(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    record = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if record is None:
        # Distinct from "no such run": the run exists, analytics simply has
        # not been produced for it (an older run, or one that never reached
        # completed).
        raise HTTPException(
            status_code=404,
            detail=f"no business analytics for run {run.run_number or run.id} - analytics run once, after exploration, on completed runs only",
        )

    payload = dict(record.findings_json)
    payload["run_number"] = run.run_number
    payload["schema_version"] = record.schema_version
    payload["generated_at"] = record.generated_at.isoformat() if record.generated_at else None
    return payload
