"""The Marketing Agent's HTTP surface.

    GET /marketing/{run_id}   findings, key values, and the refusal reason

Accepts a run NUMBER or a full UUID via the one shared matcher every other
run-taking endpoint uses (app/id_lookup.py), so this screen can never drift
from Reports/Audit/Analytics on what identifies a run.

A run that did not qualify returns 200 with `applicable: false` and the
missing roles named - NOT a 404. "This source is not an ads export, and here
is the role it was missing" is an answer; a 404 would make a refusal
indistinguishable from a run that was never analysed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.id_lookup import resolve_run
from app.models import MarketingAnalysis

router = APIRouter(prefix="/marketing", tags=["marketing"])


@router.get("/{run_id}")
def get_marketing(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    record = db.query(MarketingAnalysis).filter(MarketingAnalysis.run_id == run.id).one_or_none()
    if record is None:
        # Distinct from a refusal: the run exists but the agent never ran for
        # it - an older run, or one that never reached completed.
        raise HTTPException(
            status_code=404,
            detail=(
                f"no marketing analysis for run {run.run_number or run.id} - "
                "the Marketing Agent runs once, after business analytics, on completed runs only"
            ),
        )

    payload = dict(record.findings_json)
    payload["run_number"] = run.run_number
    payload["schema_version"] = record.schema_version
    payload["generated_at"] = record.generated_at.isoformat() if record.generated_at else None
    return payload
