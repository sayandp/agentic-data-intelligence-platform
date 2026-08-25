"""Producing and persisting a run's session summary.

Idempotent: a run that already has a summary is left alone rather than
regenerated, the same pattern app/exploration/pipeline.py and
app/narrative/pipeline.py follow.

The LLM path must clear its post-checks. If it cannot - after one regeneration
attempt - the template is used and the reason is recorded. A summary that
failed number fidelity is worse than a plain one: it is confident, readable,
and wrong, for a reader who was told they would not need to check.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AgentTrace, ExplorationFinding, Run, SessionSummary
from app.exploration.findings import DataQualityContext
from app.narrative.quality import render_quality_context_summary
from app.privacy.classification import PrivacyClassification
from app.privacy.egress_log import persist_egress
from app.summary.agent import SessionSummaryAgent
from app.summary.config import SummaryConfig
from app.summary.facts import build_summary_facts
from app.summary.postchecks import run_summary_post_checks
from app.summary.template import render_template_summary

#: One regeneration attempt on a post-check failure, matching the Narrative
#: Agent. A second retry has never been observed to help and doubles the cost.
MAX_GENERATION_ATTEMPTS = 2


def _quality_context_for(db: Session, run: Run) -> str:
    """The same deterministic renderer the narrative uses, over the same
    stored context - so the two never state different caveats for one run."""
    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    payload = (record.findings_json or {}) if record else {}
    raw = payload.get("data_quality_context")
    if isinstance(raw, dict):
        try:
            return render_quality_context_summary(DataQualityContext.model_validate(raw))
        except Exception:  # noqa: BLE001 - a stale payload must not block a summary
            pass
    return render_quality_context_summary(DataQualityContext(total_events=0))


def run_summary_for_run(
    db: Session,
    run: Run,
    agent: SessionSummaryAgent | None,
    config: SummaryConfig | None = None,
) -> SessionSummary | None:
    """Idempotent. Returns the existing row unchanged if there is one."""
    existing = db.query(SessionSummary).filter(SessionSummary.run_id == run.id).one_or_none()
    if existing is not None:
        return existing

    config = config or SummaryConfig()
    facts = build_summary_facts(db, run)
    quality_context = _quality_context_for(db, run)
    facts_json = [
        {"id": f.id, "group": f.group.value, "text": f.text, "values": f.values, "origin": f.origin}
        for f in facts
    ]

    summary_text: str | None = None
    generation_mode = "template"
    fallback_reason: str | None = None
    claims_json = None
    post_checks: list = []

    if agent is None:
        fallback_reason = "no language model is configured for this run"
    else:
        privacy = PrivacyClassification.from_dict(run.privacy_classification)
        egress_records: list = []

        claims_outcome = agent.generate_claims(facts, privacy=privacy)
        if claims_outcome.egress is not None:
            egress_records.append(claims_outcome.egress)

        if not claims_outcome.claims:
            fallback_reason = (
                f"the grounding stage produced no usable claims ({claims_outcome.source})"
                if claims_outcome.claims is not None
                else f"the grounding stage failed ({claims_outcome.source})"
            )
        else:
            claims = claims_outcome.claims
            for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
                prose_outcome = agent.generate_prose(claims)
                if prose_outcome.egress is not None:
                    egress_records.append(prose_outcome.egress)
                if prose_outcome.prose is None:
                    fallback_reason = f"the writing stage failed ({prose_outcome.source})"
                    break

                candidate = prose_outcome.prose.summary_text
                checked = run_summary_post_checks(claims, candidate, attempt, config)
                post_checks.append(checked)
                if all(outcome.passed for outcome in checked.outcomes):
                    summary_text = candidate
                    generation_mode = "llm"
                    claims_json = [c.model_dump(mode="json") for c in claims]
                    break
                failures = [issue.detail for o in checked.outcomes for issue in o.issues]
                fallback_reason = f"the generated summary failed its post-checks: {'; '.join(failures[:3])}"

        persist_egress(db, run.id, egress_records)

    if summary_text is None:
        summary_text = render_template_summary(facts, fallback_reason or "generation was not attempted")

    record = SessionSummary(
        run_id=run.id,
        summary_text=summary_text,
        quality_context=quality_context,
        generation_mode=generation_mode,
        fallback_reason=None if generation_mode == "llm" else fallback_reason,
        claims_json=claims_json,
        facts_json=facts_json,
        post_check_results=[a.model_dump(mode="json") for a in post_checks],
    )
    db.add(record)
    db.add(
        AgentTrace(
            run_id=run.id,
            agent_name="session_summary",
            input_summary=f"facts={len(facts)}",
            output_summary=f"mode={generation_mode} sentences={len(summary_text.split('.'))}",
            edge_taken=generation_mode,
        )
    )
    db.flush()
    db.refresh(record)
    return record


def rendered_summary(record: SessionSummary) -> str:
    """Quality context FIRST, always. The caveat cannot be separated from the
    content by anything downstream - the same guarantee the narrative makes."""
    return f"{record.quality_context}\n\n{record.summary_text}"
