import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, apiFetch } from "../api/client";
import type { AgricultureRecord, AgricultureFindingRecord, AnalyticsChartRecord } from "../api/types";
import { Badge, Card, ErrorMessage, Muted, Pager, RunLabel, usePagedList } from "../components/ui";
import { loadPlotly, applyAnalyticsColours } from "../lib/plotly";

// The Agriculture Agent's view. Key values first, then what needs a decision,
// then improvements - the same order the Marketing page uses, because the
// question a reader arrives with is the same one.
//
// A run that does not qualify shows the REASON and the missing roles, never a
// blank page: "this source is not agricultural production data, and here is
// what it was missing" is an answer.

function AgricultureChart({ chart, id }: { chart: AnalyticsChartRecord; id: string }) {
  // The SAME persisted-spec path every other chart uses. Nothing about the
  // figure is derived in the browser; only the colour roles are resolved.
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

function KeyValue({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div>
      <div className="text-label uppercase text-ink-faint">{label}</div>
      <div className="text-lg text-ink">{value}</div>
      {note && <div className="text-sm text-ink-faint">{note}</div>}
    </div>
  );
}

function Breach({ finding }: { finding: AgricultureFindingRecord }) {
  const p = finding.payload;
  const tone = finding.severity === "warning" ? "caution" : "neutral";
  return (
    <li className="border-b border-border py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <Badge value={finding.severity === "warning" ? "needs a decision" : "improvement"} tone={tone} />
        <span className="font-medium text-ink">{String(p.finding_type).replace(/_/g, " ")}</span>
        {p.scope && <span className="text-sm text-ink-muted">&mdash; {p.scope}</span>}
      </div>
      <div className="mt-1 text-sm text-ink">
        {p.metric}: observed <span className="font-mono">{Number(p.observed).toLocaleString()}</span>, threshold{" "}
        <span className="font-mono">{Number(p.threshold).toLocaleString()}</span>
      </div>
      {/* The comparison basis is never optional: a threshold with no stated
          basis is a magic number by another route. */}
      <p className="mt-1 text-sm text-ink-faint">Compared against {p.compared_against}</p>
      {p.undefined_note && <p className="mt-1 text-sm text-ink-faint">{p.undefined_note}</p>}
    </li>
  );
}

export default function AgriculturePage() {
  const [params] = useSearchParams();
  const [runQuery, setRunQuery] = useState(params.get("run") ?? "");
  const [data, setData] = useState<AgricultureRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  // Deterministic filtering only, exactly like the Marketing page: the
  // findings are already persisted per district-crop, so narrowing the view
  // is a filter over what the run produced - no request, no model call.
  const [selectedScope, setSelectedScope] = useState<string | null>(null);

  useEffect(() => {
    const runRef = params.get("run");
    if (!runRef) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiFetch<AgricultureRecord>(`/agriculture/${runRef}`)
      .then((body) => !cancelled && setData(body))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    setRunQuery(runRef);
    return () => {
      cancelled = true;
    };
  }, [params]);

  const notFound = error instanceof ApiError && error.status === 404;
  const totals = data?.findings.find((f) => f.finding_type === "season_totals");
  const t = totals?.payload;

  const scopes = Array.from(
    new Set(
      (data?.findings ?? [])
        .map((f) => f.payload?.scope as string | undefined)
        .filter((scope): scope is string => Boolean(scope))
    )
  ).sort();

  // Same two-part key as the marketing pack: a findings list belongs to one
  // run and one scope filter. Measured on 7,680 rows of district-crop data,
  // this page rendered 655 scope chips and 1,328 findings in one column.
  const findingsKey = `${data?.run_id ?? ""}:${selectedScope ?? "all"}`;
  const scopePage = usePagedList(scopes, 48, data?.run_id ?? "");

  const visible = (data?.findings ?? []).filter((f) => {
    if (!selectedScope) return true;
    const scope = f.payload?.scope as string | undefined;
    return !scope || scope === selectedScope;
  });
  const warnings = visible.filter((f) => f.severity === "warning");
  const improvements = visible.filter((f) => f.severity === "improvement");
  const warningsPage = usePagedList(warnings, 10, findingsKey);
  const improvementsPage = usePagedList(improvements, 10, findingsKey);

  return (
    <div>
      <h1 className="mb-2 text-page-title text-ink">Agriculture</h1>
      <Muted>
        Crop production statistics: yield, area and rainfall by district, crop and season. Deterministic rules only
        &mdash; no language model is involved on this page.
      </Muted>

      <div className="my-6 flex flex-wrap items-center gap-3">
        <input
          id="agriculture-run"
          value={runQuery}
          onChange={(e) => setRunQuery(e.target.value)}
          placeholder="run number (e.g. 17)"
          className="w-64 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
        />
        <button
          type="button"
          className="rounded-sm bg-brand-600 px-4 py-1.5 text-sm text-on-accent hover:bg-brand-700 disabled:opacity-50"
          disabled={loading || !runQuery}
          onClick={() => {
            setLoading(true);
            setError(null);
            apiFetch<AgricultureRecord>(`/agriculture/${runQuery}`)
              .then(setData)
              .catch(setError)
              .finally(() => setLoading(false));
          }}
        >
          {loading ? "Loading…" : "Load"}
        </button>
      </div>

      {notFound ? <Muted>No agriculture analysis for that run.</Muted> : <ErrorMessage error={error} />}

      {data && !data.applicable && (
        <Card title="This run is not agricultural production data">
          <p className="text-sm text-ink">{data.not_applicable_reason}</p>
          {data.missing_roles.length > 0 && (
            <p className="mt-2 text-sm text-ink-faint">Roles not found: {data.missing_roles.join(", ")}</p>
          )}
        </Card>
      )}

      {data && data.applicable && (
        <div className="flex flex-col gap-6">
          <Card title="Runs">
            <RunLabel runNumber={data.run_number} runId={data.run_id} />
          </Card>

          {t && (
            <Card title="Key values" subtitle="Reported plainly. Nothing here is a finding about anything.">
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <KeyValue
                  label="Total area"
                  value={t.total_area != null ? Number(t.total_area).toLocaleString() : "—"}
                  note={t.undefined?.total_area}
                />
                <KeyValue
                  label="Total production"
                  value={t.total_production != null ? Number(t.total_production).toLocaleString() : "—"}
                  note={t.undefined?.total_production}
                />
                <KeyValue
                  label="Average yield"
                  value={t.average_yield != null ? Number(t.average_yield).toLocaleString() : "—"}
                  note={t.yield_basis ?? t.undefined?.average_yield}
                />
                <KeyValue
                  label="Rainfall (mean)"
                  value={t.rainfall_mean != null ? Number(t.rainfall_mean).toLocaleString() : "—"}
                  note={
                    t.rainfall_mean != null
                      ? `range ${Number(t.rainfall_min).toLocaleString()} – ${Number(t.rainfall_max).toLocaleString()}`
                      : t.undefined?.rainfall_mean
                  }
                />
              </div>
              {t.top_crops?.length > 0 && (
                <p className="mt-4 text-sm text-ink-muted">
                  Top crops by production: {t.top_crops.map((c: { crop: string }) => c.crop).join(", ")}
                </p>
              )}
            </Card>
          )}

          {scopes.length > 0 && (
            <Card
              title="District and crop"
              subtitle="Select one to narrow this page to it. Account-wide key values stay visible."
            >
              <div className="flex flex-wrap gap-2">
                {scopePage.visible.map((scope) => {
                  const active = selectedScope === scope;
                  return (
                    <button
                      key={scope}
                      type="button"
                      aria-pressed={active}
                      data-testid="agriculture-scope-chip"
                      className={
                        active
                          ? "rounded-sm bg-brand-600 px-3 py-1 text-sm text-on-accent"
                          : "rounded-sm border border-border-strong px-3 py-1 text-sm text-ink hover:bg-surface-sunken"
                      }
                      onClick={() => setSelectedScope(active ? null : scope)}
                    >
                      {scope}
                    </button>
                  );
                })}
              </div>
              <Pager paged={scopePage} noun="district-crop pair" />
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
              <Muted>Nothing to suggest for this period.</Muted>
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
              <AgricultureChart chart={chart} id={`agriculture-chart-${chart.kind}`} />
            </Card>
          ))}

          {data.skipped_rules.length > 0 && (
            <Card title="Rules that did not run" subtitle="Each names the requirement it was missing.">
              <ul>
                {data.skipped_rules.map((s, i) => (
                  <li key={`${s.rule}-${i}`} className="border-b border-border py-2 text-sm last:border-b-0">
                    <span className="font-medium text-ink">{s.rule}</span>{" "}
                    <span className="text-ink-faint">&mdash; {s.reason}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
