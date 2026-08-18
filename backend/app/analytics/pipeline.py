"""Persists the Business Analytics Agent's output for one run.

Idempotent, and shaped exactly like app/exploration/pipeline.py: a run that
already has a row is left alone rather than recomputed, and an AgentTrace is
written like every other agent so the Audit screen shows this agent's
decision alongside the rest.

Runs AFTER exploration, on the REPAIRED frame, only for completed runs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.chart_specs import charts_for_results
from app.analytics.engine import run_business_analytics
from app.models import AgentTrace, BusinessAnalysis, ConfirmedColumnRole, Run
from app.privacy.confirmations import PII_ROLE_PREFIX


def confirmed_roles_for_source(db: Session, source_id: str) -> dict[str, str]:
    """The roles a human confirmed for this SOURCE, as {role: column}.

    Source-scoped is what makes a re-ingest not re-ask: the second run over
    the same file picks these up on its own, with no user action, because
    the confirmation describes the source's shape rather than one pass over
    it.
    """
    # Privacy decisions share this table but are NOT semantic roles. They are
    # excluded here explicitly rather than left to be ignored downstream: an
    # unknown key that happens to be harmless today is a coincidence, not a
    # design.
    rows = (
        db.query(ConfirmedColumnRole)
        .filter(
            ConfirmedColumnRole.source_id == source_id,
            ~ConfirmedColumnRole.role.startswith(PII_ROLE_PREFIX),
        )
        .all()
    )
    return {row.role: row.column_name for row in rows}


def run_business_analytics_for_run(
    db: Session,
    run: Run,
    repaired_df: pd.DataFrame,
    replace: bool = False,
) -> BusinessAnalysis | None:
    """Idempotent by default. `replace=True` is the one path that recomputes
    an existing row - used when a human confirms a column role, which is
    precisely the case where the previous answer is known to be stale."""
    existing = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if existing is not None and not replace:
        return existing

    confirmed = confirmed_roles_for_source(db, run.source_id)
    findings = run_business_analytics(run.id, repaired_df, confirmed)

    # Charts are derived from the SERIALISED findings, not the model objects -
    # the same bytes the Analytics page and the deck read. Deriving from a
    # different shape than consumers see is how two implementations start
    # to disagree.
    payload = findings.model_dump(mode="json")
    payload["charts"] = charts_for_results(payload.get("results") or [])

    if existing is not None:
        existing.schema_version = findings.schema_version
        existing.findings_json = payload
        # These results were computed just now, not when the run first
        # completed. Leaving the old stamp would misdate them on a page
        # whose whole subject is what the numbers were computed from.
        existing.generated_at = datetime.now(timezone.utc)
        record = existing
    else:
        record = BusinessAnalysis(
            run_id=run.id,
            schema_version=findings.schema_version,
            findings_json=payload,
        )
        db.add(record)

    ran = [r.analysis for r in findings.results if r.ran]
    skipped = [r.analysis for r in findings.results if not r.ran]
    # A trace per computation, including a recompute after a confirmation.
    # Overwriting the row without a new trace would leave the Audit screen
    # showing a set of results that no longer exists, with nothing recording
    # that a person's decision changed them.
    confirmed_note = f" confirmed_roles={json.dumps(confirmed, sort_keys=True)}" if confirmed else ""
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="business_analytics",
            input_summary=(
                f"columns={len(repaired_df.columns)} rows={len(repaired_df)}"
                f"{confirmed_note}{' (recomputed)' if existing is not None else ''}"
            ),
            output_summary=json.dumps(
                {
                    "analyses_run": ran,
                    "analyses_not_run": skipped,
                    "finding_count": len(findings.all_findings()),
                    "detected_roles": {
                        role: candidate["column"]
                        for role, candidate in findings.detected_roles.get("assigned", {}).items()
                    },
                }
            ),
            # Deterministic end to end - there is no model whose confidence
            # could be anything other than certain.
            confidence_score=1.0,
        )
    )
    db.flush()
    db.refresh(record)
    return record
