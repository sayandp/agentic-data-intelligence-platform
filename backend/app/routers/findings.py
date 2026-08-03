from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ExplorationFinding, Run

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("/{run_id}")
def get_findings(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one_or_none()
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no findings for run '{run_id}' (status={run.status!r}) - exploration only runs once a run "
                "reaches 'completed'; a run still awaiting_approval or failed has none"
            ),
        )

    return {
        "id": record.id,
        "run_id": record.run_id,
        "schema_version": record.schema_version,
        "generated_at": record.generated_at.isoformat(),
        "findings": record.findings_json,
    }
