import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, apiFetch } from "../api/client";
import type { AnalysisFindingRecord, AnalysisResultRecord, AnalyticsRecord } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted, RunLabel } from "../components/ui";
import { RunNotFoundHelp, RunPicker, useRunSelection } from "../components/RunPicker";
import { chartForFinding, clusterScatter, segmentBar, type ChartSpec } from "../lib/analyticsCharts";
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
          <td className="px-3 py-2 font-medium">Band {String(p.band)}</td>
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

  return (
    <Card title={title}>
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
                <Badge value={candidate.confidence} />
              </span>
            ))}
          </div>

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
