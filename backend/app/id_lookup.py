"""Dashboard UX pass, Part 1: human-friendly run lookup for the Reports/Audit
"paste an id" boxes. Supersedes an earlier unique-id-PREFIX scheme (which
could be ambiguous and needed a 409-plus-candidate-picker to handle it) with
a much simpler mechanism: Run.run_number, a short sequential integer assigned
once at creation (app/routers/ingest.py::next_run_number) and enforced
UNIQUE at the database level (app/db.py::init_db). A number match is always
exact - there is no prefix-style ambiguity left to handle, and therefore no
409 path either. Full UUIDs stay the only real primary key anywhere; this is
purely an alternate, friendlier way to ASK for a row a caller already has by
either name.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Run


def find_run(db: Session, run_id_or_number: str) -> Run | None:
    """The single definition of "what string identifies a run" - the full
    UUID (exact primary-key path) or a plain run_number, optionally
    "#"-prefixed ("17" or "#17"), never a prefix of either.

    Returns None rather than raising, so callers that are not HTTP handlers
    (app/query/pipeline.py::resolve_run, which raises ValueError and is
    shared by /ask and /predict) can apply these exact same rules without
    importing HTTP semantics into a domain layer. Every page that takes a
    run identifier resolves it through this function - Reports, Audit,
    ingest status, Ask and Predict - so a run number typed into one of them
    can never mean something different in another.
    """
    run = db.get(Run, run_id_or_number)
    if run is not None:
        return run

    candidate = run_id_or_number.strip()
    if candidate.startswith("#"):
        candidate = candidate[1:]
    if candidate.isdigit():
        return db.query(Run).filter(Run.run_number == int(candidate)).one_or_none()

    return None


def resolve_run(db: Session, run_id_or_number: str) -> Run:
    """Returns the matching Run, or raises HTTPException(404). Accepts
    exactly what find_run accepts."""
    run = find_run(db, run_id_or_number)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run
