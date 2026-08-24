"""Comparing two completed runs of one source.

COMPUTED ON READ, NOT PERSISTED. Stated here because it is a design decision
someone will reasonably question.

A comparison is a pure function of two already-persisted artifacts. Storing it
would create a second copy of facts the run rows already hold - the two-sources-
of-truth failure this codebase keeps finding - and would need invalidating,
because a completed run's artifacts are NOT actually frozen: confirming a
column role recomputes that run's analytics in place
(app/analytics/pipeline.py, `replace=True`), and recording a privacy decision
recomputes its classification. A stored comparison would go stale at exactly
the moment someone acts on the system, and nothing would notice.

The cost of recomputing is a handful of JSON reads and dictionary diffs. No
model call, no connector fetch, no frame rebuild. That is cheap enough that
caching it would be optimising the wrong thing.

NOTHING HERE IS CAUSAL. Every output states that a number moved and by how
much. The two runs differ in every uncontrolled way at once, so no comparison
here can support a claim about why.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.comparison.identity import (
    analytics_key,
    describe_exploration,
    exploration_key,
    marketing_key,
)
from app.comparison.models import (
    Comparability,
    Delta,
    Membership,
    RunComparison,
    SectionComparison,
)
from app.models import (
    Baseline,
    BusinessAnalysis,
    ExplorationFinding,
    MarketingAnalysis,
    ModelRun,
    Run,
    ValidationEvent,
)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _run_summary(run: Run) -> dict:
    return {
        "run_id": run.id,
        "run_number": run.run_number,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "source_id": run.source_id,
    }


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def _compare_schema(run_a: Run, run_b: Run) -> SectionComparison:
    types_a = ((run_a.contract_metadata or {}).get("column_types")) or {}
    types_b = ((run_b.contract_metadata or {}).get("column_types")) or {}

    if not types_a or not types_b:
        missing = "the earlier run" if not types_a else "the later run"
        return SectionComparison(
            name="schema",
            comparability=Comparability.ONE_SIDED if (types_a or types_b) else Comparability.ABSENT,
            reason=(
                f"{missing} has no stored schema - it completed before contract metadata was cached, "
                "so the columns it actually had are not recoverable without re-ingesting it"
            ),
        )

    memberships = [
        Membership(label=column, side="b_only", detail={"type": types_b[column]})
        for column in sorted(set(types_b) - set(types_a))
    ] + [
        Membership(label=column, side="a_only", detail={"type": types_a[column]})
        for column in sorted(set(types_a) - set(types_b))
    ]

    changed = {
        column: {"before": types_a[column], "after": types_b[column]}
        for column in sorted(set(types_a) & set(types_b))
        if types_a[column] != types_b[column]
    }

    rows_a = _numeric((run_a.contract_metadata or {}).get("row_count"))
    rows_b = _numeric((run_b.contract_metadata or {}).get("row_count"))

    return SectionComparison(
        name="schema",
        comparability=Comparability.COMPARABLE,
        deltas=[
            Delta(label="row count", before=rows_a, after=rows_b, unit="rows"),
            Delta(label="column count", before=float(len(types_a)), after=float(len(types_b)), unit="columns"),
        ],
        memberships=memberships,
        notes={"type_changes": changed},
    )


# ---------------------------------------------------------------------------
# data quality
# ---------------------------------------------------------------------------

#: Terminal states, grouped as a reader thinks about them.
_AUTO_FIXED_STATES = {"auto_fixed", "resolved"}
_ESCALATED_STATES = {"awaiting_approval", "escalated"}


def _quality_counts(events: list[ValidationEvent]) -> dict:
    fired_by_rule: dict[str, int] = {}
    auto_fixed = 0
    escalated = 0
    for event in events:
        rule = (event.rule_failed or "").split(":")[0]
        fired_by_rule[rule] = fired_by_rule.get(rule, 0) + 1
        if event.action_taken and event.action_taken.startswith("auto_fix") and "reverted" not in event.action_taken:
            auto_fixed += 1
        if event.state in _ESCALATED_STATES:
            escalated += 1
    return {"total": len(events), "by_rule": fired_by_rule, "auto_fixed": auto_fixed, "escalated": escalated}


def _compare_data_quality(db: Session, run_a: Run, run_b: Run) -> SectionComparison:
    events_a = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_a.id).all()
    events_b = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_b.id).all()
    counts_a = _quality_counts(events_a)
    counts_b = _quality_counts(events_b)

    deltas = [
        Delta(label="rules fired", before=float(counts_a["total"]), after=float(counts_b["total"]), unit="events"),
        Delta(label="auto-fixed", before=float(counts_a["auto_fixed"]), after=float(counts_b["auto_fixed"]), unit="events"),
        Delta(label="escalated", before=float(counts_a["escalated"]), after=float(counts_b["escalated"]), unit="events"),
    ]

    memberships: list[Membership] = []
    for rule in sorted(set(counts_b["by_rule"]) - set(counts_a["by_rule"])):
        memberships.append(Membership(label=rule, side="b_only", detail={"count": counts_b["by_rule"][rule]}))
    for rule in sorted(set(counts_a["by_rule"]) - set(counts_b["by_rule"])):
        memberships.append(Membership(label=rule, side="a_only", detail={"count": counts_a["by_rule"][rule]}))

    for rule in sorted(set(counts_a["by_rule"]) & set(counts_b["by_rule"])):
        deltas.append(
            Delta(
                label=f"rule `{rule}`",
                before=float(counts_a["by_rule"][rule]),
                after=float(counts_b["by_rule"][rule]),
                unit="events",
            )
        )

    return SectionComparison(name="data_quality", comparability=Comparability.COMPARABLE, deltas=deltas, memberships=memberships)


# ---------------------------------------------------------------------------
# exploration
# ---------------------------------------------------------------------------

#: Payload fields worth reporting a delta on, per finding type. Anything not
#: listed is compared for presence only - a shape descriptor like "bimodal"
#: is not a number and a delta on it would be meaningless.
_EXPLORATION_NUMERIC_FIELDS = {
    # summary_stat is by far the most common finding type, and it carries the
    # numbers a reader most wants compared: a mean that moved, nulls that
    # appeared, cardinality that collapsed. Omitting it - as a first version
    # of this map did - made the exploration section render a confident,
    # silent "no change" for exactly the columns that changed.
    "summary_stat": (
        "count",
        "null_count",
        "null_rate",
        "cardinality",
        "min",
        "max",
        "mean",
        "median",
        "std",
        "q1",
        "q3",
    ),
    "correlation": ("coefficient", "r", "strength"),
    "trend": ("slope", "change_pct", "direction_strength"),
    "missing_pattern": ("null_fraction", "missing_fraction", "null_rate"),
    "outlier_cluster": ("count", "fraction", "share"),
    "distribution_shape": ("skew", "kurtosis"),
    "cardinality_note": ("cardinality", "unique_ratio", "row_count"),
}


def _index_exploration(record: ExplorationFinding | None) -> dict[tuple, dict]:
    if record is None:
        return {}
    findings = (record.findings_json or {}).get("findings") or []
    return {exploration_key(f): f for f in findings if isinstance(f, dict)}


def _compare_exploration(db: Session, run_a: Run, run_b: Run) -> SectionComparison:
    record_a = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_a.id).one_or_none()
    record_b = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_b.id).one_or_none()

    if record_a is None and record_b is None:
        return SectionComparison(
            name="exploration",
            comparability=Comparability.ABSENT,
            reason="neither run has exploration findings - exploration runs only on a run that reaches completed",
        )
    if record_a is None or record_b is None:
        which = "the later run" if record_a is not None else "the earlier run"
        return SectionComparison(
            name="exploration",
            comparability=Comparability.ONE_SIDED,
            reason=f"{which} has no exploration findings, so there is nothing to compare against",
        )

    index_a = _index_exploration(record_a)
    index_b = _index_exploration(record_b)

    memberships = [
        Membership(label=describe_exploration(index_b[key]), side="b_only", detail={"finding_type": index_b[key].get("finding_type")})
        for key in sorted(set(index_b) - set(index_a), key=str)
    ] + [
        Membership(label=describe_exploration(index_a[key]), side="a_only", detail={"finding_type": index_a[key].get("finding_type")})
        for key in sorted(set(index_a) - set(index_b), key=str)
    ]

    deltas: list[Delta] = []
    kind_changes: list[dict] = []
    for key in sorted(set(index_a) & set(index_b), key=str):
        finding_type = str(index_a[key].get("finding_type", ""))
        fields = _EXPLORATION_NUMERIC_FIELDS.get(finding_type, ())
        payload_a = index_a[key].get("payload") or {}
        payload_b = index_b[key].get("payload") or {}
        # A column that changed kind (numeric -> categorical, because the
        # source started sending "1,234" as text) has summary fields that are
        # not the same measurement. Reported as a note, and its numbers are
        # skipped rather than differenced.
        kind_a, kind_b = payload_a.get("kind"), payload_b.get("kind")
        if kind_a != kind_b:
            kind_changes.append(
                {"finding": describe_exploration(index_a[key]), "before": kind_a, "after": kind_b}
            )
            continue

        for field_name in fields:
            before = _numeric(payload_a.get(field_name))
            after = _numeric(payload_b.get(field_name))
            if before is None and after is None:
                continue
            deltas.append(Delta(label=f"{describe_exploration(index_a[key])} - {field_name}", before=before, after=after))

    return SectionComparison(
        name="exploration",
        comparability=Comparability.COMPARABLE,
        deltas=deltas,
        memberships=memberships,
        notes={"findings_a": len(index_a), "findings_b": len(index_b), "kind_changes": kind_changes},
    )


# ---------------------------------------------------------------------------
# analytics - the section with the real comparability trap
# ---------------------------------------------------------------------------

#: Payload fields worth a delta, by analytics finding type.
_ANALYTICS_NUMERIC_FIELDS = (
    "entity_count",
    "entity_share",
    "value_total",
    "value_share",
    "cumulative_value_share",
    "retention_rate",
    "repeat_rate",
    "historical_value_per_entity",
    "median_value",
    "size",
    "share",
    "support",
    "confidence",
    "lift",
)


def _baseline_superseded_between(db: Session, run_a: Run, run_b: Run) -> Baseline | None:
    """A baseline created strictly between the two runs' start times.

    Inferred from creation timestamps rather than read from a recorded link:
    a Run does not store which Baseline it validated against. That is a real
    limitation of this check and it errs toward reporting a supersession,
    which is the safe direction - a spurious "not comparable" costs a reader
    one sentence, a missed one costs them a wrong conclusion.
    """
    if run_a.started_at is None or run_b.started_at is None:
        return None
    earlier, later = sorted([run_a, run_b], key=lambda r: r.started_at)
    return (
        db.query(Baseline)
        .filter(
            Baseline.source_id == run_a.source_id,
            Baseline.created_at > earlier.started_at,
            Baseline.created_at < later.started_at,
        )
        .order_by(Baseline.created_at)
        .first()
    )


def _value_basis_label(payload: dict) -> str | None:
    definition = payload.get("value_definition")
    if isinstance(definition, dict):
        return definition.get("label") or definition.get("monetary_column")
    return None


def _compare_analytics(db: Session, run_a: Run, run_b: Run) -> SectionComparison:
    record_a = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run_a.id).one_or_none()
    record_b = db.query(BusinessAnalysis).filter(BusinessAnalysis.run_id == run_b.id).one_or_none()

    if record_a is None and record_b is None:
        return SectionComparison(
            name="analytics",
            comparability=Comparability.ABSENT,
            reason="neither run has business analytics",
        )
    if record_a is None or record_b is None:
        which = "the later run" if record_a is not None else "the earlier run"
        return SectionComparison(
            name="analytics",
            comparability=Comparability.ONE_SIDED,
            reason=f"{which} has no business analytics, so there is nothing to compare against",
        )

    payload_a = record_a.findings_json or {}
    payload_b = record_b.findings_json or {}
    basis_a = _value_basis_label(payload_a)
    basis_b = _value_basis_label(payload_b)
    notes = {"value_basis_a": basis_a, "value_basis_b": basis_b}

    # THE TRAP. Every value figure below - Pareto share, CLV, segment value -
    # is a sum over the value basis. A unit-price basis and a price x quantity
    # basis differ by orders of magnitude and both look reasonable in
    # isolation, so a delta between them is not a small inaccuracy: it is a
    # number with no meaning, displayed with the authority of a measurement.
    if basis_a != basis_b:
        return SectionComparison(
            name="analytics",
            comparability=Comparability.NOT_COMPARABLE,
            reason=(
                f"the two runs used different value bases - the earlier run summed {basis_a!r} and the later "
                f"run summed {basis_b!r}. Every value figure here (Pareto share, segment value, historical "
                "value per entity) is a sum over that basis, so a delta between them would compare two "
                "different quantities. Re-run the earlier source with the same basis to compare them."
            ),
            notes=notes,
        )

    superseded = _baseline_superseded_between(db, run_a, run_b)
    if superseded is not None:
        return SectionComparison(
            name="analytics",
            comparability=Comparability.NOT_COMPARABLE,
            reason=(
                f"a baseline was superseded between these runs (baseline {superseded.id} created "
                f"{superseded.created_at.isoformat()}). The later run's figures are measured against a "
                "different definition of normal, so a delta would mix a change in the data with a change "
                "in what it was compared to."
            ),
            notes={**notes, "superseding_baseline_id": superseded.id},
        )

    results_a = {r.get("analysis"): r for r in (payload_a.get("results") or []) if isinstance(r, dict)}
    results_b = {r.get("analysis"): r for r in (payload_b.get("results") or []) if isinstance(r, dict)}

    deltas: list[Delta] = []
    memberships: list[Membership] = []

    for analysis in sorted(set(results_a) | set(results_b), key=str):
        entry_a = results_a.get(analysis)
        entry_b = results_b.get(analysis)
        ran_a = bool(entry_a and entry_a.get("ran"))
        ran_b = bool(entry_b and entry_b.get("ran"))

        # "This analysis ran in one run only" is membership, never a delta
        # against nothing.
        if ran_a != ran_b:
            side = "b_only" if ran_b else "a_only"
            other = entry_a if ran_b else entry_b
            memberships.append(
                Membership(
                    label=f"analysis `{analysis}`",
                    side=side,
                    detail={"reason_not_run": (other or {}).get("reason") or (other or {}).get("requirement")},
                )
            )
            continue
        if not ran_a:
            continue

        index_a = {analytics_key(analysis, f): f for f in (entry_a.get("findings") or []) if isinstance(f, dict)}
        index_b = {analytics_key(analysis, f): f for f in (entry_b.get("findings") or []) if isinstance(f, dict)}

        for key in sorted(set(index_b) - set(index_a), key=str):
            memberships.append(Membership(label=f"{analysis}: {key[1]} {key[3]}".strip(), side="b_only", detail={}))
        for key in sorted(set(index_a) - set(index_b), key=str):
            memberships.append(Membership(label=f"{analysis}: {key[1]} {key[3]}".strip(), side="a_only", detail={}))

        for key in sorted(set(index_a) & set(index_b), key=str):
            pa = index_a[key].get("payload") or {}
            pb = index_b[key].get("payload") or {}
            label_stem = f"{analysis}: {key[1]}" + (f" {key[3]}" if key[3] else "")
            for field_name in _ANALYTICS_NUMERIC_FIELDS:
                before = _numeric(pa.get(field_name))
                after = _numeric(pb.get(field_name))
                if before is None and after is None:
                    continue
                deltas.append(
                    Delta(
                        label=f"{label_stem} - {field_name}",
                        before=before,
                        after=after,
                        basis=basis_a if "value" in field_name else None,
                    )
                )

    return SectionComparison(
        name="analytics",
        comparability=Comparability.COMPARABLE,
        deltas=deltas,
        memberships=memberships,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# marketing
# ---------------------------------------------------------------------------

_MARKETING_NUMERIC_FIELDS = ("observed", "observed_value", "threshold", "value", "median", "share")


def _compare_marketing(db: Session, run_a: Run, run_b: Run) -> SectionComparison:
    record_a = db.query(MarketingAnalysis).filter(MarketingAnalysis.run_id == run_a.id).one_or_none()
    record_b = db.query(MarketingAnalysis).filter(MarketingAnalysis.run_id == run_b.id).one_or_none()

    if record_a is None and record_b is None:
        return SectionComparison(
            name="marketing",
            comparability=Comparability.ABSENT,
            reason="neither run has a marketing analysis",
        )

    payload_a = (record_a.findings_json if record_a else None) or {}
    payload_b = (record_b.findings_json if record_b else None) or {}
    qualifies_a = bool(payload_a.get("applicable"))
    qualifies_b = bool(payload_b.get("applicable"))

    # A marketing row is written even when the run does not qualify, so
    # "both have a row" is not "both qualify" - the distinction the spec
    # calls for.
    if not (qualifies_a and qualifies_b):
        if not qualifies_a and not qualifies_b:
            return SectionComparison(
                name="marketing",
                comparability=Comparability.ABSENT,
                reason="neither run qualifies as an ad-platform export, so there is no marketing pack to compare",
            )
        which = "the earlier run" if qualifies_b else "the later run"
        missing = (payload_a if not qualifies_a else payload_b).get("missing_roles") or []
        return SectionComparison(
            name="marketing",
            comparability=Comparability.ONE_SIDED,
            reason=(
                f"only one of these runs qualifies for the marketing pack - {which} is missing "
                f"{', '.join(str(r) for r in missing) or 'a required role'}"
            ),
            notes={"qualifies_a": qualifies_a, "qualifies_b": qualifies_b},
        )

    index_a = {marketing_key(f): f for f in (payload_a.get("findings") or []) if isinstance(f, dict)}
    index_b = {marketing_key(f): f for f in (payload_b.get("findings") or []) if isinstance(f, dict)}

    memberships = [
        Membership(label=" ".join(str(p) for p in key if p), side="b_only", detail={})
        for key in sorted(set(index_b) - set(index_a), key=str)
    ] + [
        Membership(label=" ".join(str(p) for p in key if p), side="a_only", detail={})
        for key in sorted(set(index_a) - set(index_b), key=str)
    ]

    deltas: list[Delta] = []
    for key in sorted(set(index_a) & set(index_b), key=str):
        pa = index_a[key].get("payload") or {}
        pb = index_b[key].get("payload") or {}
        stem = " ".join(str(p) for p in key if p)
        for field_name in _MARKETING_NUMERIC_FIELDS:
            before = _numeric(pa.get(field_name))
            after = _numeric(pb.get(field_name))
            if before is None and after is None:
                continue
            deltas.append(
                Delta(
                    label=f"{stem} - {field_name}",
                    before=before,
                    after=after,
                    basis=str(pa.get("compared_against") or "") or None,
                )
            )

    return SectionComparison(
        name="marketing",
        comparability=Comparability.COMPARABLE,
        deltas=deltas,
        memberships=memberships,
    )


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


def _latest_model(db: Session, run: Run) -> ModelRun | None:
    return (
        db.query(ModelRun)
        .filter(ModelRun.run_id == run.id, ModelRun.out_of_sample_score.isnot(None))
        .order_by(ModelRun.created_at.desc())
        .first()
    )


def _compare_model(db: Session, run_a: Run, run_b: Run) -> SectionComparison:
    model_a = _latest_model(db, run_a)
    model_b = _latest_model(db, run_b)

    if model_a is None and model_b is None:
        return SectionComparison(
            name="model",
            comparability=Comparability.ABSENT,
            reason="neither run has a scored model",
        )
    if model_a is None or model_b is None:
        which = "the later run" if model_a is not None else "the earlier run"
        return SectionComparison(
            name="model",
            comparability=Comparability.ONE_SIDED,
            reason=f"{which} has no scored model, so there is nothing to compare against",
        )

    # A score is only meaningful against the same target and the same metric.
    if model_a.target_column != model_b.target_column or model_a.out_of_sample_metric != model_b.out_of_sample_metric:
        return SectionComparison(
            name="model",
            comparability=Comparability.NOT_COMPARABLE,
            reason=(
                f"the two models are not the same measurement - the earlier run scored "
                f"{model_a.target_column!r} by {model_a.out_of_sample_metric}, the later run scored "
                f"{model_b.target_column!r} by {model_b.out_of_sample_metric}"
            ),
        )

    def _best_baseline(model: ModelRun) -> float | None:
        scores = model.baseline_scores_json or {}
        values = [_numeric(v) for v in scores.values()] if isinstance(scores, dict) else []
        values = [v for v in values if v is not None]
        return max(values) if values else None

    return SectionComparison(
        name="model",
        comparability=Comparability.COMPARABLE,
        deltas=[
            Delta(
                label=f"out-of-sample {model_a.out_of_sample_metric}",
                before=_numeric(model_a.out_of_sample_score),
                after=_numeric(model_b.out_of_sample_score),
                basis=f"target `{model_a.target_column}`",
            ),
            Delta(
                label="best baseline score",
                before=_best_baseline(model_a),
                after=_best_baseline(model_b),
                basis=f"target `{model_a.target_column}`",
            ),
        ],
        notes={"model_family_a": model_a.model_family, "model_family_b": model_b.model_family},
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def compare_runs(db: Session, run_a: Run, run_b: Run) -> RunComparison:
    """Two runs, side by side. `run_a` is treated as the earlier reference."""
    summary_a, summary_b = _run_summary(run_a), _run_summary(run_b)

    if run_a.id == run_b.id:
        return RunComparison(run_a=summary_a, run_b=summary_b, blocked_reason="a run cannot be compared with itself")

    if run_a.source_id != run_b.source_id:
        return RunComparison(
            run_a=summary_a,
            run_b=summary_b,
            blocked_reason=(
                "these runs are of different sources. A comparison would report every column, rule and "
                "finding as changed, which describes two unrelated datasets rather than a change over time."
            ),
        )

    incomplete = [r.run_number or r.id for r in (run_a, run_b) if r.status != "completed"]
    if incomplete:
        return RunComparison(
            run_a=summary_a,
            run_b=summary_b,
            blocked_reason=(
                f"run(s) {incomplete} did not reach completed. Exploration and analytics run only on a "
                "completed run, so a comparison would be against partial output."
            ),
        )

    return RunComparison(
        run_a=summary_a,
        run_b=summary_b,
        sections=[
            _compare_schema(run_a, run_b),
            _compare_data_quality(db, run_a, run_b),
            _compare_exploration(db, run_a, run_b),
            _compare_analytics(db, run_a, run_b),
            _compare_marketing(db, run_a, run_b),
            _compare_model(db, run_a, run_b),
        ],
    )
