import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, apiPostJson, pollIngestStatus } from "../api/client";
import type { Decision, PendingApprovals, ResolveAckResponse } from "../api/types";
import { Badge, Button, Card, CodeBlock, CopyableId, ErrorMessage, Muted, RunLabel, Toast } from "../components/ui";
import { auditLink, reportLink } from "../lib/runLinks";
import { useToast } from "../hooks/useToast";

// Part 2: every one of these items already knows the run_id it belongs to -
// killing the copy-paste workflow means jumping straight to that run's
// report/audit rather than making a human paste an id they'd have to go
// find first.
function RunLinks({ runId, runNumber }: { runId: string; runNumber: number | null }) {
  return (
    <span className="text-xs">
      <Link className="text-brand-600 hover:underline" to={reportLink(runId, runNumber)}>
        View report
      </Link>{" "}
      &middot;{" "}
      <Link className="text-brand-600 hover:underline" to={auditLink(runId, runNumber)}>
        View audit
      </Link>
    </span>
  );
}

// Dashboard UX pass, Part 2: collapsed by default (Part 5's same
// progressive-disclosure pattern as Audit's JSON expanders) - useful
// history, not something that should dominate a page whose main job is
// showing what still needs a decision.
function RecentlyResolved({ count, children }: { count: number; children: React.ReactNode }) {
  if (count === 0) return null;
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-sm font-medium text-brand-600">Recently resolved ({count})</summary>
      <div className="mt-3 flex flex-col gap-2">{children}</div>
    </details>
  );
}

//: Above this many entries the list is collapsed by default. Provisional
//: baselines are informational and accumulate one per source per first
//: ingest, so a long list is normal - and a long NORMAL list pushing the
//: things that actually need a decision off the screen is the problem.
const BASELINE_COLLAPSE_THRESHOLD = 10;

/** Collapses a long informational list, with the count in the summary so
 *  nothing is hidden - a reviewer can see how much is in there without
 *  scrolling past it. Short lists stay open: collapsing three rows would
 *  add a click and save no space. */
function CollapsibleList({ count, noun, children }: { count: number; noun: string; children: React.ReactNode }) {
  if (count <= BASELINE_COLLAPSE_THRESHOLD) return <>{children}</>;
  return (
    <details>
      <summary className="cursor-pointer text-sm font-medium text-brand-600">
        Show {count} {noun}s
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

function ResolvedMeta({ decision, resolvedBy, resolvedAt }: { decision: string | null; resolvedBy: string | null; resolvedAt: string | null }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
      {decision && <Badge value={decision} />}
      {resolvedBy && <span>by {resolvedBy}</span>}
      {resolvedAt && <span>{resolvedAt}</span>}
    </div>
  );
}

const DECISION_LABELS: Record<Decision, string> = {
  approve: "Approving...",
  reject_fix: "Rejecting fix...",
  reject_data: "Rejecting data...",
  accept_as_baseline: "Accepting as baseline...",
  acknowledge: "Acknowledging...",
};

const DECISION_PAST_TENSE: Record<Decision, string> = {
  approve: "approved",
  reject_fix: "kept as-is (no fix applied)",
  reject_data: "discarded permanently - run failed",
  accept_as_baseline: "accepted as the new baseline",
  acknowledge: "acknowledged",
};

// Provisional-baseline rejection reuses the reject_data decision (see
// BASELINE_DECISIONS in app/routers/approvals.py - approve/reject_data are
// the only two meaningful decisions for a baseline candidate), but nothing
// about a "run" is discarded or failed when a baseline is rejected - the
// generic validation-event copy above would say exactly that, wrongly, so
// this section gets its own wording instead of sharing DECISION_PAST_TENSE.
const BASELINE_DECISION_PAST_TENSE: Partial<Record<Decision, string>> = {
  approve: "confirmed as the active baseline",
  reject_data: "rejected - this candidate will never become the baseline",
};

// Part 3: an empty section should say which of two different things is
// true - "this has never happened" (the trigger just hasn't occurred yet)
// vs. "this happened and is already handled" (something resolved sits in
// Recently resolved below) - not the same flat "nothing here" for both.
function EmptyState({ neverText, resolvedCount, triggerText }: { neverText: string; resolvedCount: number; triggerText: string }) {
  return (
    <Muted>
      {resolvedCount > 0
        ? `Nothing pending right now - ${resolvedCount} resolved below. ${triggerText}`
        : `${neverText} ${triggerText}`}
    </Muted>
  );
}

const DISCARD_CONFIRM_MESSAGE =
  "Discard this run permanently?\n\n" +
  "This fails the run outright. Unlike every other decision here, there is " +
  "nothing to undo afterward - no fix chain to replay, no baseline history " +
  "to fall back on, no reversal record. This is the one action in the " +
  "system with no recovery path.";

const BASELINE_REJECT_CONFIRM_MESSAGE =
  "Reject this baseline candidate?\n\n" +
  "It will never become the active baseline for this source. There is no " +
  "undo - the next successful ingest will need to establish a fresh " +
  "candidate from scratch.";

export default function ApprovalsPage() {
  const [pending, setPending] = useState<PendingApprovals | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [resolvedBy, setResolvedBy] = useState("dashboard-user");
  const [initialLoading, setInitialLoading] = useState(false);
  // Keyed by `${itemId}:${decision}` - resolving a group can take a while
  // (a diagnosis or reveal re-check may hit an LLM retry-with-backoff), and
  // more than one item can be worked on independently, so a single global
  // "busy" flag would either lock the whole page or lie about which button
  // is actually doing something.
  const [inFlight, setInFlight] = useState<Record<string, boolean>>({});
  // Keyed like inFlight (`${itemId}:${decision}`) - only ever populated for
  // a validation_event resolve, the one decision type whose resume can hit
  // narrate_node's LLM chain and take a while (see resolve()'s
  // status==="resolving" branch below). Every other item type's resolve
  // is still synchronous and never touches this.
  const [resolveProgress, setResolveProgress] = useState<Record<string, string>>({});
  const { toast, showToast, dismissToast } = useToast();

  async function load() {
    setInitialLoading(true);
    try {
      setError(null);
      setPending(await apiFetch<PendingApprovals>("/approvals/pending"));
    } catch (err) {
      setError(err);
    } finally {
      setInitialLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // itemLabel identifies WHAT was resolved (e.g. "Baseline for source
  // abc123", "Validation event group (2 rules)") - the whole point of the
  // toast is to say what just happened, not just that something did, since
  // the resolved item itself disappears from its list the same moment this
  // shows.
  //
  // reject_data means something different for each item type it's used
  // for (see BASELINE_DECISIONS/EVENT_DECISIONS in
  // app/routers/approvals.py) - a baseline candidate has no "run" to fail,
  // so the confirm-dialog wording and toast phrasing are overridable per
  // call site rather than hardcoded to the validation-event language.
  async function resolve(
    id: string,
    decision: Decision,
    itemLabel: string,
    opts?: { confirmMessage?: string; pastTense?: string },
  ) {
    // reject_data is the one decision in the whole system with no undo -
    // no fix chain to replay, no baseline history, no reversal record.
    // Every other decision here is recoverable in some form; this one
    // isn't, so it's the only one that gets a confirmation step.
    if (decision === "reject_data" && !window.confirm(opts?.confirmMessage ?? DISCARD_CONFIRM_MESSAGE)) {
      return;
    }
    const key = `${id}:${decision}`;
    setInFlight((prev) => ({ ...prev, [key]: true }));
    try {
      const ack = await apiPostJson<ResolveAckResponse>(`/approvals/${id}/resolve`, {
        decision,
        resolved_by: resolvedBy || "dashboard-user",
      });
      // Baseline/connector-warning/query/model resolutions never touch the
      // graph and are already final here (status is absent or something
      // other than "resolving"). Only a validation_event's decision defers
      // to a background graph resume - RESOLVE-HANG FIX, same cure as
      // ingest's: never hold this request open for narrate_node's LLM
      // chain, poll for the real outcome instead of waiting on one fetch().
      if (ack.status === "resolving" && ack.run_id) {
        await pollIngestStatus(
          ack.run_id,
          (elapsedMs) => {
            const seconds = Math.round(elapsedMs / 1000);
            setResolveProgress((prev) => ({
              ...prev,
              [key]: `Still working (${seconds}s elapsed) - a slow or retrying LLM call can take a while, this is not stuck`,
            }));
          },
          id
        );
      }
      showToast(`${itemLabel} ${opts?.pastTense ?? DECISION_PAST_TENSE[decision]}.`, "success");
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      showToast(`Failed to resolve ${itemLabel}: ${message}`, "error");
    } finally {
      setInFlight((prev) => ({ ...prev, [key]: false }));
      setResolveProgress((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  }

  const isBusy = (id: string, decision: Decision) => inFlight[`${id}:${decision}`] ?? false;

  return (
    <div>
      <h1 className="mb-6 text-page-title text-ink">Approvals</h1>
      <div className="mb-6 flex items-center gap-3">
        <label className="text-sm font-medium text-ink-muted">Resolved by</label>
        <input
          value={resolvedBy}
          onChange={(e) => setResolvedBy(e.target.value)}
          className="rounded-sm border border-border-strong px-2 py-1.5 text-sm"
        />
        <Button onClick={load} loading={initialLoading} loadingText="Refreshing...">
          Refresh
        </Button>
      </div>

      <ErrorMessage error={error} />

      {/* Dashboard UX pass, Part 2: distinguishes "nothing ever" from
          "everything handled" - only shown once every section's pending
          list is actually empty, since "All clear" would be a lie while
          something is still waiting on a decision. */}
      {pending &&
        pending.provisional_baselines.length === 0 &&
        pending.validation_events.length === 0 &&
        pending.connector_warnings.length === 0 &&
        pending.escalated_queries.length === 0 &&
        pending.escalated_models.length === 0 && (
          <p className="mb-6 rounded-md border border-status-positive bg-status-positive-tint px-4 py-2 text-sm font-medium text-status-positive">
            {pending.summary.total_resolved === 0
              ? "Nothing has required review yet."
              : `All clear — ${pending.summary.total_resolved} item${pending.summary.total_resolved === 1 ? "" : "s"} resolved across ${pending.summary.distinct_runs} run${pending.summary.distinct_runs === 1 ? "" : "s"}.`}
          </p>
        )}

      {/* ORDERED BY CONSEQUENCE, not by how many rows each has.
          An escalated validation event is a run PAUSED waiting on a
          person - nothing downstream of it can proceed. An escalated
          query or model is an answer withheld. A connector warning is
          advisory. A provisional baseline is informational: normal on
          a first ingest and resolvable whenever.

          Baselines used to lead, so ~45 informational rows sat above
          the one paused thing and told a reviewer the wrong story about
          what needed them. */}
      <Card title="Escalated validation events" subtitle="A correlated group of failures is shown, and resolved, as ONE item.">
        {!pending ? (
          <Muted>Loading...</Muted>
        ) : pending.validation_events.length === 0 ? (
          <EmptyState
            neverText="Nothing here yet."
            resolvedCount={pending.recently_resolved.validation_events.length}
            triggerText="Appears when an ingest finds issues that can't be auto-fixed."
          />
        ) : (
          <div className="flex flex-col gap-4">
            {pending.validation_events.map((g) => {
              const diagnosis = (g.diagnosis ?? {}) as Record<string, unknown>;
              const suggested = (diagnosis.suggested_fix ?? {}) as Record<string, unknown>;
              const hasApprovableAction = typeof suggested.action === "string" && suggested.action !== "escalate" && !diagnosis.error;
              const groupBusy =
                isBusy(g.resolve_id, "approve") ||
                isBusy(g.resolve_id, "reject_fix") ||
                isBusy(g.resolve_id, "reject_data") ||
                isBusy(g.resolve_id, "accept_as_baseline");
              return (
                <div key={g.resolve_id} className="rounded-md border border-border p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <RunLabel runNumber={g.run_number} runId={g.run_id} />
                    {g.risk_level && <Badge value={g.risk_level} />}
                    <RunLinks runId={g.run_id} runNumber={g.run_number} />
                  </div>
                  <p className="mb-1 text-sm">
                    <span className="font-medium">Rules failed:</span> {g.rules_failed.join(", ")}
                  </p>
                  <p className="mb-1 text-sm">
                    <span className="font-medium">Columns:</span> {g.columns.filter(Boolean).join(", ")}
                  </p>
                  {typeof diagnosis.likely_cause === "string" && (
                    <p className="mb-1 text-sm">
                      <span className="font-medium">Diagnosis:</span> {diagnosis.likely_cause}
                      {typeof suggested.action === "string" && ` (suggested: ${suggested.action}, confidence: ${String(diagnosis.confidence ?? "")})`}
                    </p>
                  )}
                  {g.gate_reasons && g.gate_reasons.length > 0 && (
                    <p className="mb-2 text-sm text-ink-muted">
                      <span className="font-medium text-ink">Gate reasons:</span> {g.gate_reasons.join("; ")}
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {(() => {
                      const label = `Validation group (${g.rules_failed.length} rule${g.rules_failed.length === 1 ? "" : "s"})`;
                      return (
                        <>
                          {/* The three recoverable decisions, grouped together. */}
                          <Button
                            variant="primary"
                            disabled={!hasApprovableAction || (groupBusy && !isBusy(g.resolve_id, "approve"))}
                            loading={isBusy(g.resolve_id, "approve")}
                            loadingText={DECISION_LABELS.approve}
                            onClick={() => resolve(g.resolve_id, "approve", label)}
                          >
                            Approve
                          </Button>
                          <Button
                            disabled={groupBusy && !isBusy(g.resolve_id, "reject_fix")}
                            loading={isBusy(g.resolve_id, "reject_fix")}
                            loadingText={DECISION_LABELS.reject_fix}
                            onClick={() => resolve(g.resolve_id, "reject_fix", label)}
                          >
                            Keep data as-is
                          </Button>
                          <Button
                            disabled={groupBusy && !isBusy(g.resolve_id, "accept_as_baseline")}
                            loading={isBusy(g.resolve_id, "accept_as_baseline")}
                            loadingText={DECISION_LABELS.accept_as_baseline}
                            onClick={() => resolve(g.resolve_id, "accept_as_baseline", label)}
                          >
                            Accept as new baseline
                          </Button>
                          {/* Separated with a visible divider, not just spacing -
                              this is the one decision with no undo, and it should
                              never read as a fourth variant of the three above. */}
                          <span className="mx-2 h-6 w-px bg-border-strong" aria-hidden="true" />
                          <Button
                            variant="danger"
                            disabled={groupBusy && !isBusy(g.resolve_id, "reject_data")}
                            loading={isBusy(g.resolve_id, "reject_data")}
                            loadingText={DECISION_LABELS.reject_data}
                            onClick={() => resolve(g.resolve_id, "reject_data", label)}
                          >
                            Discard run (permanent)
                          </Button>
                        </>
                      );
                    })()}
                  </div>
                  {(["approve", "reject_fix", "accept_as_baseline", "reject_data"] as Decision[])
                    .map((d) => resolveProgress[`${g.resolve_id}:${d}`])
                    .filter(Boolean)
                    .map((message, i) => (
                      <p key={i} className="mt-1 text-xs text-status-active">
                        {message}
                      </p>
                    ))}
                  {!hasApprovableAction && (
                    <Muted>Approve is disabled - the diagnosis has no matrix-permitted fix to apply for this group.</Muted>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {pending && (
          <RecentlyResolved count={pending.recently_resolved.validation_events.length}>
            {pending.recently_resolved.validation_events.map((e) => (
              <div key={e.id} className="rounded-md bg-surface-sunken p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>
                    {e.rule_failed}
                    {e.column && ` (${e.column})`}
                  </span>
                  <RunLabel runNumber={e.run_number} runId={e.run_id} />
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <ResolvedMeta decision={e.decision} resolvedBy={e.resolved_by} resolvedAt={e.resolved_at} />
                  <RunLinks runId={e.run_id} runNumber={e.run_number} />
                </div>
              </div>
            ))}
          </RecentlyResolved>
        )}
      </Card>

      <Card title="Escalated queries">
        {!pending ? (
          <Muted>Loading...</Muted>
        ) : pending.escalated_queries.length === 0 ? (
          <EmptyState
            neverText="Nothing here yet."
            resolvedCount={pending.recently_resolved.queries.length}
            triggerText="Appears when a question on the Ask page can't be answered safely."
          />
        ) : (
          <div className="flex flex-col gap-4">
            {pending.escalated_queries.map((q) => (
              <div key={q.id} className="rounded-md border border-border p-4">
                <div className="mb-1 flex items-center gap-2 text-sm">
                  <RunLabel runNumber={q.run_number} runId={q.run_id} />
                  <RunLinks runId={q.run_id} runNumber={q.run_number} />
                </div>
                <p className="mb-1 text-sm"><span className="font-medium">Question:</span> {q.question}</p>
                <p className="mb-2 text-sm text-ink-muted">
                  <span className="font-medium text-ink">Escalation:</span> {q.escalation_reason} - {q.escalation_detail}
                </p>
                <div className="mb-3"><CodeBlock>{q.code || "(no code generated)"}</CodeBlock></div>
                <div className="flex gap-2">
                  {q.approvable && (
                    <Button
                      variant="primary"
                      onClick={() => resolve(q.id, "approve", "Escalated query")}
                      loading={isBusy(q.id, "approve")}
                      loadingText={DECISION_LABELS.approve}
                      disabled={isBusy(q.id, "reject_fix")}
                    >
                      Approve &amp; run
                    </Button>
                  )}
                  <Button
                    onClick={() => resolve(q.id, "reject_fix", "Escalated query")}
                    loading={isBusy(q.id, "reject_fix")}
                    loadingText={DECISION_LABELS.reject_fix}
                    disabled={isBusy(q.id, "approve")}
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
        {pending && (
          <RecentlyResolved count={pending.recently_resolved.queries.length}>
            {pending.recently_resolved.queries.map((q) => (
              <div key={q.id} className="rounded-md bg-surface-sunken p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>{q.question}</span>
                  <RunLabel runNumber={q.run_number} runId={q.run_id} />
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <ResolvedMeta decision={q.decision} resolvedBy={q.resolved_by} resolvedAt={q.resolved_at} />
                  <RunLinks runId={q.run_id} runNumber={q.run_number} />
                </div>
              </div>
            ))}
          </RecentlyResolved>
        )}
      </Card>

      <Card title="Escalated models">
        {!pending ? (
          <Muted>Loading...</Muted>
        ) : pending.escalated_models.length === 0 ? (
          <EmptyState
            neverText="Nothing here yet."
            resolvedCount={pending.recently_resolved.models.length}
            triggerText="Appears when a Predict request can't produce a trustworthy model."
          />
        ) : (
          <div className="flex flex-col gap-4">
            {pending.escalated_models.map((m) => (
              <div key={m.id} className="rounded-md border border-border p-4">
                <div className="mb-1 flex items-center gap-2 text-sm">
                  <RunLabel runNumber={m.run_number} runId={m.run_id} />
                  <RunLinks runId={m.run_id} runNumber={m.run_number} />
                </div>
                <p className="mb-1 text-sm">
                  <span className="font-medium">Target:</span> {m.target_column} <span className="font-medium">Task:</span> {m.task_type}
                </p>
                <p className="mb-2 text-sm text-ink-muted">
                  <span className="font-medium text-ink">Escalation:</span> {m.escalation_reason} - {m.escalation_detail}
                </p>
                {m.out_of_sample_score != null && (
                  <p className="mb-2 text-sm">
                    <span className="font-medium">{m.out_of_sample_metric}:</span> {m.out_of_sample_score}
                  </p>
                )}
                <div className="flex gap-2">
                  {m.approvable && (
                    <Button
                      variant="primary"
                      onClick={() => resolve(m.id, "approve", "Escalated model")}
                      loading={isBusy(m.id, "approve")}
                      loadingText={DECISION_LABELS.approve}
                      disabled={isBusy(m.id, "reject_fix")}
                    >
                      Approve despite caveat
                    </Button>
                  )}
                  <Button
                    onClick={() => resolve(m.id, "reject_fix", "Escalated model")}
                    loading={isBusy(m.id, "reject_fix")}
                    loadingText={DECISION_LABELS.reject_fix}
                    disabled={isBusy(m.id, "approve")}
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
        {pending && (
          <RecentlyResolved count={pending.recently_resolved.models.length}>
            {pending.recently_resolved.models.map((m) => (
              <div key={m.id} className="rounded-md bg-surface-sunken p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>{m.target_column ?? m.question ?? "Model"}</span>
                  <RunLabel runNumber={m.run_number} runId={m.run_id} />
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <ResolvedMeta decision={m.decision} resolvedBy={m.resolved_by} resolvedAt={m.resolved_at} />
                  <RunLinks runId={m.run_id} runNumber={m.run_number} />
                </div>
              </div>
            ))}
          </RecentlyResolved>
        )}
      </Card>

      <Card title="Connector warnings">
        {!pending ? (
          <Muted>Loading...</Muted>
        ) : pending.connector_warnings.length === 0 ? (
          <EmptyState
            neverText="Nothing here yet."
            resolvedCount={pending.recently_resolved.connector_warnings.length}
            triggerText="Appears for source-level issues like a multi-sheet Excel file or a SQL type mismatch."
          />
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                <th className="py-2 px-3 font-medium">Run</th>
                <th className="py-2 px-3 font-medium">Message</th>
                <th className="py-2 px-3 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {pending.connector_warnings.map((w) => (
                <tr key={w.id} className="border-b border-border">
                  <td className="py-2 px-3">
                    <div className="flex items-center gap-2">
                      <RunLabel runNumber={w.run_number} runId={w.run_id} />
                      <RunLinks runId={w.run_id} runNumber={w.run_number} />
                    </div>
                  </td>
                  <td className="py-2 px-3">{w.message}</td>
                  <td className="py-2 px-3">
                    <Button
                      onClick={() => resolve(w.id, "acknowledge", "Connector warning")}
                      loading={isBusy(w.id, "acknowledge")}
                      loadingText={DECISION_LABELS.acknowledge}
                    >
                      Acknowledge
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
        {pending && (
          <RecentlyResolved count={pending.recently_resolved.connector_warnings.length}>
            {pending.recently_resolved.connector_warnings.map((w) => (
              <div key={w.id} className="rounded-md bg-surface-sunken p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>{w.message}</span>
                  <RunLabel runNumber={w.run_number} runId={w.run_id} />
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <ResolvedMeta decision={w.decision} resolvedBy={w.resolved_by} resolvedAt={w.resolved_at} />
                  <RunLinks runId={w.run_id} runNumber={w.run_number} />
                </div>
              </div>
            ))}
          </RecentlyResolved>
        )}
      </Card>

      <Card title="Provisional baselines" subtitle="Established automatically on a source's first ingest, not yet confirmed by a human.">
        {!pending ? (
          <Muted>Loading...</Muted>
        ) : pending.provisional_baselines.length === 0 ? (
          <EmptyState
            neverText="Nothing here yet."
            resolvedCount={pending.recently_resolved.provisional_baselines.length}
            triggerText="Appears automatically on a source's first successful ingest, until a human confirms or rejects it."
          />
        ) : (
          <CollapsibleList count={pending.provisional_baselines.length} noun="provisional baseline">
          <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                <th className="py-2 px-3 font-medium">ID</th>
                <th className="py-2 px-3 font-medium">Source</th>
                <th className="py-2 px-3 text-right font-medium">Rows</th>
                <th className="py-2 px-3 font-medium">Created</th>
                <th className="py-2 px-3 font-medium">Decision</th>
              </tr>
            </thead>
            <tbody>
              {pending.provisional_baselines.map((b) => (
                <tr key={b.id} className="border-b border-border">
                  <td className="py-2 px-3"><CopyableId id={b.id} /></td>
                  <td className="py-2 px-3"><CopyableId id={b.source_id} /></td>
                  <td className="py-2 px-3 text-right font-mono">{b.row_count}</td>
                  <td className="py-2 px-3 text-ink-muted">{b.created_at}</td>
                  <td className="py-2 px-3">
                    <div className="flex gap-2">
                      <Button
                        onClick={() =>
                          resolve(b.id, "approve", `Baseline for source ${b.source_id.slice(0, 8)}`, {
                            pastTense: BASELINE_DECISION_PAST_TENSE.approve,
                          })
                        }
                        loading={isBusy(b.id, "approve")}
                        loadingText={DECISION_LABELS.approve}
                        disabled={isBusy(b.id, "reject_data")}
                      >
                        Confirm
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() =>
                          resolve(b.id, "reject_data", `Baseline for source ${b.source_id.slice(0, 8)}`, {
                            confirmMessage: BASELINE_REJECT_CONFIRM_MESSAGE,
                            pastTense: BASELINE_DECISION_PAST_TENSE.reject_data,
                          })
                        }
                        loading={isBusy(b.id, "reject_data")}
                        loadingText={DECISION_LABELS.reject_data}
                        disabled={isBusy(b.id, "approve")}
                      >
                        Reject
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          </CollapsibleList>
        )}
        {pending && (
          <RecentlyResolved count={pending.recently_resolved.provisional_baselines.length}>
            {pending.recently_resolved.provisional_baselines.map((b) => (
              <div key={b.id} className="rounded-md bg-surface-sunken p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>Baseline for source</span>
                  <CopyableId id={b.source_id} />
                </div>
                <ResolvedMeta decision={b.decision} resolvedBy={b.resolved_by} resolvedAt={b.resolved_at} />
              </div>
            ))}
          </RecentlyResolved>
        )}
      </Card>

      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
