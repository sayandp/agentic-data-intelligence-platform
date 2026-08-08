"""Persists the Business Analytics Agent's output for one run.

Idempotent, and shaped exactly like app/exploration/pipeline.py: a run that
already has a row is left alone rather than recomputed, and an AgentTrace is
written like every other agent so the Audit screen shows this agent's
decision alongside the rest.

Runs AFTER exploration, on the REPAIRED frame, only for completed runs.
"""

from __future__ import annotations

import json

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.engine import run_business_analytics
from app.models import AgentTrace, BusinessAnalysis, Run


def run_business_analytics_for_run(db: Session, run: Run, repaired_df: pd.DataFrame) -> BusinessAnalysis | None:
    existing = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if existing is not None:
        return existing

    findings = run_business_analytics(run.id, repaired_df)

    record = BusinessAnalysis(
        run_id=run.id,
        schema_version=findings.schema_version,
        findings_json=findings.model_dump(mode="json"),
    )
    db.add(record)

    ran = [r.analysis for r in findings.results if r.ran]
    skipped = [r.analysis for r in findings.results if not r.ran]
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="business_analytics",
            input_summary=f"columns={len(repaired_df.columns)} rows={len(repaired_df)}",
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
