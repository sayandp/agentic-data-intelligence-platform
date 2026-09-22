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
from app.privacy.classification import PrivacyClassification
from app.db import release_connection
from app.privacy.egress_log import persist_egress
from app.narrative.charts import build_charts
from app.semantic_roles import prompt_context
from app.narrative.config import NarrativeConfig
from app.narrative.grounding import filter_grounded_claims
from app.narrative.models import ChartRef, GenerationMode, NarrativeReport, PostCheckAttempt
from app.narrative.postchecks import run_post_checks
from app.narrative.quality import render_quality_context_summary
from app.narrative.template import build_template_claims, render_template_narrative


@dataclass
class NarrativeInputs:
    """Everything the report needs that lives in the database, read once and
    then carried as plain values - so the model calls that follow need no
    session, and none is open while they run."""

    run_id: str
    findings: ExplorationFindings
    analytics_findings: object | None
    roles: dict | None
    privacy: PrivacyClassification


def load_narrative_inputs(db: Session, run: Run, exploration_record: ExplorationFinding) -> NarrativeInputs | None:
    """Phase 1 of 3. None when the run already has a report (idempotent)."""
    if db.query(Report).filter(Report.run_id == run.id).one_or_none() is not None:
        return None

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

    return NarrativeInputs(
        run_id=run.id,
        findings=findings,
        analytics_findings=analytics_findings,
        roles=run.semantic_roles,
        privacy=PrivacyClassification.from_dict(run.privacy_classification),
    )


def generate_for_inputs(
    inputs: NarrativeInputs,
    repaired_df: pd.DataFrame,
    narrative_agent: NarrativeAgent | None,
    config: NarrativeConfig | None = None,
) -> tuple[NarrativeReport, list]:
    """Phase 2 of 3: the model calls. Takes no session and can reach none."""
    egress_records: list = []
    report = generate_narrative_report(
        inputs.findings,
        repaired_df,
        narrative_agent,
        config,
        inputs.analytics_findings,
        roles=inputs.roles,
        privacy=inputs.privacy,
        egress_sink=egress_records,
    )
    return report, egress_records


def save_narrative_report(
    db: Session, inputs: NarrativeInputs, report: NarrativeReport, egress_records: list
) -> Report | None:
    """Phase 3 of 3, in a FRESH session. The run is re-read, because it may
    have changed while the model was answering.

    The egress rows are written whatever happens next: those calls went out,
    and the audit trail must say so even when the report they produced is
    not kept.

    Then the report is written only if it would not overwrite anything:
      - a report that appeared in the meantime (another writer finished
        first) is kept, and this one is dropped;
      - a run that is no longer "completed" (discarded, failed, or sent
        back for a decision) gets no report written against its old state.
    Each outcome is recorded as a trace, so a dropped report is visible
    rather than silently absent.
    """
    persist_egress(db, inputs.run_id, egress_records)

    run = db.get(Run, inputs.run_id)
    existing = db.query(Report).filter(Report.run_id == inputs.run_id).one_or_none()
    skip_reason = None
    if run is None:
        skip_reason = "the run no longer exists"
    elif existing is not None:
        skip_reason = "a report was written for this run while this one was being generated; the existing one is kept"
    elif run.status != "completed":
        skip_reason = f"the run is now {run.status!r}, not 'completed'; no report is written against its earlier state"

    if skip_reason is not None:
        if run is not None:
            db.add(
                AgentTrace(
                    run_id=inputs.run_id,
                    agent_name="narrative",
                    input_summary="report generated, not saved",
                    output_summary=json.dumps({"skipped": skip_reason, "generation_mode": report.generation_mode.value}),
                    edge_taken="skipped",
                )
            )
        db.flush()
        return existing

    record = Report(
        run_id=inputs.run_id,
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
            run_id=inputs.run_id,
            agent_name="narrative",
            input_summary=f"finding_count={len(inputs.findings.findings)} chart_count={len(report.charts)}",
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


def run_narrative_for_run(
    db: Session,
    run: Run,
    exploration_record: ExplorationFinding,
    repaired_df: pd.DataFrame,
    narrative_agent: NarrativeAgent | None,
    config: NarrativeConfig | None = None,
) -> Report | None:
    """All three phases against a session the CALLER owns. Its connection is
    released before the model calls (release_connection refuses if the
    caller left changes pending), so even this entry point holds nothing
    across the network. app/graph/nodes.py::narrate_node goes further and
    uses separate sessions for the read and the write.

    Idempotent: a run that already has a Report row is left alone."""
    inputs = load_narrative_inputs(db, run, exploration_record)
    if inputs is None:
        return db.query(Report).filter(Report.run_id == run.id).one_or_none()
    release_connection(db)
    report, egress_records = generate_for_inputs(inputs, repaired_df, narrative_agent, config)
    return save_narrative_report(db, inputs, report, egress_records)


def generate_narrative_report(
    findings: ExplorationFindings,
    repaired_df: pd.DataFrame,
    narrative_agent: NarrativeAgent | None,
    config: NarrativeConfig | None = None,
    analytics_findings=None,
    roles: dict | None = None,
    privacy=None,
    egress_sink: list | None = None,
) -> NarrativeReport:
    """`egress_sink`, when given, collects the EgressRecord for each outbound
    call so the caller - which holds the DB session, unlike this function -
    can persist it. A None sink records nothing and changes no behaviour.

    `roles` is Run.semantic_roles - the run's one detection pass. It keeps
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
        findings, narrative_agent, config, quality_context_summary, charts, analytics_findings, roles, privacy,
        egress_sink=egress_sink,
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
    privacy=None,
    egress_sink: list | None = None,
) -> _LLMAttemptResult:
    claims_outcome = narrative_agent.generate_claims(
        findings,
        analytics_findings=analytics_findings,
        column_roles=prompt_context(roles),
        privacy=privacy,
    )
    # Recorded whatever the outcome: the payload left the machine even when
    # the model's answer came back unusable.
    if egress_sink is not None and claims_outcome.egress is not None:
        egress_sink.append(claims_outcome.egress)
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
        # Inside the loop deliberately: each regeneration attempt is its own
        # outbound call, and a run that regenerated three times reached a
        # third party three times. Recording one row for the loop would make
        # the trail's call count quietly wrong.
        if egress_sink is not None and prose_outcome.egress is not None:
            egress_sink.append(prose_outcome.egress)
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
