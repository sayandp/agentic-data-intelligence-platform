import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, apiFetch } from "../api/client";
import type { AnalysisFindingRecord, AnalysisResultRecord, AnalyticsRecord, RoleCandidateRecord } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted, RunLabel, type BadgeTone } from "../components/ui";
import { RunNotFoundHelp, RunPicker, useRunSelection } from "../components/RunPicker";
import { chartForFinding, clusterScatter, paretoBandColor, segmentBar, type ChartSpec } from "../lib/analyticsCharts";
import { loadPlotly } from "../lib/plotly";

// Business analytics for one run. The NOT-APPLICABLE list is given the same
// weight as the results, because "why didn't this run" is the most common
// question this page has to answer - an empty section with no explanation is
// the exact failure the capability layer was built to prevent.

const ANALYSIS_TITLES: Record<string, string> = {
  abc_pareto: "ABC / Pareto concentration",
  rfm: "RFM segments",
  cohort_retention: "Cohort retention",
  behavioural_segmentation: "Behavioural segmentation",
  market_basket: "Market basket",
  retention_churn: "Retention and churn",
  historical_clv: "Historical customer value",
};

function AnalyticsChart({ spec, id }: { spec: ChartSpec; id: string }) {
  useEffect(() => {
    loadPlotly().then(() => {
      const el = document.getElementById(id);
      if (el && window.Plotly) {
        try {
          window.Plotly.newPlot(el, spec.data, spec.layout, { responsive: true, displayModeBar: false });
        } catch {
          el.innerHTML = '<p class="text-sm text-ink-muted">Chart could not be rendered.</p>';
        }
      }
    });
  }, [spec, id]);
  return <div id={id} className="mt-2" />;
}

// CONFIDENCE, not severity. "high" here is the good case, the opposite of
// what the same word means on a data-quality issue.
const CONFIDENCE_TONES: Record<string, BadgeTone> = {
  confirmed: "positive",
  high: "positive",
  medium: "caution",
  low: "neutral",
  stale: "caution",
};

function pct(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "-";
}

function num(value: unknown, digits = 2): string {
  return typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : "-";
}

/** Every finding type renders as a table of its own payload. Charts are
 *  additive; the numbers are always present in text, so nothing depends on
 *  a chart having rendered. */
function FindingBody({ finding }: { finding: AnalysisFindingRecord }) {
  const p = finding.payload;
  switch (finding.finding_type) {
    case "concentration_band":
      return (
        <tr className="border-b border-border">
          <td className="px-3 py-2 font-medium">
            {/* Ordered, so a ramp step - and the letter is always present,
                because the band must never depend on colour alone. */}
            <span
              className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle"
              style={{ backgroundColor: paretoBandColor(String(p.band)) }}
              aria-hidden="true"
            />
            Band {String(p.band)}
          </td>
          <td className="px-3 py-2 text-right font-mono">{num(p.entity_count, 0)}</td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.entity_share)}</td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.value_share)}</td>
          <td className="px-3 py-2 text-ink-muted">{(p.top_entities as string[]).slice(0, 3).join(", ")}</td>
        </tr>
      );
    case "segment_profile":
      return (
        <tr className="border-b border-border">
          <td className="px-3 py-2 font-medium">{String(p.segment)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.entity_count, 0)}</td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.entity_share)}</td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.value_share)}</td>
          <td className="px-3 py-2 text-right font-mono text-ink-muted">
            {num((p.centre as Record<string, number>)?.recency_days, 0)}d
          </td>
        </tr>
      );
    case "association_rule":
      return (
        <tr className="border-b border-border">
          <td className="px-3 py-2">
            {(p.antecedent as string[]).join(" + ")} <span className="text-ink-faint">&rarr;</span>{" "}
            {(p.consequent as string[]).join(" + ")}
          </td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.support)}</td>
          <td className="px-3 py-2 text-right font-mono">{pct(p.confidence)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.lift)}</td>
        </tr>
      );
    case "lifetime_value":
      return (
        <tr className="border-b border-border">
          <td className="px-3 py-2 font-medium">{String(p.segment)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.entity_count, 0)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.average_order_value)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.purchase_frequency)}</td>
          <td className="px-3 py-2 text-right font-mono">{num(p.historical_value_per_entity)}</td>
        </tr>
      );
    default:
      return null;
  }
}

const TABLE_HEADERS: Record<string, string[]> = {
  concentration_band: ["Band", "Entities", "Share of entities", "Share of value", "Top"],
  segment_profile: ["Segment", "Entities", "Share of entities", "Share of value", "Mean recency"],
  association_rule: ["Rule", "Support", "Confidence", "Lift"],
  lifetime_value: ["Segment", "Entities", "Avg order value", "Orders per entity", "Value per entity"],
};

/** Whether the concentration this method is named for actually holds.
 *
 *  Reported EITHER WAY, and above the bands rather than below them: a
 *  reader who takes an A/B/C split at face value on flat data has been
 *  misled by the method's name, and a note they have to scroll to find has
 *  not corrected that. The sentence itself comes from the backend, so the
 *  wording lives in one place and cannot drift from the number. */
function ConcentrationNote({ finding }: { finding: AnalysisFindingRecord }) {
  const weak = finding.payload.concentration_is_weak;
  const note = finding.payload.concentration_note;
  // Older rows predate this check: `null` means unknown, and inventing a
  // reassuring "concentration holds" for them would be worse than silence.
  if (typeof note !== "string" || typeof weak !== "boolean") return null;

  if (weak) {
    return (
      <div className="mb-4 rounded-md border border-status-caution bg-status-caution-tint p-4">
        <p className="text-sm font-medium text-status-caution">Concentration is weak for this data</p>
        <p className="mt-1 text-sm text-ink">{note}</p>
      </div>
    );
  }
  return <p className="mb-4 text-sm text-ink-muted">{note}</p>;
}

/** WHICH quantity every summed figure on this page describes.
 *
 *  A revenue total and a unit-price total look equally plausible in
 *  isolation and differ by orders of magnitude, so this is stated once, at
 *  the top, before any number that depends on it - not left for a reader to
 *  infer from a column name in a parameters blob. */
function ValueDefinitionNote({ data }: { data: AnalyticsRecord }) {
  const vd = data.value_definition;
  if (!vd) return null;
  return (
    <div className="mb-6 rounded-md border border-border bg-surface-sunken p-4">
      <p className="text-sm text-ink">
        <span className="font-medium">Value {vd.derived ? "computed as" : "taken directly from"}</span>{" "}
        <span className="font-mono">{vd.label}</span>
      </p>
      <p className="mt-1 text-sm text-ink-muted">{vd.note}</p>
    </div>
  );
}

/** A monetary column is allowed a small share of negatives - returns and
 *  refunds are ordinary transaction data. When any are present the count is
 *  stated NEXT TO THE ROLE, so "accepted despite returns" never looks the
 *  same as "had none". Silence here would hide the one fact that changes how
 *  every summed figure below should be read. */
function NegativeValueNote({ candidate }: { candidate: RoleCandidateRecord }) {
  const count = candidate.details?.negative_count ?? 0;
  const fraction = candidate.details?.negative_fraction;
  if (count <= 0) return null;
  const pct = typeof fraction === "number" ? `${(fraction * 100).toFixed(fraction < 0.001 ? 4 : 2)}%` : null;
  return (
    <span className="ml-1 text-ink-faint">
      {count.toLocaleString()} negative{pct ? ` (${pct})` : ""} &mdash; returns net off
    </span>
  );
}

/** Who is NOT in the A/B/C bands, and why. An entity that returned
 *  everything nets to zero and is held out of the ranking; leaving that
 *  unsaid would make the bands look like they covered everyone. */
function NonContributingNote({ finding }: { finding: AnalysisFindingRecord }) {
  const p = finding.payload;
  return (
    <div className="mt-3 rounded-md border border-border bg-surface-sunken p-4">
      <p className="text-sm font-medium text-ink">
        {String(p.entity_count)} held out of the bands (net zero or below)
      </p>
      <p className="mt-1 text-sm text-ink-muted">{String(p.note)}</p>
      {Array.isArray(p.examples) && p.examples.length > 0 && (
        <p className="mt-1 text-xs text-ink-faint">
          For example: <span className="font-mono">{(p.examples as string[]).slice(0, 5).join(", ")}</span>
        </p>
      )}
    </div>
  );
}

/** The detector deliberately refuses to promote a low-confidence candidate
 *  on its own. This is the other half of that: the control a person uses to
 *  answer the question it declined to guess at.
 *
 *  One row per DISTINCT missing role, not one per analysis - `monetary` is
 *  usually missing for three analyses at once, and three identical pickers
 *  would read as three different questions. Each row names the analyses the
 *  answer would unlock, so the cost of answering is visible.
 *
 *  Nothing is ever pre-selected. An unconfirmed candidate stays unused, so
 *  a default selection would be the machine guessing under the appearance
 *  of a human decision - exactly what the confidence floor exists to
 *  prevent. */
function RoleConfirmation({
  data,
  runRef,
  columns,
  onUpdated,
}: {
  data: AnalyticsRecord;
  runRef: string;
  columns: string[];
  onUpdated: (next: AnalyticsRecord) => void;
}) {
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const confirmed = data.detected_roles.confirmed_roles ?? {};
  const stale = data.detected_roles.stale_confirmations ?? [];

  // Distinct missing roles, each carrying what it would unlock.
  const wanted = new Map<string, { description: string; unlocks: string[]; changesValue?: boolean; candidates?: RoleCandidateRecord[] }>();
  for (const entry of data.applicability) {
    if (entry.applicable) continue;
    for (const { role, description } of entry.missing_roles ?? []) {
      const seen = wanted.get(role) ?? { description, unlocks: [] };
      seen.unlocks.push(ANALYSIS_TITLES[entry.analysis] ?? entry.analysis);
      wanted.set(role, seen);
    }
  }
  // The quantity gap is NOT a missing requirement of any analysis - every
  // analysis still ran. It changes what "value" MEANS, which is why it has
  // to be merged in here explicitly rather than arriving via missing_roles.
  const gap = data.quantity_confirmation;
  if (gap) {
    wanted.set(gap.role, {
      description: gap.reason,
      unlocks: [],
      changesValue: true,
      // The detector's own reason for rejecting each candidate. That
      // sentence is what lets a person overrule it with confidence -
      // "contains negative values" on a returns column is a reason to
      // confirm anyway, not a reason to stay away.
      candidates: gap.candidates,
    });
  }

  // A role already answered is not still a question, even if the answer is
  // stale - the stale entry gets a Clear action below instead.
  for (const role of Object.keys(confirmed)) wanted.delete(role);

  async function send(path: string, init: RequestInit, role: string) {
    setBusy(role);
    setError(null);
    try {
      onUpdated(await apiFetch<AnalyticsRecord>(path, init));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  const confirm = (role: string) =>
    send(
      `/analytics/${encodeURIComponent(runRef)}/confirmed-roles`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, column: choice[role] }),
      },
      role
    );

  const clear = (role: string) =>
    send(`/analytics/${encodeURIComponent(runRef)}/confirmed-roles/${encodeURIComponent(role)}`, { method: "DELETE" }, role);

  if (wanted.size === 0 && Object.keys(confirmed).length === 0) return null;

  return (
    <div className="mt-4 border-t border-border pt-4">
      <p className="text-sm font-medium text-ink">Confirm a column role</p>
      <p className="mt-1 text-sm text-ink-muted">
        Nothing here is selected for you. Confirming a role re-runs the analyses that need it, and is remembered for
        this source so a later ingest does not ask again.
      </p>

      <ErrorMessage error={error} />

      {Object.entries(confirmed).length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {Object.entries(confirmed).map(([role, column]) => {
            const staleEntry = stale.find((s) => s.role === role);
            return (
              <li key={role} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-ink-muted">{role}</span>
                <span className="font-mono text-ink">{column}</span>
                <Badge value={staleEntry ? "stale" : "confirmed"} tone={CONFIDENCE_TONES[staleEntry ? "stale" : "confirmed"]} />
                {staleEntry && <span className="text-xs text-ink-faint">{staleEntry.why}</span>}
                <Button onClick={() => clear(role)} disabled={busy === role}>
                  {busy === role ? "Clearing..." : "Clear"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      {wanted.size > 0 && columns.length === 0 && (
        <p className="mt-3 text-sm text-ink-faint">Loading this run's columns...</p>
      )}

      {columns.length > 0 &&
        [...wanted.entries()].map(([role, { description, unlocks, changesValue, candidates }]) => {
          const selectId = `confirm-role-${role}`;
          return (
            <div key={role} className="mt-3 flex flex-wrap items-end gap-3">
              <div>
                <label htmlFor={selectId} className="block text-sm text-ink">
                  <span className="font-mono">{role}</span> &mdash; {description}
                </label>
                <p className="text-xs text-ink-faint">
                  {changesValue
                    ? "Would change how value is summed on every analysis below."
                    : `Would let ${unlocks.join(", ")} run.`}
                </p>
                {candidates && candidates.length > 0 && (
                  <p className="mt-1 text-xs text-ink-faint">
                    Closest:{" "}
                    {candidates.slice(0, 2).map((c, i) => (
                      <span key={c.column}>
                        {i > 0 && "; "}
                        <span className="font-mono">{c.column}</span> &mdash; {c.reasons[0]}
                      </span>
                    ))}
                  </p>
                )}
              </div>
              <select
                id={selectId}
                value={choice[role] ?? ""}
                onChange={(e) => setChoice({ ...choice, [role]: e.target.value })}
                className="rounded-sm border border-border bg-surface px-3 py-2 text-sm text-ink"
              >
                <option value="">Select a column...</option>
                {columns.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <Button onClick={() => confirm(role)} disabled={!choice[role] || busy === role}>
                {busy === role ? "Confirming..." : "Confirm"}
              </Button>
            </div>
          );
        })}
    </div>
  );
}

function ResultCard({ result }: { result: AnalysisResultRecord }) {
  const title = ANALYSIS_TITLES[result.analysis] ?? result.analysis;

  if (!result.ran) {
    return (
      <Card title={title}>
        <div className="rounded-md border border-status-caution bg-status-caution-tint p-4">
          <p className="text-sm font-medium text-status-caution">Not run</p>
          <p className="mt-1 text-sm text-ink">{result.not_run_reason}</p>
        </div>
      </Card>
    );
  }

  const tabular = result.findings.filter((f) => TABLE_HEADERS[f.finding_type]);
  const headers = tabular.length ? TABLE_HEADERS[tabular[0].finding_type] : null;
  const curve = result.findings.find((f) => chartForFinding(f) !== null);
  const chart =
    curve !== undefined
      ? chartForFinding(curve)
      : result.analysis === "behavioural_segmentation"
        ? clusterScatter(result.findings)
        : result.analysis === "rfm"
          ? segmentBar(result.findings)
          : null;

  const repeat = result.findings.find((f) => f.finding_type === "repeat_behaviour");
  const concentration = result.findings.find((f) => f.finding_type === "concentration_curve");
  const nonContributing = result.findings.find((f) => f.finding_type === "non_contributing_entities");

  return (
    <Card title={title}>
      {concentration && <ConcentrationNote finding={concentration} />}

      {repeat && (
        <div className="mb-4 flex flex-wrap gap-6 text-sm">
          <div>
            <span className="font-medium text-ink-muted">Repeat rate:</span>{" "}
            <span className="font-mono">{pct(repeat.payload.repeat_rate)}</span>
          </div>
          <div>
            <span className="font-medium text-ink-muted">Inactive:</span>{" "}
            <span className="font-mono">{pct(repeat.payload.inactive_share)}</span>{" "}
            <span className="text-ink-faint">
              (using a {String(repeat.payload.inactivity_window_days)}-day window)
            </span>
          </div>
          <div>
            <span className="font-medium text-ink-muted">Median gap:</span>{" "}
            <span className="font-mono">{num(repeat.payload.gap_days_median, 0)} days</span>
          </div>
        </div>
      )}

      {headers && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-ink-muted">
                {headers.map((h, i) => (
                  <th key={h} className={`px-3 py-2 font-medium ${i > 0 && i < headers.length - 1 ? "text-right" : ""}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tabular.map((f) => (
                <FindingBody key={f.id} finding={f} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {nonContributing && <NonContributingNote finding={nonContributing} />}

      {chart && <AnalyticsChart spec={chart} id={`chart-${result.analysis}`} />}

      {/* Every knob this analysis used. A result whose thresholds are
          invisible is not reproducible - and for churn and CLV the chosen
          window and value basis change what the numbers MEAN. */}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-medium text-brand-600">Parameters used</summary>
        <pre className="mt-2 overflow-x-auto rounded-md bg-code p-3 font-mono text-xs text-code-ink">
          {JSON.stringify(result.parameters, null, 2)}
        </pre>
      </details>
    </Card>
  );
}

export default function AnalyticsPage() {
  const [params] = useSearchParams();
  const selection = useRunSelection(params.get("run") ?? "");
  const [data, setData] = useState<AnalyticsRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const runRef = selection.runRef.trim();

  useEffect(() => {
    if (!runRef) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    apiFetch<AnalyticsRecord>(`/analytics/${encodeURIComponent(runRef)}`)
      .then((body) => !cancelled && setData(body))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runRef]);

  const notApplicable = (data?.applicability ?? []).filter((a) => !a.applicable);
  const notFound = error instanceof ApiError && error.status === 404;

  return (
    <div>
      <h1 className="mb-2 text-[22px] font-semibold text-ink">Analytics</h1>
      <Muted>
        Named business analyses, computed deterministically from this run's repaired data. No language model takes part
        in choosing a method or producing a number.
      </Muted>

      <div className="my-6">
        <Card>
          <RunPicker selection={selection} idPrefix="analytics" />
        </Card>
      </div>

      {loading && <Muted>Loading analytics...</Muted>}
      <ErrorMessage error={error} />
      {notFound && <RunNotFoundHelp error={error} selection={selection} />}

      {data && (
        <>
          <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
            <RunLabel runNumber={data.run_number} runId={data.run_id} />
            <span className="text-ink-muted" aria-hidden="true">/</span>
            <span className="text-ink-muted">Roles detected:</span>
            {Object.entries(data.detected_roles.assigned).map(([role, candidate]) => (
              <span key={role} className="text-xs">
                <span className="text-ink-muted">{role}</span>{" "}
                <span className="font-mono text-ink">{candidate.column}</span>{" "}
                <Badge value={candidate.confidence} tone={CONFIDENCE_TONES[candidate.confidence]} />
                <NegativeValueNote candidate={candidate} />
              </span>
            ))}
          </div>

          <ValueDefinitionNote data={data} />

          {/* The refusals, FIRST and in full. A reader arriving at a page
              with four analyses must immediately see that the other three
              were ineligible and exactly why - not conclude the agent found
              nothing worth reporting. */}
          {notApplicable.length > 0 && (
            <Card
              title={`Not applicable to this data (${notApplicable.length} of ${data.applicability.length})`}
              subtitle="Each analysis needs a specific data shape. These did not run, and this is the requirement each one was missing."
            >
              <div className="flex flex-col gap-3">
                {notApplicable.map((entry) => (
                  <div key={entry.analysis} className="rounded-md border border-border p-3">
                    <p className="text-sm font-medium text-ink">{ANALYSIS_TITLES[entry.analysis] ?? entry.analysis}</p>
                    <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">
                      {entry.missing_requirements.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                    {entry.near_misses.length > 0 && (
                      <p className="mt-1 text-xs text-ink-faint">
                        Closest candidate
                        {entry.near_misses.length === 1 ? "" : "s"}:{" "}
                        {entry.near_misses.map((n, i) => (
                          <span key={`${n.role}-${n.column}`}>
                            {i > 0 && "; "}
                            <span className="font-mono">{n.column}</span> for {n.role} - {n.why_rejected}
                          </span>
                        ))}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              {/* Adjacent to the refusals it answers, not on a settings
                  screen elsewhere - the question and the way to answer it
                  belong in the same place. */}
              <RoleConfirmation
                data={data}
                runRef={runRef}
                columns={selection.columnTypes ? Object.keys(selection.columnTypes) : []}
                onUpdated={setData}
              />
            </Card>
          )}

          {/* A run with everything detected still needs the withdraw path -
              otherwise a confirmation made earlier could never be undone.
              Only when there IS one: an empty card asks nothing. */}
          {notApplicable.length === 0 && Object.keys(data.detected_roles.confirmed_roles ?? {}).length > 0 && (
            <Card title="Column roles">
              <RoleConfirmation
                data={data}
                runRef={runRef}
                columns={selection.columnTypes ? Object.keys(selection.columnTypes) : []}
                onUpdated={setData}
              />
            </Card>
          )}

          {data.results.filter((r) => r.ran).map((result) => (
            <ResultCard key={result.analysis} result={result} />
          ))}

          {data.results.filter((r) => !r.ran && !notApplicable.some((n) => n.analysis === r.analysis)).map((result) => (
            <ResultCard key={result.analysis} result={result} />
          ))}
        </>
      )}

      {!loading && !data && !error && runRef === "" && (
        <Muted>Select a run above to see its business analyses.</Muted>
      )}

      {data === null && !loading && error !== null && !notFound && (
        <div className="mt-3">
          <Button onClick={() => selection.setRunRef(runRef)}>Retry</Button>
        </div>
      )}
    </div>
  );
}
