from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.baseline_sanity import BaselineSanityError, assert_baseline_sane
from app.connectors.factory import build_connector
from app.db import get_db
from app.models import Baseline, DataSource
from app.profiling import BaselineProfiler

router = APIRouter(prefix="/sources", tags=["baselines"])


@router.post("/{source_id}/baseline")
def recompute_baseline(source_id: str, db: Session = Depends(get_db)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")

    try:
        connector = build_connector(source)
        contract = connector.fetch()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    profile = BaselineProfiler().profile(contract.data)

    try:
        assert_baseline_sane(profile)
    except BaselineSanityError as sanity_error:
        # Leave the existing active baseline (if any) untouched - a bad
        # recompute must not be allowed to clobber a good baseline.
        raise HTTPException(
            status_code=422,
            detail={"message": "baseline failed sanity floors", "reasons": sanity_error.reasons},
        ) from sanity_error

    db.query(Baseline).filter(
        Baseline.source_id == source.id, Baseline.is_active.is_(True)
    ).update({"is_active": False})

    # An explicit recompute is a deliberate human action, unlike the automatic
    # first-ingest baseline, so it's authoritative immediately (not provisional).
    baseline = Baseline(source_id=source.id, profile_json=profile, is_active=True, is_provisional=False)
    db.add(baseline)
    db.commit()
    db.refresh(baseline)

    return {
        "id": baseline.id,
        "source_id": source.id,
        "row_count": profile["row_count"],
        "is_provisional": baseline.is_provisional,
        "created_at": baseline.created_at.isoformat(),
    }
