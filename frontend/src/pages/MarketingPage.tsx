import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, apiFetch } from "../api/client";
import type { MarketingRecord, MarketingFindingRecord, AnalyticsChartRecord } from "../api/types";
import { Badge, Card, ErrorMessage, Muted, RunLabel } from "../components/ui";
import { RunNotFoundHelp, RunPicker, useRunSelection } from "../components/RunPicker";
import { applyAnalyticsColours, loadPlotly } from "../lib/plotly";

// The eighth agent's view. Key values first, then what needs a decision,
// then what is merely worth knowing - the same order of consequence the
// Approvals page uses, for the same reason: a reader's eye should reach the
// thing that needs them before the thing that does not.

function MarketingChart({ chart, id }: { chart: AnalyticsChartRecord; id: string }) {
  // The SAME persisted-spec path the analytics charts use. Only the colour
  // roles are resolved here, against the live theme.
  useEffect(() => {
    loadPlotly().then(() => {
      const el = document.getElementById(id);
      if (el && window.Plotly) {
        try {
          const { data, layout } = applyAnalyticsColours(chart);
          window.Plotly.newPlot(el, data, layout, { responsive: true, displayModeBar: false });
        } catch {
          el.innerHTML = '<p class="text-sm text-ink-muted">Chart could not be rendered.</p>';
        }
      }
    });
  }, [chart, id]);
  return <div id={id} className="mt-2" />;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function KeyValue({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-[9rem]">
      <div className="text-label uppercase text-ink-faint">{label}</div>
      <div className="mt-0.5 font-mono text-ink">{value}</div>
      {/* An undefined figure states WHY rather than rendering as zero - an
          account with no conversions has no CPA, and that is a fact about
          the campaign, not a gap in the report. */}
      {note && <div className="mt-0.5 text-xs text-ink-muted">{note}</div>}
    </div>
  );
}

function Breach({ finding }: { finding: MarketingFindingRecord }) {
  const p = finding.payload;
  const tone = finding.severity === "warning" ? "caution" : "neutral";
  return (
    <li className="border-b border-border py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={finding.severity === "warning" ? "needs a decision" : "improvement"} tone={tone} />
        <span className="font-medium text-ink">{p.finding_type.replace(/_/g, " ")}</span>
        {p.scope && <span className="text-sm text-ink-muted">— {p.scope}</span>}
      </div>
      {/* The four things a warning needs to be judgeable: what was measured,
          what it came out at, what the threshold was, and what that
          threshold was derived from. */}
      <div className="mt-2 flex flex-wrap gap-x-8 gap-y-1 text-sm">
        <span>
          <span className="text-ink-muted">observed {p.metric}:</span>{" "}
          <span className="font-mono text-ink">{num(p.observed, 4)}</span>
        </span>
        <span>
          <span className="text-ink-muted">threshold:</span> <span className="font-mono text-ink">{num(p.threshold, 4)}</span>
        </span>
      </div>
      <p className="mt-1 text-sm text-ink-muted">Compared against {p.compared_against}</p>
      {p.undefined_note && <p className="mt-1 text-xs text-ink-faint">{p.undefined_note}</p>}
    </li>
  );
}

export default function MarketingPage() {
  const [params] = useSearchParams();
  const selection = useRunSelection(params.get("run") ?? "");
  const [data, setData] = useState<MarketingRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const runRef = selection.runRef.trim();

  useEffect(() => {
    if (!runRef) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    apiFetch<MarketingRecord>(`/marketing/${encodeURIComponent(runRef)}`)
      .then((body) => !cancelled && setData(body))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runRef]);

  const notFound = error instanceof ApiError && error.status === 404;
  const totals = data?.findings.find((f) => f.finding_type === "account_totals");
  const warnings = (data?.findings ?? []).filter((f) => f.severity === "warning");
  const improvements = (data?.findings ?? []).filter((f) => f.severity === "improvement");
  const t = totals?.payload;

  return (
    <div>
      <h1 className="mb-2 text-page-title text-ink">Marketing</h1>
      <Muted>
        Ad-campaign performance, computed deterministically from this run's repaired data. No language model takes part
        in deriving a metric or deciding a threshold, and nothing here changes a campaign.
      </Muted>

      <div className="my-6">
        <Card>
          <RunPicker selection={selection} idPrefix="marketing" />
        </Card>
      </div>

      {loading && <Muted>Loading marketing analysis...</Muted>}
      {!notFound && <ErrorMessage error={error} />}
      <RunNotFoundHelp error={error} selection={selection} />

      {notFound && (
        <Card title="No marketing analysis for this run">
          <Muted>
            The Marketing Agent runs once, after business analytics, on completed runs only. A run from before this
            agent existed will not have one.
          </Muted>
        </Card>
      )}

      {/* A run that did not qualify says so, and names what was missing -
          never an empty page a reader has to interpret. */}
      {data && !data.applicable && (
        <Card title="This run is not an ad-campaign export">
          <p className="text-sm text-ink">{data.not_applicable_reason}</p>
          {data.missing_roles.length > 0 && (
            <p className="mt-3 text-sm text-ink-muted">
              Missing: {data.missing_roles.map((r) => r.replace(/_/g, " ")).join(", ")}
            </p>
          )}
          {Object.keys(data.resolved_roles ?? {}).length > 0 && (
            <p className="mt-2 text-sm text-ink-muted">
              Found:{" "}
              {Object.entries(data.resolved_roles)
                .map(([role, v]) => `${role.replace(/_/g, " ")} = ${v.column}`)
                .join(", ")}
            </p>
          )}
        </Card>
      )}

      {data && data.applicable && (
        <>
          <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
            <RunLabel runNumber={data.run_number} runId={data.run_id} />
            {data.preprocessing && <Badge value={`grain: ${data.preprocessing.grain}`} tone="neutral" />}
          </div>

          {/* KEY VALUES first, plainly, with the period they cover. */}
          {t && (
            <Card
              title="Key values"
              subtitle={t.period_start ? `${t.period_start} to ${t.period_end}` : "period not recorded"}
            >
              <div className="flex flex-wrap gap-x-10 gap-y-4">
                <KeyValue label="Spend" value={num(t.total_spend)} />
                <KeyValue label="Impressions" value={t.total_impressions?.toLocaleString() ?? "—"} />
                <KeyValue label="Clicks" value={t.total_clicks?.toLocaleString() ?? "—"} />
                <KeyValue label="Conversions" value={t.total_conversions?.toLocaleString() ?? "—"} />
                <KeyValue label="Blended CPA" value={num(t.blended_cpa)} note={t.undefined?.blended_cpa} />
                <KeyValue label="Blended ROAS" value={num(t.blended_roas)} note={t.undefined?.blended_roas} />
                <KeyValue
                  label="Blended CTR"
                  value={t.blended_ctr != null ? `${(t.blended_ctr * 100).toFixed(2)}%` : "—"}
                  note={t.undefined?.blended_ctr}
                />
              </div>
            </Card>
          )}

          <Card title="Needs a decision" subtitle="Every warning escalates to a person; nothing here is applied automatically.">
            {warnings.length === 0 ? (
              <Muted>No warning thresholds were breached in this period.</Muted>
            ) : (
              <ul>
                {warnings.map((f) => (
                  <Breach key={f.id} finding={f} />
                ))}
              </ul>
            )}
          </Card>

          <Card title="Improvements" subtitle="Informational. Nothing here blocks a run.">
            {improvements.length === 0 ? (
              <Muted>No improvement opportunities were identified in this period.</Muted>
            ) : (
              <ul>
                {improvements.map((f) => (
                  <Breach key={f.id} finding={f} />
                ))}
              </ul>
            )}
          </Card>

          {(data.charts ?? []).map((chart) => (
            <Card key={chart.chart_id} title={chart.title}>
              <MarketingChart chart={chart} id={`marketing-chart-${chart.kind}`} />
            </Card>
          ))}

          {/* What was derived versus what was in the file. */}
          {data.preprocessing && (
            <Card title="How these numbers were prepared">
              <p className="text-sm text-ink-muted">
                {data.preprocessing.rows_in} row(s) in, {data.preprocessing.rows_out} after aggregating to{" "}
                {data.preprocessing.grain}
                {data.preprocessing.summary_rows_excluded > 0 &&
                  `, with ${data.preprocessing.summary_rows_excluded} platform summary row(s) excluded`}
                .
              </p>
              {data.preprocessing.normalisations.length > 0 && (
                <ul className="mt-3 text-sm text-ink-muted">
                  {data.preprocessing.normalisations.map((n, i) => (
                    <li key={i}>
                      <span className="font-mono text-ink">{n.column}</span>: {n.transformation}
                    </li>
                  ))}
                </ul>
              )}
              {data.preprocessing.derived_metrics.length > 0 && (
                <ul className="mt-3 text-sm">
                  {data.preprocessing.derived_metrics.map((d) => (
                    <li key={d.metric} className="text-ink-muted">
                      <span className="font-mono text-ink">{d.metric}</span> = {d.formula}
                      {d.undefined_reason && <span className="text-ink-faint"> — {d.undefined_reason}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          {data.skipped_rules.length > 0 && (
            <Card title="Rules that did not run" subtitle="Each needs data this export did not carry.">
              <ul className="text-sm text-ink-muted">
                {data.skipped_rules.map((s, i) => (
                  <li key={i} className="border-b border-border py-2 last:border-b-0">
                    <span className="font-medium text-ink">{s.rule.replace(/_/g, " ")}</span> — {s.reason}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
