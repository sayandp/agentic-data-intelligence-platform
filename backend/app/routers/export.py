"""POST /export/{run_id}/pptx  -> start generation (returns immediately)
GET  /export/{run_id}/pptx/status -> poll
GET  /export/{run_id}/pptx        -> download the file

Async with polling rather than one long request, the same shape POST
/ingest and POST /predict already use: rendering every chart through
kaleido drives a real browser and takes seconds, and holding a connection
open for that is the exact bug those two endpoints were changed to avoid.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.export.pipeline import deck_path, job_status, start_export
from app.id_lookup import resolve_run

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/{run_id}/pptx")
def start_pptx_export(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    status = start_export(db, run)
    return {"run_id": run.id, "run_number": run.run_number, **status}


@router.get("/{run_id}/pptx/status")
def pptx_export_status(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    key = str(run.run_number or run.id)
    status = job_status(key)
    # A deck already on disk from an earlier session is ready even though
    # this process has no job for it - the file is the durable artifact,
    # the job is only the progress of one render.
    if status.get("state") in ("none", None) and deck_path(run).exists():
        return {"run_id": run.id, "run_number": run.run_number, "state": "ready", "bytes": deck_path(run).stat().st_size}
    return {"run_id": run.id, "run_number": run.run_number, **status}


@router.get("/{run_id}/pptx")
def download_pptx(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    path = deck_path(run)
    if not path.exists():
        state = job_status(str(run.run_number or run.id)).get("state")
        detail = (
            "the deck for this run is still being generated - poll GET /export/{run}/pptx/status"
            if state == "running"
            else "no deck has been generated for this run yet - POST /export/{run}/pptx to build one"
        )
        raise HTTPException(status_code=404, detail=detail)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"run-{run.run_number or run.id}.pptx",
    )
