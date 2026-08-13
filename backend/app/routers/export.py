"""POST /export/{run_id}/pptx  -> start generation (returns immediately)
GET  /export/{run_id}/pptx/status -> poll
GET  /export/{run_id}/pptx        -> download the file

Async with polling rather than one long request, the same shape POST
/ingest and POST /predict already use: rendering every chart through
kaleido drives a real browser and takes seconds, and holding a connection
open for that is the exact bug those two endpoints were changed to avoid.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.export.deck import deck_outline
from app.export.pipeline import collect_sources, deck_path, job_status, start_export
from app.id_lookup import resolve_run

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/{run_id}/pptx")
def start_pptx_export(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    status = start_export(db, run)
    return {"run_id": run.id, "run_number": run.run_number, **status}


@router.get("/{run_id}/pptx/status")
def pptx_export_status(run_id: str, db: Session = Depends(get_db)):
    """Reports the FILE first, the job second.

    A deck a user generated an hour ago is still theirs to download; the
    in-process job only ever describes the progress of one render and does
    not survive a restart. Reading the job alone is what made a finished
    deck look like it had never existed the moment someone navigated away.
    """
    run = resolve_run(db, run_id)
    key = str(run.run_number or run.id)
    status = job_status(key)
    path = deck_path(run)

    existing = None
    if path.exists():
        stat = path.stat()
        existing = {
            "bytes": stat.st_size,
            "generated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }

    # A render in flight is the more current fact; otherwise the file on
    # disk is, whether or not this process remembers making it.
    if status.get("state") == "running":
        return {"run_id": run.id, "run_number": run.run_number, **status, "existing": existing}
    if existing:
        return {"run_id": run.id, "run_number": run.run_number, "state": "ready", **existing, "existing": existing}
    return {"run_id": run.id, "run_number": run.run_number, **status, "existing": None}


@router.get("/{run_id}/pptx/contents")
def pptx_export_contents(run_id: str, db: Session = Depends(get_db)):
    """What the deck WILL contain, without building it.

    So the export screen can say which sections carry this run's results and
    which will carry the sentence explaining why they do not - before
    someone waits through a render to find out.
    """
    run = resolve_run(db, run_id)
    sources = collect_sources(db, run)
    return {
        "run_id": run.id,
        "run_number": run.run_number,
        "source_label": sources.source_label,
        "sections": deck_outline(sources),
    }


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
