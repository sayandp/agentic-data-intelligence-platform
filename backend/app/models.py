from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String, nullable=False)
    connection_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("data_sources.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Ordered list of committed fixes: [{"action": str, "spec": dict}, ...].
    # This is the repaired frame's *recipe*, not a mutated copy of the data -
    # replaying it against a base frame (app/repair.py) is what "the repaired
    # frame" means, deterministically and without duplicate storage. Only
    # successful (post-condition-verified) fixes are appended; a reverted
    # attempt never becomes part of this chain.
    fix_chain: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Set only when this run reached awaiting_approval against a mutable
    # source (source_immutable=False - SQL, API). Points at a Parquet file
    # (app/run_snapshots.py) holding the exact raw frame this run's diagnoses
    # were made against, so a later approval replays fix_chain against what
    # was actually seen instead of re-fetching a source that may have changed
    # since. Null for file sources (re-fetching is already reproducible) and
    # for runs that never paused.
    snapshot_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # Deepest reveal level app/resolution.py::process_queue actually reached
    # for this run (0 = every processed group came from the initial
    # validate() pass, nothing was revealed). Not "how many reveals
    # happened" - the max DEPTH of the chain, which is what the cap
    # (REVEAL_DEPTH_CAP) bounds. Kept even when the cap was never hit, so a
    # run that legitimately never reveals anything is distinguishable from
    # one that was never checked.
    reveal_depth_reached: Mapped[int] = mapped_column(nullable=False, default=0)
    # The contract metadata (row_count, column_types, encoding) as it was
    # for THIS run, cached at completion.
    #
    # It used to be recomputed on every GET /ingest/{run}/status by
    # rebuilding the repaired frame through the connector - O(source size),
    # measured at 30.9s on a 94MB CSV. That is a poll target, and it is also
    # what Ask/Predict/Analytics read to offer column hints, so on a large
    # source those screens simply never showed their columns and the status
    # poll held a DB connection for half a minute at a time.
    #
    # Null for a run that completed before this column existed; the
    # serializer falls back to recomputing in that case, so nothing breaks.
    contract_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Semantic column roles (app/semantic_roles.py), detected ONCE when the
    # run completes and read by every downstream agent - exploration's
    # exclusions, and the role context handed to Query/Modeling/Narrative.
    # Detection used to happen only inside the analytics agent, so everyone
    # else saw dtypes and correlated invoice numbers against prices.
    #
    # Null for a run that completed before this column existed; every reader
    # treats an absent document as "no roles known" and behaves as it did
    # before, so old runs keep working.
    semantic_roles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Which columns hold personal data (app/privacy/), detected ONCE when the
    # run completes, from the same repaired frame the roles come from. Read at
    # every egress boundary to decide what is masked before leaving for a
    # third-party model.
    #
    # Null for a run that completed before this column existed; an absent
    # classification redacts nothing and behaves exactly as the system did
    # before, so old runs keep working.
    privacy_classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Dashboard UX pass: a short, human-typeable alias for `id` - "Run #17"
    # instead of a UUID. `id` stays the only real primary key (nothing about
    # foreign keys, joins, or the graph's checkpoint thread_id changes); this
    # is a second, independently-unique column assigned once at creation
    # (app/routers/ingest.py::next_run_number) and never reused or reassigned.
    # Nullable at the schema level only because SQLite can't add a NOT NULL
    # column with no default to a table that already has rows in one ALTER
    # TABLE statement (app/db.py's migration adds the column, then backfills
    # every existing row in creation order) - from that point on the
    # application guarantees it's always set, the same "additive, nullable"
    # shape as AgentTrace.edge_taken above.
    run_number: Mapped[int | None] = mapped_column(unique=True, nullable=True)


class Baseline(Base):
    """The stored 'normal' a later ingest is compared against.

    Superseding a baseline sets the old row's is_active to False rather than
    deleting it, so the profile history stays available for audit.

    is_provisional marks a baseline established automatically from a source's
    first successful ingest, with no human confirmation that the data it was
    built from was actually clean (trust-on-first-use). It stays is_active=True
    so validation runs against it immediately, but a human should confirm it
    via GET/POST /approvals before it's trusted the way an explicitly
    recomputed baseline is.
    """

    __tablename__ = "baselines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("data_sources.id"), nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationEvent(Base):
    """Written by the Data-Quality Agent's validation engine (Phase 2 Part 3).

    diagnosis_json/risk_level/action_taken stay null until the Diagnostic Agent
    and Action Executor (Part 4/5) process the event. `state` is the lifecycle
    position (app/state_machine.py): detected -> diagnosed ->
    (auto_fixed | awaiting_approval) -> (resolved | rejected).
    """

    __tablename__ = "validation_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    rule_failed: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="detected")
    column_name: Mapped[str | None] = mapped_column(String, nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # True when this event was raised against a baseline that hasn't been
    # human-confirmed yet (Baseline.is_provisional at validation time). A
    # failure here may just mean the *baseline* is wrong, not the data.
    against_provisional_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set by the deterministic pre-diagnosis correlator (app/correlation.py)
    # when this event was grouped with others as one candidate fix (e.g. a
    # missing_column + unexpected_column pair treated as a rename). Events
    # sharing a correlation_group_id get one diagnosis call, one gate
    # decision, and one action between them - null means diagnosed alone.
    correlation_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    diagnosis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    action_taken: Mapped[str | None] = mapped_column(String, nullable=True)
    # Populated whenever a fix was actually attempted (auto or human-approved),
    # whether it stuck or was reverted - sufficient to undo it. Never
    # populated for events that were escalated without an attempt.
    reversal_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Why the gate did what it did (matrix/allowlist/risk/confidence checks),
    # for audit - a human looking at an escalated event should see exactly
    # which condition(s) failed, not just that it did.
    gate_reasons_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    # Dashboard UX pass, Part 2 (RECENTLY RESOLVED): the OTHER three
    # approval-bearing models (Baseline, QueryRun, ModelRun) already record
    # when a human resolution happened; ValidationEvent recorded who
    # (resolved_by) but never when, which the "Recently resolved" list needs
    # to order and display by. Set alongside every existing resolved_by
    # assignment (app/resolution.py, app/routers/approvals.py's connector-
    # warning path) - never for AUTO_FIXED, which isn't a human resolution.
    # Additive/nullable/never backfilled for pre-existing rows, same as
    # AgentTrace.edge_taken above - there is no historical timestamp to
    # recover for an event resolved before this column existed.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 8 Part 2: which edge the graph took OUT of this node - null for
    # every trace written before the graph migration (additive, nullable,
    # never backfilled) and for traces that aren't a graph node's own
    # routing decision (e.g. a per-group "diagnosis"/"gate" trace, which
    # doesn't correspond 1:1 with a graph edge). This is what makes
    # GET /audit/{run_id} able to say WHY the graph branched, not just which
    # nodes ran - a trace showing nodes but not edges is not an audit trail.
    edge_taken: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExplorationFinding(Base):
    """Written by the Exploration Agent (Phase 4) - one row per run, only
    ever for a run that reached 'completed' (never awaiting_approval or
    failed; see app/exploration/pipeline.py). findings_json is the
    JSON-serialized ExplorationFindings (app/exploration/findings.py),
    schema_version duplicated out of it as its own column so a consumer can
    filter/migrate by version without deserializing the payload.
    """

    __tablename__ = "exploration_findings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    findings_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BusinessAnalysis(Base):
    """Written by the Business Analytics Agent - one row per run, only ever
    for a run that reached 'completed', exactly like ExplorationFinding.

    findings_json is the JSON-serialized BusinessAnalyticsFindings
    (app/analytics/findings.py). It carries the applicability report as well
    as the results, because "this analysis did not run, and here is the
    precise requirement it was missing" is a first-class part of the output,
    not a debugging aid - a user looking at an empty analytics page must
    never have to guess whether an analysis found nothing or was never
    eligible.

    schema_version is duplicated out of the payload into its own column so a
    consumer can filter or migrate by version without deserializing, the
    same choice ExplorationFinding made.
    """

    __tablename__ = "business_analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    findings_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConfirmedColumnRole(Base):
    """A semantic column role a HUMAN confirmed, scoped to the SOURCE.

    The detector (app/analytics/roles.py) never promotes a low-confidence
    candidate on its own - the standing rule is to stay silent rather than
    guess. This table is the other half of that bargain: the place a
    person's decision is recorded so the question is asked once, not on
    every ingest of the same file.

    Keyed by source rather than by run deliberately. A run is one pass over
    a source; the fact that `total_spend` is the monetary column is a
    property of the SOURCE's shape and survives re-ingest, which is exactly
    what makes a second ingest not re-ask.

    Rows carry the confirming column name verbatim. A later ingest whose
    frame no longer has that column does NOT fail - the confirmation is
    skipped and reported as stale, because a source can legitimately change
    shape and a stored answer to a question about a vanished column is
    simply no longer applicable.
    """

    __tablename__ = "confirmed_column_roles"
    __table_args__ = (UniqueConstraint("source_id", "role", name="uq_confirmed_role_per_source"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("data_sources.id"), nullable=False)
    #: An app.analytics.roles.ColumnRole value. Stored as its string so this
    #: table does not import the analytics package.
    role: Mapped[str] = mapped_column(String, nullable=False)
    column_name: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MarketingAnalysis(Base):
    """Written by the Marketing Agent - one row per run, only ever for a run
    that reached 'completed', exactly like BusinessAnalysis.

    A row is written EVEN WHEN THE RUN DOES NOT QUALIFY. "This source is not
    an ads export, and here is the role it was missing" is the output a user
    needs when the Marketing tab is absent; a missing row would leave them
    unable to tell a refusal from a failure.
    """

    __tablename__ = "marketing_analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    findings_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QueryRun(Base):
    """Written by the Query Agent (Phase 6) - one row per POST /ask call.

    `state` reuses app/state_machine.py's AWAITING_APPROVAL/RESOLVED/REJECTED
    constants and its transition() validator directly (Part 5: "surfaced
    through the existing approvals mechanism. Reuse it") - an escalated
    query goes through the exact same lifecycle a validation event does,
    just starting one step further along and reaching only a subset of the
    resolution vocabulary (see app/routers/approvals.py). "answered" is not
    part of that shared state machine - a query that was answered directly
    never needed approval and is simply terminal from the moment it's
    written.
    """

    __tablename__ = "query_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("data_sources.id"), nullable=False)
    # The run whose repaired frame / quality context this question was
    # answered against - always set: POST /ask resolves a bare source_id to
    # its latest completed run before doing anything else (app/query/pipeline.py).
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # "sql" | "pandas" | "unanswerable" | null (null only if escalated before
    # a query_kind was ever assigned - never happens in practice, since kind
    # is assigned deterministically before generation, but the column stays
    # nullable rather than defaulting to a value that didn't actually occur).
    query_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    generated_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    columns_referenced_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    assumptions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "answered" | "awaiting_approval" | "resolved" | "rejected"
    state: Mapped[str] = mapped_column(String, nullable=False, default="answered")
    # Closed EscalationReason value (app/query/models.py) - null for a
    # directly-answered query, always set for an escalated one.
    escalation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    escalation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The sandbox's JSON-safe result payload - null until/unless a resolved
    # escalation actually runs the code (app/routers/approvals.py's
    # low_confidence-only approve path).
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    quality_context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelRun(Base):
    """Written by the Modeling Agent (Phase 7) - one row per POST /predict call
    that reaches training, plus every escalation along the way.

    `state` reuses app/state_machine.py's AWAITING_APPROVAL/RESOLVED/REJECTED
    constants and its transition() validator directly, same reasoning as
    QueryRun above: an escalated model goes through the exact same lifecycle
    a validation event does. "answered" is not part of that shared state
    machine - a model that trained and was reported directly never needed
    approval and is simply terminal from the moment it's written.

    Every field the spec requires for reproducibility/audit is recorded here
    rather than only in result_json, so it can be filtered/queried without
    deserializing the payload - mirroring ExplorationFinding.schema_version's
    reasoning.
    """

    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("data_sources.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_column: Mapped[str | None] = mapped_column(String, nullable=True)
    # "forecast" | "classification" | "regression" | "unsupported" | null
    # (null only if escalated before a task type could be determined at all,
    # e.g. the target column itself doesn't exist).
    task_type: Mapped[str | None] = mapped_column(String, nullable=True)
    model_family: Mapped[str | None] = mapped_column(String, nullable=True)
    hyperparameters_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    seed: Mapped[int | None] = mapped_column(nullable=True)
    # list[dict] of CandidateScore (app/modeling/models.py) - every candidate
    # family's out-of-sample score, not just the winner's.
    candidate_scores_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    baseline_scores_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # list[dict] of ExcludedFeature - every feature Part 3 dropped and why.
    excluded_features_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    split_strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    row_count_trained_on: Mapped[int | None] = mapped_column(nullable=True)
    class_distribution_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    out_of_sample_metric: Mapped[str | None] = mapped_column(String, nullable=True)
    out_of_sample_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_interval_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # list[dict] of FeatureAssociation - deliberately never "importance" or
    # "cause" in the field name itself; see app/modeling/models.py's module
    # docstring for why (same rule as ExplorationFinding).
    feature_associations_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "running" (RESOLVE-HANG-style fix, dashboard UX pass Part 1: this row
    # is created as a placeholder immediately, before training starts, so
    # POST /predict never holds the request open for a slow CV/training
    # loop - see app/routers/predict.py) | "redirected" (the question was
    # actually retrieval-intent; see redirected_query_run_id below,
    # never both this and a real task_type/model_family) | "answered" |
    # "awaiting_approval" | "resolved" | "rejected"
    state: Mapped[str] = mapped_column(String, nullable=False, default="answered")
    escalation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    escalation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Set only when intent classification (app/graph/predict_graph.py::
    # classify_node) determined this "predict" request was actually a
    # retrieval question and routed it to the Query Agent instead - this
    # placeholder ModelRun row is never filled in with a task_type/model in
    # that case, it's simply marked "redirected" so a poller doesn't wait
    # forever for fields that were never going to arrive.
    redirected_query_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Chart-ready data for a FORECAST task only (app/modeling/models.py::
    # ForecastSeries) - historical actuals + a forward horizon with a
    # per-step interval band, computed deterministically alongside training
    # (never LLM-touched, same "chart shape decided by data/model output
    # alone" rule app/narrative/charts.py already follows). Null for every
    # non-forecast task type.
    forecast_series_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Report(Base):
    """Written by the Narrative Agent (Phase 5) - one row per run, only
    ever for a run that has an ExplorationFinding (i.e. reached
    'completed'; see app/narrative/pipeline.py). narrative_text is the
    FULLY rendered document - quality context first, then the narrative
    body, then a labelled recommendations section
    (NarrativeReport.rendered_text) - never just the LLM's raw prose.
    """

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    narrative_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # list[dict] of ChartRef (app/narrative/models.py), each recording its
    # chart_type, the finding_ids it visualizes, and the Plotly figure spec.
    chart_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # list[dict] of GroundedClaim - the full grounding trail behind
    # narrative_text, persisted regardless of generation_mode (a template
    # report's claims are just its findings restated 1:1, still worth
    # keeping for audit).
    grounded_claims_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "llm" | "generation_mode" (app/narrative/models.py::GenerationMode) -
    # "template" means the LLM was unavailable, produced no usable claims,
    # or the generated prose failed post-checks twice; the run never fails
    # for lack of an LLM (Part 4), it degrades to this instead.
    generation_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    # list[dict] of PostCheckAttempt (app/narrative/models.py) - one entry
    # per generation attempt (up to two), each with per-check pass/fail and
    # precisely what failed and where. Persisted even when generation_mode
    # is "template", so a two-failed-attempts fallback is auditable, not
    # just asserted.
    post_check_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
