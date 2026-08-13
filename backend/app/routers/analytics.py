"""The Business Analytics Agent's HTTP surface.

    GET    /analytics/{run_id}                      results + applicability
    POST   /analytics/{run_id}/confirmed-roles      confirm a column role
    DELETE /analytics/{run_id}/confirmed-roles/{role}   withdraw one

The two write endpoints exist because detection deliberately refuses to
promote a low-confidence candidate on its own. Surfacing a candidate a
person can never act on would make that refusal a dead end rather than a
question.

Accepts a run NUMBER or a full UUID via the one shared matcher every other
run-taking endpoint uses (app/id_lookup.py), so this screen can never drift
from Reports/Audit on what identifies a run.

Returns the applicability report alongside the results. A 200 with several
`ran: false` entries is the normal, informative case - not an error - because
"this analysis was never eligible, and here is the requirement it needed" is
the answer to the most common question a user has on this page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analytics.chart_specs import charts_for_results
from app.analytics.pipeline import run_business_analytics_for_run
from app.analytics.roles import ColumnRole
from app.connectors.factory import build_connector
from app.db import get_db
from app.id_lookup import resolve_run
from app.models import Baseline, BusinessAnalysis, ConfirmedColumnRole, DataSource
from app.repair import repaired_contract_for_run

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ConfirmRoleRequest(BaseModel):
    """A human's answer to "which column fills this role?".

    Both fields are validated against reality before anything is stored:
    the role against the closed ColumnRole set, the column against the
    columns this run's repaired frame actually has. A confirmation is a
    fact the system will then trust over its own inference, so accepting an
    unverifiable one would be worse than asking again.
    """

    role: str
    column: str


def _repaired_frame(db: Session, run):
    """The same repaired frame every other phase reads - never a fresh,
    unrepaired fetch (app/repair.py)."""
    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    connector = build_connector(source)
    return repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None).data


def _payload(record: BusinessAnalysis, run) -> dict:
    """One response shape for all three endpoints. A confirmation returns
    the SAME document a GET would, so the page re-renders from the response
    it already knows how to read instead of round-tripping."""
    payload = dict(record.findings_json)
    # Rows written before chart specs were persisted carry findings but no
    # `charts`. Derived on read from those same findings by the SAME function
    # that persists them, so an older run gets the identical figures rather
    # than a blank space or a second code path that could drift.
    if not payload.get("charts"):
        payload["charts"] = charts_for_results(payload.get("results") or [])
    payload["run_number"] = run.run_number
    payload["schema_version"] = record.schema_version
    payload["generated_at"] = record.generated_at.isoformat() if record.generated_at else None
    return payload


@router.get("/{run_id}")
def get_analytics(run_id: str, db: Session = Depends(get_db)):
    run = resolve_run(db, run_id)
    record = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if record is None:
        # Distinct from "no such run": the run exists, analytics simply has
        # not been produced for it (an older run, or one that never reached
        # completed).
        raise HTTPException(
            status_code=404,
            detail=f"no business analytics for run {run.run_number or run.id} - analytics run once, after exploration, on completed runs only",
        )

    return _payload(record, run)


@router.post("/{run_id}/confirmed-roles")
def confirm_role(run_id: str, body: ConfirmRoleRequest, db: Session = Depends(get_db)):
    """Record that a human confirmed `column` fills `role`, then recompute.

    Stored against the run's SOURCE, so a later ingest of the same source
    inherits the answer and the question is never asked twice.

    Recomputing here rather than leaving the stored results stale is the
    whole point of the action: a user confirms a role in order to see the
    analyses it unlocks, and a confirmation that required a manual
    re-ingest to take effect would not be worth making.
    """
    run = resolve_run(db, run_id)

    try:
        role = ColumnRole(body.role)
    except ValueError:
        known = ", ".join(r.value for r in ColumnRole)
        raise HTTPException(status_code=400, detail=f"unknown role `{body.role}` - expected one of: {known}")

    frame = _repaired_frame(db, run)
    columns = [str(c) for c in frame.columns]
    if body.column not in columns:
        raise HTTPException(
            status_code=400,
            detail=f"run {run.run_number or run.id} has no column named `{body.column}` - available: {', '.join(columns)}",
        )

    # One confirmation per (source, role): confirming again replaces the
    # previous answer rather than accumulating conflicting ones.
    existing = (
        db.query(ConfirmedColumnRole)
        .filter(ConfirmedColumnRole.source_id == run.source_id, ConfirmedColumnRole.role == role.value)
        .one_or_none()
    )
    if existing is not None:
        existing.column_name = body.column
    else:
        db.add(ConfirmedColumnRole(source_id=run.source_id, role=role.value, column_name=body.column))
    db.flush()

    record = run_business_analytics_for_run(db, run, frame, replace=True)
    db.commit()
    db.refresh(record)

    return _payload(record, run)


@router.delete("/{run_id}/confirmed-roles/{role}")
def clear_confirmed_role(run_id: str, role: str, db: Session = Depends(get_db)):
    """Withdraw a confirmation and recompute without it.

    Present so confirming is not a one-way door: a person who confirms the
    wrong column must be able to take it back, and a stale confirmation
    naming a column a re-ingest removed must be clearable. Detection alone
    decides the role again afterwards.
    """
    run = resolve_run(db, run_id)
    existing = (
        db.query(ConfirmedColumnRole)
        .filter(ConfirmedColumnRole.source_id == run.source_id, ConfirmedColumnRole.role == role)
        .one_or_none()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=f"no confirmed `{role}` role for this run's source")
    db.delete(existing)
    db.flush()

    record = run_business_analytics_for_run(db, run, _repaired_frame(db, run), replace=True)
    db.commit()
    db.refresh(record)

    return _payload(record, run)
