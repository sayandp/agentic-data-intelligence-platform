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

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db import release_connection
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


@dataclass
class SummaryInputs:
    """What the summary needs from the database, read once and carried as
    plain values - so the model calls need no session and none is open."""

    run_id: str
    facts: list
    facts_json: list
    quality_context: str
    privacy: PrivacyClassification


@dataclass
class SummaryResult:
    summary_text: str
    generation_mode: str
    fallback_reason: str | None
    claims_json: list | None
    post_checks: list
    egress_records: list = field(default_factory=list)


def load_summary_inputs(db: Session, run: Run) -> SummaryInputs | None:
    """Phase 1 of 3. None when the run already has a summary (idempotent)."""
    if db.query(SessionSummary).filter(SessionSummary.run_id == run.id).one_or_none() is not None:
        return None
    facts = build_summary_facts(db, run)
    return SummaryInputs(
        run_id=run.id,
        facts=facts,
        facts_json=[
            {"id": f.id, "group": f.group.value, "text": f.text, "values": f.values, "origin": f.origin}
            for f in facts
        ],
        quality_context=_quality_context_for(db, run),
        privacy=PrivacyClassification.from_dict(run.privacy_classification),
    )


def generate_summary(
    inputs: SummaryInputs, agent: SessionSummaryAgent | None, config: SummaryConfig | None = None
) -> SummaryResult:
    """Phase 2 of 3: the model calls. Takes no session and can reach none."""
    config = config or SummaryConfig()
    facts = inputs.facts

    summary_text: str | None = None
    generation_mode = "template"
    fallback_reason: str | None = None
    claims_json = None
    post_checks: list = []
    egress_records: list = []

    if agent is None:
        fallback_reason = "no language model is configured for this run"
    else:
        claims_outcome = agent.generate_claims(facts, privacy=inputs.privacy)
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

    if summary_text is None:
        summary_text = render_template_summary(facts, fallback_reason or "generation was not attempted")

    return SummaryResult(
        summary_text=summary_text,
        generation_mode=generation_mode,
        fallback_reason=fallback_reason,
        claims_json=claims_json,
        post_checks=post_checks,
        egress_records=egress_records,
    )


def save_summary(db: Session, inputs: SummaryInputs, result: SummaryResult) -> SessionSummary | None:
    """Phase 3 of 3, in a FRESH session, re-reading the run first. Same rules
    as the narrative's save: egress is always recorded, because those calls
    went out; the summary is not written over one that appeared meanwhile,
    nor against a run that is no longer "completed"; a dropped summary
    leaves a trace saying why."""
    persist_egress(db, inputs.run_id, result.egress_records)

    run = db.get(Run, inputs.run_id)
    existing = db.query(SessionSummary).filter(SessionSummary.run_id == inputs.run_id).one_or_none()
    skip_reason = None
    if run is None:
        skip_reason = "the run no longer exists"
    elif existing is not None:
        skip_reason = "a summary was written for this run while this one was being generated; the existing one is kept"
    elif run.status != "completed":
        skip_reason = f"the run is now {run.status!r}, not 'completed'; no summary is written against its earlier state"

    if skip_reason is not None:
        if run is not None:
            db.add(
                AgentTrace(
                    run_id=inputs.run_id,
                    agent_name="session_summary",
                    input_summary="summary generated, not saved",
                    output_summary=f"skipped: {skip_reason}",
                    edge_taken="skipped",
                )
            )
        db.flush()
        return existing

    record = SessionSummary(
        run_id=inputs.run_id,
        summary_text=result.summary_text,
        quality_context=inputs.quality_context,
        generation_mode=result.generation_mode,
        fallback_reason=None if result.generation_mode == "llm" else result.fallback_reason,
        claims_json=result.claims_json,
        facts_json=inputs.facts_json,
        post_check_results=[a.model_dump(mode="json") for a in result.post_checks],
    )
    db.add(record)
    db.add(
        AgentTrace(
            run_id=inputs.run_id,
            agent_name="session_summary",
            input_summary=f"facts={len(inputs.facts)}",
            output_summary=f"mode={result.generation_mode} sentences={len(result.summary_text.split('.'))}",
            edge_taken=result.generation_mode,
        )
    )
    db.flush()
    db.refresh(record)
    return record


def run_summary_for_run(
    db: Session,
    run: Run,
    agent: SessionSummaryAgent | None,
    config: SummaryConfig | None = None,
) -> SessionSummary | None:
    """All three phases against a session the CALLER owns, releasing its
    connection before the model calls (release_connection refuses if the
    caller left changes pending). app/graph/nodes.py::summarise_node goes
    further and uses separate sessions. Idempotent."""
    inputs = load_summary_inputs(db, run)
    if inputs is None:
        return db.query(SessionSummary).filter(SessionSummary.run_id == run.id).one_or_none()
    release_connection(db)
    result = generate_summary(inputs, agent, config)
    return save_summary(db, inputs, result)


def rendered_summary(record: SessionSummary) -> str:
    """Quality context FIRST, always. The caveat cannot be separated from the
    content by anything downstream - the same guarantee the narrative makes."""
    return f"{record.quality_context}\n\n{record.summary_text}"
