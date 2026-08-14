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
from app.analytics.findings import BusinessAnalyticsFindings
from app.models import AgentTrace, BusinessAnalysis, ExplorationFinding, Report, Run
from app.narrative.agent import NarrativeAgent
from app.narrative.charts import build_charts
from app.semantic_roles import prompt_context
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

    # Business analytics findings, when this run produced them, are offered
    # to stage 1 as additional claim sources on identical terms. Absent (an
    # older run, or a table no analysis applied to) the report is exactly
    # what it was before.
    analytics_findings = None
    analytics_record = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if analytics_record is not None:
        try:
            analytics_findings = BusinessAnalyticsFindings.model_validate(analytics_record.findings_json)
        except Exception:  # noqa: BLE001 - a stale payload must not block a report
            analytics_findings = None

    report = generate_narrative_report(
        findings, repaired_df, narrative_agent, config, analytics_findings, roles=run.semantic_roles
    )

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
    analytics_findings=None,
    roles: dict | None = None,
) -> NarrativeReport:
    """`roles` is Run.semantic_roles - the run's one detection pass. It keeps
    identifier columns out of the charts and tells the grounding stage what
    each column MEANS. None for an older run, which behaves as before."""
    config = config or NarrativeConfig()
    # Deterministic in BOTH generation modes, computed once, up front - Part
    # 5's guarantee that the caveat cannot be separated from the content
    # starts here, not as a post-processing step applied only sometimes.
    quality_context_summary = render_quality_context_summary(findings.data_quality_context)
    charts = build_charts(findings, repaired_df, config, roles=roles)

    if narrative_agent is None:
        return _template_report(findings, quality_context_summary, charts, reason="no LLM configured for this run")

    attempt = _try_llm_report(
        findings, narrative_agent, config, quality_context_summary, charts, analytics_findings, roles
    )
    if attempt.report is not None:
        return attempt.report

    return _template_report(
        findings, quality_context_summary, charts, reason=attempt.fallback_reason, post_check_history=attempt.post_check_history
    )


def _stage_failure_reason(what_failed: str, stage: str, outcome) -> str:
    """The sentence a reader of the report actually sees.

    Leads with the cause in plain words ("couldn't ground the narrative -
    the model's response was cut off before it finished") and keeps the
    stage and machine-readable source code in a trailing parenthetical,
    where codes belong and where anyone correlating with the backend log
    can still find them. `failure_summary` is absent only for an outcome
    produced before this existed, so the code alone remains the fallback.
    """
    summary = getattr(outcome, "failure_summary", None)
    tail = f"({stage}, {outcome.source})"
    if summary:
        return f"couldn't {what_failed} - {summary} {tail}"
    return f"couldn't {what_failed} {tail}"


def _log_stage_failure(stage: str, source: str, rejected_reasons: list[str]) -> None:
    """Full provider detail to the backend log, where a report can't carry
    it - finish_reason, model, token counts and the raw response body."""
    detail = " | ".join(rejected_reasons) if rejected_reasons else "no detail captured"
    print(f"[narrative] {stage} failed ({source}): {detail}")


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
    analytics_findings=None,
    roles: dict | None = None,
) -> _LLMAttemptResult:
    claims_outcome = narrative_agent.generate_claims(
        findings,
        analytics_findings=analytics_findings,
        column_roles=prompt_context(roles),
    )
    if claims_outcome.claims is not None and len(claims_outcome.claims) == 0:
        # Parsed cleanly and cited nothing. Distinct from a failed stage:
        # source is "llm" here, so without this branch it read as
        # "couldn't ground the narrative (llm)" - technically true and
        # completely uninformative.
        _log_stage_failure("stage 1 (grounding)", claims_outcome.source, ["model returned zero claims"])
        return _LLMAttemptResult(report=None, fallback_reason="couldn't ground the narrative - the model produced no claims about these findings (stage 1, llm)")
    if not claims_outcome.claims:
        # This used to report only `claims_outcome.source`, discarding the
        # rejected_reasons the agent had already collected - so a fallback
        # read "stage 1 (grounding) unavailable: escalated_parse_failure",
        # naming the outcome but never the cause, and leaving nothing to
        # diagnose from afterwards. Both now survive: the human-readable
        # cause in the report, the full provider detail in the log.
        _log_stage_failure("stage 1 (grounding)", claims_outcome.source, claims_outcome.rejected_reasons)
        return _LLMAttemptResult(report=None, fallback_reason=_stage_failure_reason("ground the narrative", "stage 1", claims_outcome))

    # BOTH agents' finding ids in one set. Grounding neither knows nor
    # cares which agent produced an id - a claim citing something neither
    # produced is rejected identically.
    known_finding_ids = {finding.id for finding in findings.findings}
    if analytics_findings is not None:
        known_finding_ids |= {f.id for f in analytics_findings.all_findings()}
    grounding_result = filter_grounded_claims(claims_outcome.claims, known_finding_ids, config)
    if not grounding_result.valid_claims:
        # This branch was already detailed - every rejection names the claim
        # and the unknown finding_ids it cited. That is a CORRECT rejection
        # (an ungrounded claim must never reach a report), so the wording
        # says the claims were rejected, not that something malfunctioned.
        reasons = "; ".join(grounding_result.rejected_reasons) or "no claims survived grounding validation"
        _log_stage_failure("stage 1 (grounding)", "rejected_ungrounded", grounding_result.rejected_reasons)
        return _LLMAttemptResult(
            report=None,
            fallback_reason=f"couldn't ground the narrative - every claim cited findings this run didn't produce (stage 1): {reasons}",
        )

    claims = grounding_result.valid_claims
    post_check_history: list[PostCheckAttempt] = []

    # attempt 1 is the first generation; Part 2 permits exactly
    # config.max_regeneration_attempts further tries before giving up.
    for attempt_number in range(1, config.max_regeneration_attempts + 2):
        prose_outcome = narrative_agent.generate_prose(claims)
        if prose_outcome.prose is None:
            _log_stage_failure("stage 2 (expansion)", prose_outcome.source, prose_outcome.rejected_reasons)
            return _LLMAttemptResult(
                report=None,
                post_check_history=post_check_history,
                fallback_reason=_stage_failure_reason("write the narrative", "stage 2", prose_outcome),
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
