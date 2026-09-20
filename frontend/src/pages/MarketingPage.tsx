import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, apiFetch } from "../api/client";
import type { MarketingRecord, MarketingFindingRecord, AnalyticsChartRecord } from "../api/types";
import { Badge, Card, ErrorMessage, Muted, Pager, RunLabel, usePagedList } from "../components/ui";
import { RunNotFoundHelp, RunPicker, useRunSelection } from "../components/RunPicker";
import { UploadIngestPanel } from "../components/UploadIngestPanel";
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
  const [params, setParams] = useSearchParams();
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
  const t = totals?.payload;

  // AD-SET FILTERING. Purely deterministic and purely local: the findings
  // are already persisted per ad set (payload.scope), so narrowing the view
  // is a filter over what the run produced, never a new request and never a
  // new model call. Clicking the selected ad set again clears it.
  const [selectedAdset, setSelectedAdset] = useState<string | null>(null);

  const adsets = Array.from(
    new Set(
      (data?.findings ?? [])
        .map((f) => (f.payload as { scope?: string | null }).scope)
        .filter((scope): scope is string => Boolean(scope))
    )
  ).sort();

  // Account-wide findings (no scope) stay visible under a filter: an ad set
  // is read against the account it sits in, and hiding the totals would
  // leave the reader comparing a number to nothing.
  const inScope = (f: MarketingFindingRecord) => {
    if (!selectedAdset) return true;
    const scope = (f.payload as { scope?: string | null }).scope;
    return !scope || scope === selectedAdset;
  };

  const visible = (data?.findings ?? []).filter(inScope);
  const warnings = visible.filter((f) => f.severity === "warning");
  const improvements = visible.filter((f) => f.severity === "improvement");
  const concentration = visible.filter((f) => f.finding_type === "spend_concentration");

  // A findings list belongs to one run AND one ad-set filter: narrowing to a
  // single ad set produces a different, usually much shorter list, and a page
  // number chosen against the unfiltered one means nothing there. Naming both
  // in the key is what makes a stale page unreadable rather than merely
  // corrected afterwards. The ad-set chips themselves are not filtered, so
  // they are keyed on the run alone and keep their page as you click through.
  const findingsKey = `${data?.run_id ?? ""}:${selectedAdset ?? "all"}`;
  const adsetPage = usePagedList(adsets, 48, data?.run_id ?? "");
  const concentrationPage = usePagedList(concentration, 10, findingsKey);
  const warningsPage = usePagedList(warnings, 10, findingsKey);
  const improvementsPage = usePagedList(improvements, 10, findingsKey);

  // An ENTRY POINT, not a second pipeline: the same /sources/upload and
  // /ingest endpoints the Sources page uses, through the same shared
  // component, so the file goes through the same detection, validation and
  // graph run. Only the copy and the landing page are specific to here.
  //
  // Always rendered, in ONE position. The run picker defaults to the newest
  // completed run, so "no run selected" is almost never true and hiding the
  // upload behind that condition made the entry point unreachable.
  //
  // It sits at the end of the page rather than under the picker so it never
  // pushes a report the user came to read off screen - and it is rendered
  // from a single place in the tree, because conditionally rendering it in
  // two positions made React remount it whenever the marketing fetch
  // resolved, silently discarding the file the user had just chosen.
  const uploadCard = (
        <Card
          title="Upload an ad-platform export"
          subtitle="Goes through the same ingestion, validation and detection as any other file."
        >
          <UploadIngestPanel
            idPrefix="marketing"
            autoIngest
            submitLabel="Upload and analyse"
            description={
              <>
                A campaign or ad-set level export from Meta Ads Manager, Google Ads or similar. It needs a reporting
                date and an amount-spent column, plus at least one of impressions, clicks, CTR or frequency;
                conversions and conversion value unlock cost-per-acquisition and ROAS. A platform summary or
                &ldquo;Total&rdquo; row is fine &mdash; it is excluded before anything is summed.
              </>
            }
            onIngested={(result) => {
              // Land on this run's Marketing view. If the file turns out not
              // to be ad data the run is still valid - the view then shows
              // the capability report rather than an error.
              const ref = result.run_number != null ? String(result.run_number) : result.run_id;
              selection.setRunRef(ref);
              setParams({ run: ref }, { replace: true });
            }}
          />
        </Card>

  );

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

          {adsets.length > 0 && (
            <Card
              title="Ad sets"
              subtitle="Select one to narrow this page to its own metrics, trend and warnings. Account-wide figures stay visible, so a filtered ad set is still read against the account it sits in."
            >
              <div className="flex flex-wrap gap-2">
                {adsetPage.visible.map((adset) => {
                  const active = selectedAdset === adset;
                  return (
                    <button
                      key={adset}
                      type="button"
                      aria-pressed={active}
                      data-testid="marketing-adset-chip"
                      className={
                        active
                          ? "rounded-sm bg-brand-600 px-3 py-1 text-sm text-on-accent"
                          : "rounded-sm border border-border-strong px-3 py-1 text-sm text-ink hover:bg-surface-sunken"
                      }
                      onClick={() => setSelectedAdset(active ? null : adset)}
                    >
                      {adset}
                    </button>
                  );
                })}
              </div>
              <Pager paged={adsetPage} noun="ad set" />
              {selectedAdset && (
                <p className="mt-3 text-sm text-ink-muted" data-testid="marketing-filter-note">
                  Showing <span className="font-medium text-ink">{selectedAdset}</span> and account-wide figures.{" "}
                  <button type="button" className="text-brand-600 underline" onClick={() => setSelectedAdset(null)}>
                    Show all ad sets
                  </button>
                </p>
              )}
            </Card>
          )}

          {concentration.length > 0 && (
            <Card
              title="Where the budget goes, and where the return comes from"
              subtitle="Computed by the same ABC/Pareto engine the analytics agent uses, applied to ad sets."
            >
              <ul>
                {concentrationPage.visible.map((f) => {
                  const p = f.payload as {
                    measured_over: string;
                    entity_count: number;
                    value_share: number;
                    band_cutoff: number;
                    top_entities: string[];
                    compared_against: string;
                  };
                  return (
                    <li key={f.id} className="border-b border-border py-2 last:border-b-0">
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        <Badge value={p.measured_over} tone="neutral" />
                        <span className="text-ink">
                          {p.entity_count} ad set(s) account for {(p.value_share * 100).toFixed(1)}% of{" "}
                          {p.measured_over}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-ink-faint">{p.compared_against}</p>
                      {p.top_entities.length > 0 && (
                        <p className="mt-1 text-sm text-ink-muted">
                          Band A: {p.top_entities.join(", ")}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
              <Pager paged={concentrationPage} noun="concentration finding" />
            </Card>
          )}

          <Card title="Needs a decision" subtitle="Every warning escalates to a person; nothing here is applied automatically.">
            {warnings.length === 0 ? (
              <Muted>No warning thresholds were breached in this period.</Muted>
            ) : (
              <>
                <ul>
                  {warningsPage.visible.map((f) => (
                    <Breach key={f.id} finding={f} />
                  ))}
                </ul>
                <Pager paged={warningsPage} noun="warning" />
              </>
            )}
          </Card>

          <Card title="Improvements" subtitle="Informational. Nothing here blocks a run.">
            {improvements.length === 0 ? (
              <Muted>No improvement opportunities were identified in this period.</Muted>
            ) : (
              <>
                <ul>
                  {improvementsPage.visible.map((f) => (
                    <Breach key={f.id} finding={f} />
                  ))}
                </ul>
                <Pager paged={improvementsPage} noun="improvement" />
              </>
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

      {uploadCard}
    </div>
  );
}
