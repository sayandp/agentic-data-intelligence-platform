"""Persists the Agriculture Agent's output for one run.

Idempotent, and shaped exactly like app/marketing/pipeline.py: a run that
already has a row is left alone rather than recomputed, and an AgentTrace is
written like every other agent so the Audit screen shows this agent's decision
alongside the rest.

Runs on the same REPAIRED frame, only for a completed run. Writes a row EVEN
WHEN THE RUN DOES NOT QUALIFY - "this source is not agricultural data, and here
is the role it was missing" is the output a user needs when the Agriculture tab
is absent, and a missing row would leave them guessing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.agriculture.config import AgricultureConfig
from app.agriculture.engine import run_agriculture
from app.analytics.roles import RoleDetection
from app.models import AgentTrace, AgricultureAnalysis, Run


def run_agriculture_for_run(
    db: Session,
    run: Run,
    repaired_df: pd.DataFrame,
    detection: RoleDetection,
    config: AgricultureConfig | None = None,
    baseline_profile: dict | None = None,
    replace: bool = False,
) -> AgricultureAnalysis | None:
    """Idempotent by default. `replace=True` recomputes an existing row - used
    when a human confirms a column role, precisely the case where the previous
    answer is known to be stale."""
    if run.status != "completed":
        return None

    existing = db.query(AgricultureAnalysis).filter(AgricultureAnalysis.run_id == run.id).one_or_none()
    if existing is not None and not replace:
        return existing

    findings = run_agriculture(run.id, repaired_df, detection, config=config, baseline_profile=baseline_profile)
    payload = findings.model_dump(mode="json")

    if existing is not None:
        existing.schema_version = findings.schema_version
        existing.findings_json = payload
        existing.generated_at = datetime.now(timezone.utc)
        record = existing
    else:
        record = AgricultureAnalysis(run_id=run.id, schema_version=findings.schema_version, findings_json=payload)
        db.add(record)

    warnings = [f for f in findings.findings if f.severity.value == "warning"]
    improvements = [f for f in findings.findings if f.severity.value == "improvement"]
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="agriculture",
            input_summary=f"row_count={len(repaired_df)} column_count={len(repaired_df.columns)}",
            output_summary=json.dumps(
                {
                    "applicable": findings.applicable,
                    "not_applicable_reason": findings.not_applicable_reason,
                    "warning_count": len(warnings),
                    "improvement_count": len(improvements),
                    "skipped_rule_count": len(findings.skipped_rules),
                    "missing_roles": findings.missing_roles,
                }
            ),
            # Deterministic, not a model output - always fully confident in
            # what it computed, exactly like exploration and analytics.
            confidence_score=1.0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    db.flush()
    db.refresh(record)
    return record
