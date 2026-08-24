"""GET /compare?run_a=&run_b= - two runs of one source, side by side.

Computed on read. See app/comparison/engine.py for why nothing is persisted.

Both parameters accept a run NUMBER or a full UUID, through the same shared
matcher every other run-taking endpoint uses (app/id_lookup.py), so this
screen cannot drift from Reports and Audit on what identifies a run.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.comparison.engine import compare_runs
from app.db import get_db
from app.id_lookup import resolve_run
from app.models import Run

router = APIRouter(prefix="/compare", tags=["compare"])


@router.get("")
def compare(
    run_a: str | None = Query(default=None),
    run_b: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """With both runs named, compares them. With only `source_id`, defaults to
    that source's two most recent completed runs - the comparison a person
    almost always wants, without making them look up two ids first.

    A source with fewer than two completed runs is a 400 naming how many it
    has, not an empty comparison: "nothing changed" and "there is nothing to
    compare" are different answers and must not render the same.
    """
    if run_a and run_b:
        first, second = resolve_run(db, run_a), resolve_run(db, run_b)
    elif source_id:
        recent = (
            db.query(Run)
            .filter(Run.source_id == source_id, Run.status == "completed")
            .order_by(Run.started_at.desc())
            .limit(2)
            .all()
        )
        if len(recent) < 2:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"source {source_id} has {len(recent)} completed run(s); a comparison needs two. "
                    "Ingest it again to produce a second run."
                ),
            )
        second, first = recent[0], recent[1]  # newest is B, previous is A
    else:
        raise HTTPException(status_code=400, detail="provide either run_a and run_b, or source_id")

    # Ordered oldest-first regardless of how they were named, so "before" and
    # "after" always mean what a reader expects and the same pair never
    # renders two different sets of signs depending on argument order.
    if first.started_at and second.started_at and first.started_at > second.started_at:
        first, second = second, first

    return compare_runs(db, first, second).to_dict()
