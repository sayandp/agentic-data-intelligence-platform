"""GET /runs - the run picker's backing list.

Added because Ask/Predict asked a human to TYPE a run identifier, which is
what let "run '48' not found" happen against a run that existed (the
run_number lookup was wired into Reports/Audit but not into the shared
Ask/Predict resolver - see app/query/pipeline.py::resolve_run). A picker
needs "every recent completed run, newest first, labelled well enough to
recognise" across ALL sources, and the only run listing before this was
per-source (GET /sources/{source_id}/runs), which would have meant one
request per source to fill a single dropdown.

Deliberately cheap: every field here is a plain column read plus one
already-loaded DataSource. It does NOT compute `metadata`/columns the way
GET /ingest/{run_id}/status does - that rebuilds the repaired frame
(app/repair.py::repaired_contract_for_run reads the source and replays the
fix chain), which is fine for one run on demand and far too expensive per
row of a list. Columns for the ONE run a user actually selects come from
that existing endpoint.
"""

from __future__ import annotations

import posixpath
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DataSource, Run

router = APIRouter(tags=["runs"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def source_label(source: DataSource | None) -> str:
    """Something a human recognises in a dropdown. Prefers the name they
    uploaded, then the filename part of a configured path, then the source
    type - never a bare UUID, which is exactly what the picker exists to
    stop people copying around."""
    if source is None:
        return "unknown source"

    config = source.connection_config or {}
    original = config.get("original_filename")
    if original:
        return str(original)

    path = config.get("path")
    if path:
        # Normalize both separators before taking the basename - configured
        # paths in this project are written Windows-style ("D:/...") and
        # POSIX-style interchangeably.
        return posixpath.basename(str(path).replace("\\", "/")) or str(path)

    return str(source.type)


@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    status: Literal["completed", "all"] = "completed",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    """Recent runs, newest first. Defaults to `completed` because that is
    the only state Ask/Predict can actually answer against - offering a
    running or failed run in the picker would be offering a choice that
    always fails."""
    query = db.query(Run)
    if status == "completed":
        query = query.filter(Run.status == "completed")

    # completed_at is null for a run that never finished (status=all), so
    # started_at carries the ordering for those rather than dropping them to
    # the bottom arbitrarily.
    runs = query.order_by(Run.completed_at.desc().nullslast(), Run.started_at.desc()).limit(limit).all()

    source_ids = {r.source_id for r in runs}
    sources = {s.id: s for s in db.query(DataSource).filter(DataSource.id.in_(source_ids)).all()} if source_ids else {}

    return [
        {
            "id": r.id,
            "run_number": r.run_number,
            "status": r.status,
            "source_id": r.source_id,
            "source_label": source_label(sources.get(r.source_id)),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]
