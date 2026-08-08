// Mirrors the backend's actual response shapes (see backend/app/routers/*.py's
// serialization functions) - kept in sync by hand since there is no shared
// schema generation step, deliberately, to avoid a build-time dependency on
// the backend being reachable just to compile the frontend.

export interface SourceRecord {
  id: string;
  type: "file" | "sql" | "api";
  connection_config: Record<string, unknown> & { original_filename?: string; path?: string };
  created_at: string;
}

export interface RunSummary {
  id: string;
  run_number: number | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

// POST /ingest now returns just {run_id, status: "running"} immediately -
// the graph runs in a background task rather than holding that request
// open for however long narrate_node's LLM calls take (a real bug: a
// browser fetch() has no default timeout, so a slow/backoff-heavy call
// looked exactly like a stuck spinner). GET /ingest/{run_id}/status is the
// poll target: while "running" (or "failed" - re-deriving metadata for a
// run that failed at the connector-fetch step would just fail again) it
// returns the same bare shape; once the run is completed/awaiting_approval
// it returns every field below, identically to what POST /ingest used to
// return synchronously.
// GET /runs (backend/app/routers/runs.py) - the run picker's backing list.
// Ask/Predict used to make a human TYPE a run identifier, which is how a
// valid run number ended up reading as "run '48' not found"; a picker means
// there is nothing to mistype. Deliberately cheap server-side: no
// column/metadata computation per row (that rebuilds the repaired frame),
// so columns for the ONE selected run come from GET /ingest/{run}/status.
export interface RunSummaryRecord {
  id: string;
  run_number: number | null;
  status: string;
  source_id: string;
  source_label: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface IngestResponse {
  run_id: string;
  run_number: number | null;
  status: string;
  metadata?: Record<string, unknown>;
  validation_failure_count?: number;
  baseline?: { id: string; is_active: boolean; is_provisional: boolean } | null;
  baseline_rejected_reasons?: string[] | null;
  findings?: { available: boolean; run_id: string; url: string | null };
  report?: { available: boolean; run_id: string; url: string | null; generation_mode: string | null };
  // Present only when GET was called with ?resolve_id=... - the same
  // RESOLVE-HANG FIX applies to POST /approvals/{id}/resolve for a
  // validation_event (app/routers/approvals.py::_resolve_validation_event
  // resumes this SAME graph, so it can hit narrate_node's LLM chain just
  // like a fresh ingest can). null while the background resume hasn't
  // reached await_human_node for this decision yet; once populated, this
  // is EXACTLY what POST /approvals/{id}/resolve used to return
  // synchronously - shape varies by decision (see
  // app/routers/ingest.py::find_resolution_response).
  resolution?: {
    id: string;
    type: "validation_event";
    decision?: string;
    applied?: boolean;
    error?: string;
    new_baseline_id?: string;
    run_status?: string;
    note?: string;
  } | null;
}

// POST /approvals/{id}/resolve's immediate acknowledgement - the shape
// EVERY decision type returns synchronously (baseline/connector-warning/
// query/model resolutions never touch the graph, so their real outcome is
// already final here), except a validation_event's decision, whose
// status is "resolving" and whose real outcome is polled for via
// IngestResponse.resolution above.
export interface ResolveAckResponse {
  id: string;
  type: string;
  decision?: string;
  status?: string;
  run_id?: string;
  [key: string]: unknown;
}

export interface ProvisionalBaseline {
  id: string;
  source_id: string;
  row_count: number;
  created_at: string;
}

export interface EventGroup {
  resolve_id: string;
  correlation_group_id: string | null;
  run_id: string;
  run_number: number | null;
  rules_failed: string[];
  columns: (string | null)[];
  diagnosis: Record<string, unknown> | null;
  risk_level: string | null;
  gate_reasons: string[] | null;
  action_taken: string | null;
  created_at: string;
}

export interface ConnectorWarning {
  id: string;
  run_id: string;
  run_number: number | null;
  rule_failed: string;
  message: string | null;
  created_at: string;
}

export interface EscalatedQuery {
  id: string;
  source_id: string;
  run_id: string;
  run_number: number | null;
  question: string;
  query_kind: string | null;
  code: string | null;
  escalation_reason: string | null;
  escalation_detail: string | null;
  approvable: boolean;
  created_at: string;
}

export interface EscalatedModel {
  id: string;
  source_id: string;
  run_id: string;
  run_number: number | null;
  question: string | null;
  target_column: string | null;
  task_type: string | null;
  out_of_sample_metric: string | null;
  out_of_sample_score: number | null;
  escalation_reason: string | null;
  escalation_detail: string | null;
  approvable: boolean;
  created_at: string;
}

// Dashboard UX pass, Part 2: last RECENTLY_RESOLVED_LIMIT (10) resolved
// items per section - a read over data that already existed
// (resolved_by/resolved_at, or the equivalent state/is_active fields), so
// "None pending" can be told apart from "nothing has ever happened here".
export interface ResolvedBaseline {
  id: string;
  source_id: string;
  row_count: number;
  decision: string;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface ResolvedValidationEvent {
  id: string;
  run_id: string;
  run_number: number | null;
  rule_failed: string;
  column: string | null;
  decision: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface ResolvedConnectorWarning {
  id: string;
  run_id: string;
  run_number: number | null;
  rule_failed: string;
  message: string | null;
  decision: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface ResolvedQueryRun {
  id: string;
  run_id: string;
  run_number: number | null;
  question: string;
  decision: string;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface ResolvedModelRun {
  id: string;
  run_id: string;
  run_number: number | null;
  target_column: string | null;
  question: string | null;
  decision: string;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface RecentlyResolved {
  provisional_baselines: ResolvedBaseline[];
  validation_events: ResolvedValidationEvent[];
  connector_warnings: ResolvedConnectorWarning[];
  queries: ResolvedQueryRun[];
  models: ResolvedModelRun[];
}

export interface ApprovalsSummary {
  total_resolved: number;
  distinct_runs: number;
}

export interface PendingApprovals {
  provisional_baselines: ProvisionalBaseline[];
  validation_events: EventGroup[];
  escalated_queries: EscalatedQuery[];
  escalated_models: EscalatedModel[];
  connector_warnings: ConnectorWarning[];
  recently_resolved: RecentlyResolved;
  summary: ApprovalsSummary;
}

export type Decision = "approve" | "reject_fix" | "reject_data" | "accept_as_baseline" | "acknowledge";

export interface GroundedClaimRecord {
  claim_id: string;
  claim_text: string;
  finding_ids: string[];
  values: Array<{ label: string; value: number }>;
}

export interface ReportRecord {
  id: string;
  run_id: string;
  run_number: number | null;
  generation_mode: string | null;
  narrative_text: string | null;
  chart_refs: Array<{ chart_id: string; chart_type: string; title: string; figure_json: { data: unknown; layout: unknown } }> | null;
  grounded_claims: GroundedClaimRecord[] | null;
  post_check_results: unknown[] | null;
  delivered_at: string | null;
}

export interface AgentTraceEntry {
  id: string;
  node: string;
  input: unknown;
  output: unknown;
  confidence: number | null;
  edge_taken: string | null;
  timestamp: string;
}

export interface ValidationEventDetail {
  id: string;
  rule_failed: string;
  column: string | null;
  detail: Record<string, unknown> | null;
  state: string;
  against_provisional_baseline: boolean;
  correlation_group_id: string | null;
  correlation_rule: string | null;
  diagnosis: Record<string, unknown> | null;
  risk_level: string | null;
  action_taken: string | null;
  reversal: Record<string, unknown> | null;
  gate_reasons: string[] | null;
  resolved_by: string | null;
  created_at: string;
}

export interface AuditQueryRun {
  id: string;
  question: string;
  query_kind: string | null;
  generated_code: string | null;
  state: string;
  escalation_reason: string | null;
  resolved_by: string | null;
  created_at: string;
}

export interface AuditModelRun {
  id: string;
  question: string | null;
  target_column: string | null;
  task_type: string | null;
  out_of_sample_metric: string | null;
  out_of_sample_score: number | null;
  state: string;
  escalation_reason: string | null;
  resolved_by: string | null;
  created_at: string;
}

export interface AuditRecord {
  run_id: string;
  run_number: number | null;
  source_id: string;
  run_status: string;
  started_at: string | null;
  completed_at: string | null;
  fix_chain: unknown[] | null;
  reveal_depth_reached: number;
  baseline: { id: string; is_active: boolean; is_provisional: boolean } | null;
  trace: AgentTraceEntry[];
  validation_events: ValidationEventDetail[];
  exploration: { id: string; schema_version: number; generated_at: string } | null;
  report: { id: string; generation_mode: string | null; post_check_results: unknown[] | null; delivered_at: string | null } | null;
  query_runs: AuditQueryRun[];
  model_runs: AuditModelRun[];
}

export interface AskResponse {
  id: string;
  status: "answered" | "escalated";
  question: string;
  quality_context_summary: string | null;
  query_kind: string | null;
  code: string | null;
  result: unknown;
  truncated: boolean;
  row_count: number | null;
  columns_referenced: string[];
  assumptions: string[];
  confidence: number | null;
  escalation_reason: string | null;
  escalation_detail: string | null;
  state: string;
}

// POST /predict's immediate ack (dashboard UX pass, Part 1 - the same
// RESOLVE-HANG-style fix ingest/resolve already got) - the real result is
// polled for via GET /models/{id}, below.
export interface PredictAck {
  id: string;
  state: string;
}

export interface CandidateScore {
  model_family: string;
  metric: string;
  out_of_sample_score: number;
  hyperparameters: Record<string, unknown>;
}

export interface BaselineScore {
  model_family: string;
  metric: string;
  out_of_sample_score: number;
}

export interface ExcludedFeature {
  column: string;
  reason: string;
}

export interface ClassDistribution {
  class_counts: Record<string, number>;
  majority_class_share: number;
}

export interface PredictionInterval {
  lower: number | null;
  upper: number | null;
  confidence_level: number | null;
}

export interface FeatureAssociation {
  column: string;
  association_strength: number;
}

export interface ForecastPoint {
  date: string;
  value: number;
  lower: number | null;
  upper: number | null;
}

export interface ForecastSeries {
  historical: ForecastPoint[];
  forecast: ForecastPoint[];
}

// GET /models/{id}'s full shape - what POST /predict used to return
// synchronously, now the poll target (app/routers/predict.py).
export interface PredictResult {
  id: string;
  routed_to?: "prediction";
  status: "answered" | "escalated";
  question: string | null;
  quality_context_summary: string | null;
  target_column: string | null;
  task_type: "forecast" | "classification" | "regression" | "unsupported" | null;
  model_family: string | null;
  hyperparameters: Record<string, unknown>;
  seed: number | null;
  candidate_scores: CandidateScore[];
  baseline_scores: BaselineScore[];
  excluded_features: ExcludedFeature[];
  split_strategy: string | null;
  row_count_trained_on: number | null;
  class_distribution: ClassDistribution | null;
  out_of_sample_metric: string | null;
  out_of_sample_score: number | null;
  prediction_interval: PredictionInterval | null;
  feature_associations: FeatureAssociation[];
  forecast_series: ForecastSeries | null;
  escalation_reason: string | null;
  escalation_detail: string | null;
  // "running" (still training) | "answered" | "awaiting_approval" |
  // "resolved" | "rejected" | "redirected" (turned out to be a retrieval
  // question, see redirected_query_run_id) | "failed" (defense in depth)
  state: string;
  redirected_query_run_id: string | null;
}

// GET /analytics/{run_id} - the Business Analytics Agent's output.
//
// `applicability` is as important as `results`: an analysis that could not
// run carries the precise requirement it was missing, so an empty-looking
// page never leaves a reader guessing whether an analysis found nothing or
// was never eligible.
export interface RoleCandidateRecord {
  column: string;
  role: string;
  score: number;
  confidence: "confirmed" | "high" | "medium" | "low";
  reasons: string[];
  // Role-specific measurements. For `monetary` this carries how much of the
  // column is negative, so a column accepted WITH returns in it never looks
  // identical to one that had none.
  details?: {
    negative_count?: number;
    negative_fraction?: number;
    non_null_count?: number;
    max_negative_fraction?: number;
  };
}

export interface ApplicabilityRecord {
  analysis: string;
  applicable: boolean;
  resolved_columns: Record<string, string>;
  missing_requirements: string[];
  near_misses: Array<{ role: string; column: string; confidence: string; score: number; why_rejected: string }>;
  // The unmet requirements as role names rather than prose - what a
  // "confirm this role" control needs. `missing_requirements` above is the
  // same information written for a reader.
  missing_roles?: Array<{ role: string; description: string }>;
}

export interface AnalysisFindingRecord {
  id: string;
  analysis: string;
  finding_type: string;
  columns: string[];
  // Discriminated on payload.finding_type server-side; the page narrows by
  // that field rather than by analysis name.
  payload: Record<string, unknown>;
  evidence: { sample_size: number; entity_count: number | null; total_value: number | null; parameters: Record<string, unknown> };
}

export interface AnalysisResultRecord {
  analysis: string;
  ran: boolean;
  findings: AnalysisFindingRecord[];
  not_run_reason: string | null;
  parameters: Record<string, unknown>;
}

export interface AnalyticsRecord {
  run_id: string;
  run_number: number | null;
  schema_version: number;
  generated_at: string | null;
  detected_roles: {
    row_count: number;
    minimum_usable_confidence: string;
    assigned: Record<string, RoleCandidateRecord>;
    unconfirmed_candidates: RoleCandidateRecord[];
    // Roles a human confirmed for this run's SOURCE, as {role: column}.
    // Present for both live and stale confirmations, so a confirmation
    // naming a column a later ingest dropped can still be cleared.
    confirmed_roles?: Record<string, string>;
    stale_confirmations?: Array<{ role: string; column: string; why: string }>;
  };
  applicability: ApplicabilityRecord[];
  results: AnalysisResultRecord[];
}
