"""The privacy layer's HTTP surface.

    GET    /privacy/{run_id}                    what is classified, and what leaves
    POST   /privacy/{run_id}/decisions          mark a column personal / not personal
    DELETE /privacy/{run_id}/decisions/{column} withdraw a decision

The write endpoints are the human half of default-deny. Detection refuses to
auto-classify a person name or a free-text column, so those are masked on the
strict paths until somebody says otherwise - and that "otherwise" needs a
door. A candidate a person can never clear would make over-redaction
permanent, which is how a privacy layer ends up quietly destroying the
reports it was added to protect.

Decisions are stored against the SOURCE, so a re-ingest inherits them and the
question is asked once. The run's stored classification is recomputed here
rather than left stale: the decision changes what leaves the machine on the
next outbound call, and a decision that needed a re-ingest to take effect
would not be worth making.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.connectors.factory import build_connector
from app.db import get_db
from app.id_lookup import resolve_run
from app.models import Baseline, DataSource
from app.privacy.classification import PIIConfidence, PIIKind, classify_frame
from app.privacy.confirmations import (
    NOT_PERSONAL,
    clear_privacy_decision,
    privacy_decisions_for_source,
    record_privacy_decision,
)
from app.repair import repaired_contract_for_run

router = APIRouter(prefix="/privacy", tags=["privacy"])


class PrivacyDecisionRequest(BaseModel):
    """A person's answer about one column.

    `decision` is either "not_personal" or a PIIKind value. `marked_by` is
    free text and is stored and displayed as an ATTRIBUTION, not an identity -
    this system has no authentication, and labelling it otherwise would be a
    claim the code cannot back.
    """

    column: str
    decision: str
    marked_by: str | None = None


def _repaired_frame(db: Session, run):
    """The same repaired frame classification ran against originally - never a
    fresh, unrepaired fetch."""
    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    connector = build_connector(source)
    return repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None).data


def _reclassify(db: Session, run) -> dict:
    frame = _repaired_frame(db, run)
    classification = classify_frame(frame, confirmed=privacy_decisions_for_source(db, run.source_id))
    run.privacy_classification = classification.to_dict()
    return run.privacy_classification


@router.get("/{run_id}")
def get_privacy(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    if not run.privacy_classification:
        raise HTTPException(
            status_code=404,
            detail=(
                f"run {run.run_number or run.id} has no privacy classification - "
                "classification happens once, when a run completes"
            ),
        )
    return run.privacy_classification


@router.post("/{run_id}/decisions")
def record_decision(run_id: str, body: PrivacyDecisionRequest, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)

    valid = {NOT_PERSONAL, *(k.value for k in PIIKind)}
    if body.decision not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"unknown decision `{body.decision}` - expected one of: {', '.join(sorted(valid))}",
        )

    frame = _repaired_frame(db, run)
    columns = [str(c) for c in frame.columns]
    if body.column not in columns:
        raise HTTPException(
            status_code=400,
            detail=f"run {run.run_number or run.id} has no column named `{body.column}` - available: {', '.join(columns)}",
        )

    # Clearing a column detection VERIFIED as personal is refused, not
    # honoured. The confirm-a-role flow exists to resolve what the machine
    # cannot determine; a Luhn-valid card column is not that. Making this a
    # 400 rather than a stored-then-overridden decision means the person is
    # told their action had no effect, instead of believing it did.
    if body.decision == NOT_PERSONAL:
        current = {c["column"]: c for c in (run.privacy_classification or {}).get("classifications", [])}
        entry = current.get(body.column)
        if entry and entry.get("confidence") == PIIConfidence.HIGH.value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"`{body.column}` was detected as {entry.get('kind')} in "
                    f"{entry.get('matched_fraction')} of sampled values - verified personal data "
                    "cannot be marked not personal. Only unconfirmed candidates can be cleared."
                ),
            )

    record_privacy_decision(
        db,
        source_id=run.source_id,
        column=body.column,
        decision=body.decision,
        confirmed_by=(body.marked_by or "").strip() or None,
    )
    db.flush()

    payload = _reclassify(db, run)
    db.commit()
    return payload


@router.delete("/{run_id}/decisions/{column}")
def clear_decision(run_id: str, column: str, db: Session = Depends(get_db)):
    """Withdraw a decision, returning the column to whatever detection says.

    Present because marking a column not personal is the one action here that
    REMOVES protection, and an action like that must not be a one-way door.
    """
    run = resolve_run(db, run_id)
    if not clear_privacy_decision(db, run.source_id, column):
        raise HTTPException(status_code=404, detail=f"no privacy decision for `{column}` on this run's source")
    db.flush()

    payload = _reclassify(db, run)
    db.commit()
    return payload
