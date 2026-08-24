from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.connectors.file_connector import EXCEL_EXTENSIONS
from app.db import get_db
from app.models import DataSource, Run
from app.security.config import MAX_UPLOAD_BYTES
from app.security.file_type import SNIFF_BYTES, detect_mismatch

router = APIRouter(prefix="/sources", tags=["sources"])

VALID_SOURCE_TYPES = {"sql", "file", "api"}

# Same extensions app/connectors/file_connector.py::FileConnector.fetch()
# already accepts - imported, not duplicated, so the two can't drift.
ALLOWED_UPLOAD_EXTENSIONS = {".csv", *EXCEL_EXTENSIONS}
DEFAULT_UPLOAD_DIR = "data/uploads"


def _upload_dir() -> Path:
    # data/uploads locally; docker-compose.yml points this at /data/uploads,
    # inside the SAME volume-mounted /data the README's manual-path flow
    # already uses, so an uploaded file is visible on the host too, not
    # trapped inside the container.
    return Path(os.environ.get("UPLOAD_DIR", DEFAULT_UPLOAD_DIR))


class SourceCreate(BaseModel):
    type: str
    connection_config: dict


def _create_source(db: Session, source_type: str, connection_config: dict) -> DataSource:
    source = DataSource(type=source_type, connection_config=connection_config)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.post("")
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    if payload.type not in VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of {sorted(VALID_SOURCE_TYPES)}",
        )

    source = _create_source(db, payload.type, payload.connection_config)
    return {"id": source.id}


@router.post("/upload")
async def upload_source(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """A `file`-type source whose data comes from the dashboard's Upload
    button instead of a path the app process already had filesystem access
    to - the same registration a hand-written {"path": ...} connection_config
    produces, just without requiring the browser and the app process to
    share a filesystem first.

    The client's filename is untrusted: only its extension is read (against
    the same allowlist FileConnector itself enforces), and only its
    basename - never a directory component - is used to build the stored
    path, so a crafted filename (`../../etc/passwd`, an absolute path) can't
    write outside the upload directory.
    """
    original_name = Path(file.filename or "").name  # basename only - strips any directory component
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file extension {extension!r}; expected one of {sorted(ALLOWED_UPLOAD_EXTENSIONS)}",
        )

    upload_dir = _upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid.uuid4()}{extension}"

    size = 0
    head = b""
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
            if len(head) < SNIFF_BYTES:
                head += chunk[: SNIFF_BYTES - len(head)]
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    # The extension is a claim; the bytes are the evidence. Checked AFTER the
    # write so the size cap still bounds what a hostile client can make this
    # process buffer, and the file is removed on refusal so a rejected upload
    # leaves nothing behind.
    mismatch = detect_mismatch(head, extension)
    if mismatch is not None:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=mismatch.message())

    source = _create_source(db, "file", {"path": str(dest), "original_filename": original_name})
    return {"id": source.id}


def _serialize(source: DataSource) -> dict:
    # connection_config is returned as-is, never redacted here - the
    # guarantee is structural (credentials.py only ever stores an env var
    # NAME in connection_config, never a resolved secret value), not a
    # filter applied at read time. See tests/test_connector_credentials.py.
    return {
        "id": source.id,
        "type": source.type,
        "connection_config": source.connection_config,
        "created_at": source.created_at.isoformat(),
    }


@router.get("")
def list_sources(db: Session = Depends(get_db)):
    sources = db.query(DataSource).order_by(DataSource.created_at).all()
    return [_serialize(s) for s in sources]


@router.get("/{source_id}")
def get_source(source_id: str, db: Session = Depends(get_db)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return _serialize(source)


@router.get("/{source_id}/runs")
def list_source_runs(source_id: str, db: Session = Depends(get_db)):
    """Phase 8 Part 3: the Sources dashboard screen's "history" - every run
    ever started against this source, newest first. No new state, just a
    read the dashboard needed that nothing before it required."""
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    runs = db.query(Run).filter(Run.source_id == source_id).order_by(Run.started_at.desc()).all()
    return [
        {
            "id": r.id,
            "run_number": r.run_number,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]
