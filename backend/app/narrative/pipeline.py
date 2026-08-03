"""Part 6: orchestrates stage 1 -> stage 2 -> post-checks ->
(accept | regenerate once | template fallback), assembles the
NarrativeReport, and persists it.

Wired in right after exploration completes (app/exploration/pipeline.py::
run_exploration_for_run) - app/routers/ingest.py and
app/routers/approvals.py both call run_narrative_for_run the same way,
with the SAME ExplorationFinding row and repaired frame exploration itself
just used. The run must never fail for lack of an LLM (Part 4): every path
through generate_narrative_report ends in a NarrativeReport, never an
exception.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.exploration.findings import ExplorationFindings
from app.models import AgentTrace, ExplorationFinding, Report, Run
from app.narrative.agent import NarrativeAgent
from app.narrative.charts import build_charts
from app.narrative.config import NarrativeConfig
from app.narrative.grounding import filter_grounded_claims
from app.narrative.models import ChartRef, GenerationMode, NarrativeReport, PostCheckAttempt
from app.narrative.postchecks import run_post_checks
from app.narrative.quality import render_quality_context_summary
from app.narrative.template import build_template_claims, render_template_narrative


def run_narrative_for_run(
    db: Session,
    run: Run,
    exploration_record: ExplorationFinding,
    repaired_df: pd.DataFrame,
    narrative_agent: NarrativeAgent | None,
    config: NarrativeConfig | None = None,
) -> Report | None:
    """Idempotent: a run that already has a Report row is left alone rather
    than regenerated and re-persisted a second time - same pattern as
    app/exploration/pipeline.py::run_exploration_for_run."""
    existing = db.query(Report).filter(Report.run_id == run.id).one_or_none()
    if existing is not None:
        return existing

    findings = ExplorationFindings.model_validate(exploration_record.findings_json)
    report = generate_narrative_report(findings, repaired_df, narrative_agent, config)

    record = Report(
        run_id=run.id,
        narrative_text=report.rendered_text(),
        chart_refs=[chart.model_dump(mode="json") for chart in report.charts],
        grounded_claims_json=[claim.model_dump(mode="json") for claim in report.grounded_claims],
        generation_mode=report.generation_mode.value,
        post_check_results=[attempt.model_dump(mode="json") for attempt in report.post_check_history],
        delivered_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="narrative",
            input_summary=f"finding_count={len(findings.findings)} chart_count={len(report.charts)}",
            output_summary=json.dumps(
                {
                    "generation_mode": report.generation_mode.value,
                    "claim_count": len(report.grounded_claims),
                    "post_check_attempts": len(report.post_check_history),
                    "fallback_reason": report.fallback_reason,
                }
            ),
            confidence_score=1.0 if report.generation_mode == GenerationMode.TEMPLATE else None,
        )
    )
    db.flush()
    db.refresh(record)
    return record


def generate_narrative_report(
    findings: ExplorationFindings,
    repaired_df: pd.DataFrame,
    narrative_agent: NarrativeAgent | None,
    config: NarrativeConfig | None = None,
) -> NarrativeReport:
    config = config or NarrativeConfig()
    # Deterministic in BOTH generation modes, computed once, up front - Part
    # 5's guarantee that the caveat cannot be separated from the content
    # starts here, not as a post-processing step applied only sometimes.
    quality_context_summary = render_quality_context_summary(findings.data_quality_context)
    charts = build_charts(findings, repaired_df, config)

    if narrative_agent is None:
        return _template_report(findings, quality_context_summary, charts, reason="no LLM configured for this run")

    attempt = _try_llm_report(findings, narrative_agent, config, quality_context_summary, charts)
    if attempt.report is not None:
        return attempt.report

    return _template_report(
        findings, quality_context_summary, charts, reason=attempt.fallback_reason, post_check_history=attempt.post_check_history
    )


@dataclass
class _LLMAttemptResult:
    report: NarrativeReport | None
    post_check_history: list[PostCheckAttempt] = field(default_factory=list)
    fallback_reason: str | None = None


def _try_llm_report(
    findings: ExplorationFindings,
    narrative_agent: NarrativeAgent,
    config: NarrativeConfig,
    quality_context_summary: str,
    charts: list[ChartRef],
) -> _LLMAttemptResult:
    claims_outcome = narrative_agent.generate_claims(findings)
    if not claims_outcome.claims:
        return _LLMAttemptResult(report=None, fallback_reason=f"stage 1 (grounding) unavailable: {claims_outcome.source}")

    known_finding_ids = {finding.id for finding in findings.findings}
    grounding_result = filter_grounded_claims(claims_outcome.claims, known_finding_ids, config)
    if not grounding_result.valid_claims:
        reasons = "; ".join(grounding_result.rejected_reasons) or "no claims survived grounding validation"
        return _LLMAttemptResult(report=None, fallback_reason=f"stage 1 produced no usable claims: {reasons}")

    claims = grounding_result.valid_claims
    post_check_history: list[PostCheckAttempt] = []

    # attempt 1 is the first generation; Part 2 permits exactly
    # config.max_regeneration_attempts further tries before giving up.
    for attempt_number in range(1, config.max_regeneration_attempts + 2):
        prose_outcome = narrative_agent.generate_prose(claims)
        if prose_outcome.prose is None:
            return _LLMAttemptResult(
                report=None,
                post_check_history=post_check_history,
                fallback_reason=f"stage 2 (expansion) unavailable: {prose_outcome.source}",
            )

        check_attempt = run_post_checks(claims, prose_outcome.prose, attempt=attempt_number, config=config)
        post_check_history.append(check_attempt)
        if check_attempt.passed:
            report = NarrativeReport(
                run_id=findings.run_id,
                generation_mode=GenerationMode.LLM,
                quality_context_summary=quality_context_summary,
                narrative_text=prose_outcome.prose.report_text,
                recommendations=prose_outcome.prose.recommendations,
                grounded_claims=claims,
                charts=charts,
                post_check_history=post_check_history,
            )
            return _LLMAttemptResult(report=report, post_check_history=post_check_history)

    failed_kinds = sorted({kind.value for a in post_check_history for kind in a.failed_kinds})
    reason = f"post-checks failed on {len(post_check_history)} attempt(s): {failed_kinds}"
    return _LLMAttemptResult(report=None, post_check_history=post_check_history, fallback_reason=reason)


def _template_report(
    findings: ExplorationFindings,
    quality_context_summary: str,
    charts: list[ChartRef],
    reason: str,
    post_check_history: list[PostCheckAttempt] | None = None,
) -> NarrativeReport:
    claims = build_template_claims(findings)
    narrative_text = render_template_narrative(claims)
    return NarrativeReport(
        run_id=findings.run_id,
        generation_mode=GenerationMode.TEMPLATE,
        quality_context_summary=quality_context_summary,
        narrative_text=narrative_text,
        recommendations=[],
        grounded_claims=claims,
        charts=charts,
        post_check_history=post_check_history or [],
        fallback_reason=reason,
    )
