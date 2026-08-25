"""What the Session Summary is allowed to say, assembled from PERSISTED
ARTIFACTS.

NEVER THE EXPORTED DECK. The deck is this system's own output; summarising it
would mean the summary silently changes whenever deck rendering changes, and
two artifacts would have to agree forever - the two-sources-of-truth failure
this codebase keeps finding. Everything here is read from the rows the
pipeline already wrote: validation events, exploration findings, business
analytics, the marketing pack, model runs, and the Part 1 comparison against
the previous run.

A FACT IS A CITABLE UNIT. Each carries a stable id, a sentence of plain text,
the numbers it asserts, and - separately - any values it quotes out of a real
column. That separation is what makes redaction possible: the text is written
so it never embeds a column value, and the quoted values travel in
`column_values`, keyed by the column they came from, where the egress redactor
can mask them by name.

Facts are grouped by the question they answer, because the summary has to
answer those questions in that order:

    quality    was this data OK, what was fixed, what needs me
    finding    the two or three findings that matter
    change     what changed since last time
    attention  what to look at
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.comparison.engine import compare_runs
from app.comparison.models import Comparability
from app.models import (
    BusinessAnalysis,
    ExplorationFinding,
    MarketingAnalysis,
    ModelRun,
    Run,
    ValidationEvent,
)


class FactGroup(str, Enum):
    QUALITY = "quality"
    FINDING = "finding"
    CHANGE = "change"
    ATTENTION = "attention"


@dataclass
class SummaryFact:
    """One citable statement drawn from a persisted artifact."""

    id: str
    group: FactGroup
    text: str
    #: {label: number} the fact asserts. These become the ground truth the
    #: number-fidelity post-check measures generated prose against.
    values: dict[str, float] = field(default_factory=dict)
    #: {column: [values]} quoted out of real columns. Held apart from `text`
    #: so the egress redactor can mask them by column name.
    column_values: dict[str, list[Any]] = field(default_factory=dict)
    #: Where this came from, for the audit trail. Never shown to the model.
    origin: str = ""

    def to_prompt_dict(self) -> dict:
        payload: dict[str, Any] = {"id": self.id, "group": self.group.value, "statement": self.text}
        if self.values:
            payload["values"] = self.values
        return payload


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ---------------------------------------------------------------------------
# quality: was this data OK, what was fixed, what needs me
# ---------------------------------------------------------------------------


def _quality_facts(db: Session, run: Run) -> list[SummaryFact]:
    events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run.id).all()
    facts: list[SummaryFact] = []

    if not events:
        facts.append(
            SummaryFact(
                id="fact-quality-0",
                group=FactGroup.QUALITY,
                text="No data-quality issues were detected against the active baseline for this run.",
                values={"validation events": 0.0},
                origin="validation_events",
            )
        )
        return facts

    auto_fixed = [e for e in events if (e.action_taken or "").startswith("auto_fix") and "reverted" not in (e.action_taken or "")]
    reverted = [e for e in events if "reverted" in (e.action_taken or "")]
    open_now = [e for e in events if e.state in {"awaiting_approval", "escalated"}]
    resolved_by_human = [e for e in events if e.resolved_by]

    facts.append(
        SummaryFact(
            id="fact-quality-0",
            group=FactGroup.QUALITY,
            text=f"{len(events)} data-quality issue(s) were detected for this run.",
            values={"validation events": float(len(events))},
            origin="validation_events",
        )
    )
    if auto_fixed:
        facts.append(
            SummaryFact(
                id=f"fact-quality-{len(facts)}",
                group=FactGroup.QUALITY,
                text=f"{len(auto_fixed)} issue(s) were fixed automatically and verified against the baseline.",
                values={"auto-fixed": float(len(auto_fixed))},
                origin="validation_events.action_taken",
            )
        )
    if reverted:
        facts.append(
            SummaryFact(
                id=f"fact-quality-{len(facts)}",
                group=FactGroup.QUALITY,
                text=(
                    f"{len(reverted)} automatic fix attempt(s) failed verification, were reverted, "
                    "and went to human review."
                ),
                values={"reverted fixes": float(len(reverted))},
                origin="validation_events.action_taken",
            )
        )
    if resolved_by_human:
        facts.append(
            SummaryFact(
                id=f"fact-quality-{len(facts)}",
                group=FactGroup.QUALITY,
                text=f"{len(resolved_by_human)} issue(s) were resolved by a person.",
                values={"resolved by a person": float(len(resolved_by_human))},
                origin="validation_events.resolved_by",
            )
        )
    if open_now:
        rules = sorted({(e.rule_failed or "").split(":")[0] for e in open_now})
        facts.append(
            SummaryFact(
                id=f"fact-quality-{len(facts)}",
                group=FactGroup.ATTENTION,
                text=(
                    f"{len(open_now)} issue(s) are still waiting for a decision, covering {', '.join(rules)}."
                ),
                values={"waiting for a decision": float(len(open_now))},
                origin="validation_events.state",
            )
        )
    return facts


# ---------------------------------------------------------------------------
# findings: the two or three that matter
# ---------------------------------------------------------------------------

#: How many findings of each kind are offered. The summary is 4-8 sentences,
#: so handing the model forty findings would just make it choose arbitrarily.
_MAX_EXPLORATION_FACTS = 4
_MAX_ANALYTICS_FACTS = 4
_MAX_DOMAIN_FACTS = 3


def _exploration_facts(db: Session, run: Run) -> list[SummaryFact]:
    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    if record is None:
        return []

    findings = (record.findings_json or {}).get("findings") or []
    facts: list[SummaryFact] = []

    # Ranked by how much a reader would care, not by position in the list.
    # A correlation and a missing-data pattern say something; a summary stat
    # of a column nobody asked about is filler.
    priority = {"correlation": 0, "trend": 1, "missing_pattern": 2, "outlier_cluster": 3, "distribution_shape": 4}
    ordered = sorted(
        (f for f in findings if isinstance(f, dict)),
        key=lambda f: priority.get(str(f.get("finding_type")), 99),
    )

    for finding in ordered:
        if len(facts) >= _MAX_EXPLORATION_FACTS:
            break
        kind = str(finding.get("finding_type"))
        if kind not in priority:
            continue
        payload = finding.get("payload") or {}
        columns = [str(c) for c in (finding.get("columns") or [])]
        values = {k: v for k, v in ((k, _numeric(payload.get(k))) for k in ("coefficient", "r", "slope", "null_rate", "skew")) if v is not None}
        facts.append(
            SummaryFact(
                id=f"fact-finding-{len(facts)}",
                group=FactGroup.FINDING,
                text=f"Exploration reported a {kind.replace('_', ' ')} involving {', '.join(columns) or 'the data'}.",
                values=values,
                origin=f"exploration_findings/{finding.get('id', kind)}",
            )
        )
    return facts


def _analytics_facts(db: Session, run: Run) -> list[SummaryFact]:
    record = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run.id).one_or_none()
    if record is None:
        return []

    payload = record.findings_json or {}
    basis = (payload.get("value_definition") or {}).get("label")
    facts: list[SummaryFact] = []

    for result in payload.get("results") or []:
        if len(facts) >= _MAX_ANALYTICS_FACTS:
            break
        if not isinstance(result, dict) or not result.get("ran"):
            continue
        analysis = str(result.get("analysis"))
        for finding in (result.get("findings") or [])[:1]:
            if not isinstance(finding, dict):
                continue
            fp = finding.get("payload") or {}
            columns = [str(c) for c in (finding.get("columns") or [])]
            values = {
                label: value
                for label, value in (
                    ("entity count", _numeric(fp.get("entity_count"))),
                    ("value share", _numeric(fp.get("value_share"))),
                    ("segment size", _numeric(fp.get("size"))),
                    ("retention rate", _numeric(fp.get("retention_rate"))),
                )
                if value is not None
            }
            # Entity names are real column values. They travel separately so
            # the redactor can mask them by column, and the text never
            # embeds them.
            quoted: dict[str, list[Any]] = {}
            entities = fp.get("top_entities") or fp.get("examples")
            if isinstance(entities, list) and entities and columns:
                quoted[columns[0]] = list(entities)[:5]

            detail = f" measured on {basis}" if basis and "value share" in values else ""
            facts.append(
                SummaryFact(
                    id=f"fact-finding-{100 + len(facts)}",
                    group=FactGroup.FINDING,
                    text=f"The {analysis.replace('_', ' ')} analysis produced a {str(fp.get('finding_type', 'result')).replace('_', ' ')} result{detail}.",
                    values=values,
                    column_values=quoted,
                    origin=f"business_analyses/{analysis}",
                )
            )
    return facts


def _domain_facts(db: Session, run: Run) -> list[SummaryFact]:
    """The marketing pack, and any later domain pack that follows its shape.

    Reads the persisted findings rather than re-running the rules, so a domain
    pack needs no change here to be summarised - the seam Part 4 will use.
    """
    record = db.query(MarketingAnalysis).filter(MarketingAnalysis.run_id == run.id).one_or_none()
    if record is None:
        return []
    payload = record.findings_json or {}
    if not payload.get("applicable"):
        return []

    facts: list[SummaryFact] = []
    for finding in (payload.get("findings") or [])[:_MAX_DOMAIN_FACTS]:
        if not isinstance(finding, dict):
            continue
        fp = finding.get("payload") or {}
        severity = str(finding.get("severity") or fp.get("severity") or "")
        rule = str(fp.get("rule") or finding.get("finding_type") or "a marketing rule")
        values = {
            label: value
            for label, value in (
                ("observed", _numeric(fp.get("observed") or fp.get("observed_value"))),
                ("threshold", _numeric(fp.get("threshold"))),
            )
            if value is not None
        }
        basis = fp.get("compared_against")
        # The ad set name is a column value.
        quoted = {"scope": [fp["scope"]]} if fp.get("scope") else {}
        facts.append(
            SummaryFact(
                id=f"fact-finding-{200 + len(facts)}",
                group=FactGroup.ATTENTION if severity.lower() == "warning" else FactGroup.FINDING,
                text=(
                    f"The marketing pack reported {rule}"
                    + (f", compared against {basis}" if basis else "")
                    + "."
                ),
                values=values,
                column_values=quoted,
                origin="marketing_analyses",
            )
        )
    return facts


def _model_facts(db: Session, run: Run) -> list[SummaryFact]:
    model = (
        db.query(ModelRun)
        .filter(ModelRun.run_id == run.id, ModelRun.out_of_sample_score.isnot(None))
        .order_by(ModelRun.created_at.desc())
        .first()
    )
    if model is None:
        return []
    score = _numeric(model.out_of_sample_score)
    values = {f"out-of-sample {model.out_of_sample_metric}": score} if score is not None else {}
    return [
        SummaryFact(
            id="fact-finding-300",
            group=FactGroup.FINDING,
            text=(
                f"A {model.model_family or 'model'} was trained for {model.target_column!r} and scored on held-out data."
            ),
            values=values,
            origin="model_runs",
        )
    ]


# ---------------------------------------------------------------------------
# change: what changed since last time
# ---------------------------------------------------------------------------

#: How many deltas are offered. A summary reporting fifteen movements is a
#: table, not a summary.
_MAX_CHANGE_FACTS = 4


def previous_completed_run(db: Session, run: Run) -> Run | None:
    """The completed run of this source immediately before `run`."""
    if run.started_at is None:
        return None
    return (
        db.query(Run)
        .filter(
            Run.source_id == run.source_id,
            Run.status == "completed",
            Run.started_at < run.started_at,
            Run.id != run.id,
        )
        .order_by(Run.started_at.desc())
        .first()
    )


def _change_facts(db: Session, run: Run) -> list[SummaryFact]:
    previous = previous_completed_run(db, run)
    if previous is None:
        return [
            SummaryFact(
                id="fact-change-0",
                group=FactGroup.CHANGE,
                text="This is the first completed run of this source, so there is nothing to compare it against.",
                origin="comparison",
            )
        ]

    comparison = compare_runs(db, previous, run)
    if comparison.blocked_reason:
        return [
            SummaryFact(
                id="fact-change-0",
                group=FactGroup.CHANGE,
                text=f"This run could not be compared with the previous one: {comparison.blocked_reason}",
                origin="comparison",
            )
        ]

    facts: list[SummaryFact] = []
    for section in comparison.sections:
        if len(facts) >= _MAX_CHANGE_FACTS:
            break

        # A section that cannot be compared is itself worth saying - it is
        # the difference between "nothing changed" and "this could not be
        # checked", which a reader must never have to guess between.
        if section.comparability is Comparability.NOT_COMPARABLE:
            facts.append(
                SummaryFact(
                    id=f"fact-change-{len(facts)}",
                    group=FactGroup.CHANGE,
                    text=f"The {section.name.replace('_', ' ')} figures could not be compared with the previous run: {section.reason}",
                    origin=f"comparison/{section.name}",
                )
            )
            continue
        if section.comparability is not Comparability.COMPARABLE:
            continue

        for delta in section.deltas:
            if len(facts) >= _MAX_CHANGE_FACTS:
                break
            if not delta.changed or delta.before is None or delta.after is None:
                continue
            values = {f"{delta.label} before": delta.before, f"{delta.label} after": delta.after}
            facts.append(
                SummaryFact(
                    id=f"fact-change-{len(facts)}",
                    group=FactGroup.CHANGE,
                    text=(
                        f"Compared with the previous run, {delta.label} moved from {delta.before:g} to {delta.after:g}."
                    ),
                    values=values,
                    origin=f"comparison/{section.name}",
                )
            )

    if not facts:
        facts.append(
            SummaryFact(
                id="fact-change-0",
                group=FactGroup.CHANGE,
                text="Nothing measurable changed between this run and the previous one.",
                origin="comparison",
            )
        )
    return facts


# ---------------------------------------------------------------------------


def build_summary_facts(db: Session, run: Run) -> list[SummaryFact]:
    """Every citable fact for this run, in the order the summary answers them."""
    facts = [
        *_quality_facts(db, run),
        *_exploration_facts(db, run),
        *_analytics_facts(db, run),
        *_domain_facts(db, run),
        *_model_facts(db, run),
        *_change_facts(db, run),
    ]
    # Ids must be unique: the grounding filter matches claims against them,
    # and a duplicate would let a claim cite one fact and be validated
    # against another.
    seen: set[str] = set()
    unique: list[SummaryFact] = []
    for fact in facts:
        if fact.id in seen:
            continue
        seen.add(fact.id)
        unique.append(fact)
    return unique
