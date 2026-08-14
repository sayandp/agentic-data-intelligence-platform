"""Part 5: wires the deterministic ExplorationEngine to persistence. Runs
ONLY for a run that has reached status == "completed" - never
awaiting_approval (unresolved events could still change the repaired frame)
and never failed (reject_data). Callers (app/routers/ingest.py,
app/routers/approvals.py) are responsible for handing this the REPAIRED
frame (fix_chain applied), never the raw one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.exploration.engine import ExplorationEngine
from app.exploration.quality_context import build_data_quality_context
from app.models import AgentTrace, ExplorationFinding, Run, ValidationEvent


def run_exploration_for_run(
    db: Session,
    run: Run,
    repaired_df: pd.DataFrame,
    baseline_is_provisional: bool,
    roles: dict | None = None,
) -> ExplorationFinding | None:
    """Idempotent: a run that already has an ExplorationFinding row is left
    alone rather than recomputed and re-persisted a second time."""
    if run.status != "completed":
        return None
    existing = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    if existing is not None:
        return existing

    events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run.id).all()
    data_quality_context = build_data_quality_context(events, baseline_is_provisional)

    # `roles` is the run's ONE semantic detection pass, used to keep
    # identifier columns out of correlation, trend, outlier and
    # distribution analysis. Falls back to run.semantic_roles so a caller
    # that does not pass it still gets the run's own roles.
    findings = ExplorationEngine().run(
        repaired_df,
        run_id=run.id,
        data_quality_context=data_quality_context,
        roles=roles if roles is not None else run.semantic_roles,
    )

    record = ExplorationFinding(
        run_id=run.id,
        schema_version=findings.schema_version,
        findings_json=json.loads(findings.model_dump_json()),
        generated_at=findings.generated_at,
    )
    db.add(record)
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="exploration",
            input_summary=f"row_count={len(repaired_df)} column_count={len(repaired_df.columns)}",
            output_summary=json.dumps(
                {
                    "finding_count": len(findings.findings),
                    "finding_types": sorted({f.finding_type.value for f in findings.findings}),
                    "skipped_count": len(findings.skipped),
                }
            ),
            confidence_score=1.0,  # deterministic, not a model output - always fully confident in what it computed
            timestamp=datetime.now(timezone.utc),
        )
    )
    db.flush()
    db.refresh(record)
    return record
