import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, apiFetch, pollReportReady } from "../api/client";
import type { ReportRecord } from "../api/types";
import { Badge, Button, ErrorMessage, RunLabel } from "../components/ui";
import { PrivacySection } from "../components/PrivacySection";
import type { PrivacyClassificationRecord } from "../components/PrivacySection";
import { loadPlotly, themedLayout, withDesignColors } from "../lib/plotly";
import { askLink, predictLink } from "../lib/runLinks";

// Mirrors app/narrative/models.py::NarrativeReport.rendered_text()'s fixed
// section markers exactly - quality context is ALWAYS first, recommendations
// (when present) are ALWAYS their own labelled section. See that method's
// docstring: "Never reordered per generation_mode."
function splitReportSections(text: string) {
  const qMarker = "## Data Quality Context";
  const rMarker = "## Recommendations";
  if (!text.includes(qMarker)) return { quality: text.trim(), body: "", recommendations: "" };
  const afterQuality = text.split(qMarker)[1];
  let qualityAndBody = afterQuality;
  let recommendations = "";
  if (afterQuality.includes(rMarker)) {
    const [before, after] = afterQuality.split(rMarker);
    qualityAndBody = before;
    recommendations = after;
  }
  const parts = qualityAndBody.replace(/^\n+/, "").split("\n\n");
  const quality = parts[0]?.trim() ?? "";
  const body = parts.slice(1).join("\n\n").trim();
  return { quality, body, recommendations: recommendations.trim() };
}

// Part 3 CLAIM REFERENCES: NarrativeReport.rendered_text() (app/narrative/
// models.py) appends "(see claim-N)" to every recommendation line, verbatim
// - "- {rec.text} (see {rec.claim_id})". Turns that suffix into a link that
// scrolls to the matching entry in the Evidence section below, IF claim-N
// is actually present in this report's grounded_claims (it always should
// be, since a recommendation's claim_id was validated at generation time -
// but the report is read from the DB independently of that guarantee, so
// this re-checks rather than assuming). A reference to a claim id absent
// from the list renders the recommendation text with the dead reference
// dropped, never a link to nowhere.
const CLAIM_REFERENCE_PATTERN = /^(.*?)\s*\(see (claim-\d+)\)\s*$/;

/** Exports this run as a deck. The deck is a RENDERER over the same
 *  persisted artifacts this page is showing, so it can never say something
 *  the screen does not - and generation is polled with visible elapsed time
 *  rather than a bare spinner, because rendering every chart through a real
 *  browser takes seconds. */
function RecommendationLine({ line, knownClaimIds }: { line: string; knownClaimIds: Set<string> }) {
  const match = line.match(CLAIM_REFERENCE_PATTERN);
  if (!match) return <p className="text-base leading-relaxed text-ink">{line}</p>;
  const [, text, claimId] = match;
  if (!knownClaimIds.has(claimId)) {
    return <p className="text-base leading-relaxed text-ink">{text}</p>;
  }
  return (
    <p className="text-base leading-relaxed text-ink">
      {text}{" "}
      <a
        href={`#${claimId}`}
        className="text-brand-600 hover:underline"
        onClick={(e) => {
          e.preventDefault();
          const details = document.getElementById("evidence-section") as HTMLDetailsElement | null;
          if (details) details.open = true;
          document.getElementById(claimId)?.scrollIntoView({ behavior: "smooth", block: "center" });
        }}
      >
        (see {claimId})
      </a>
    </p>
  );
}

export default function ReportsPage() {
  const [params] = useSearchParams();
  // Dashboard UX pass, Part 1: accepts a plain run_number ("17"/"#17") or
  // the full UUID - see app/id_lookup.py::resolve_run. A number match is
  // always exact (run_number is UNIQUE), so - unlike the earlier id-prefix
  // scheme this replaced - there is no ambiguity path to handle here.
  const [runQuery, setRunQuery] = useState(params.get("run") ?? "");
  const [report, setReport] = useState<ReportRecord | null>(null);
  // The privacy classification lives on the run, not the report, so it is
  // fetched alongside - a run with no report still has one.
  const [privacy, setPrivacy] = useState<PrivacyClassificationRecord | null | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  // Set while waiting out the completed-but-not-yet-narrated window, so the
  // wait shows elapsed time rather than a bare spinner (DESIGN.md: never a
  // spinner with no sense of whether it is stuck).
  const [waitingFor, setWaitingFor] = useState<string | null>(null);
  const chartsContainerRef = useRef<HTMLDivElement>(null);

  async function loadReport(query: string) {
    if (!query) return;
    setLoading(true);
    setError(null);
    setReport(null);
    setPrivacy(undefined);
    try {
      const data = await apiFetch<ReportRecord>(`/reports/${query}`);
      setReport(data);
      // The classification is on the RUN. A failure here must not fail the
      // report - the section renders its own "not classified" state.
      apiFetch<{ privacy?: PrivacyClassificationRecord | null }>(`/ingest/${query}/status`)
        .then((status) => setPrivacy(status.privacy ?? null))
        .catch(() => setPrivacy(null));
      // Normalize the box to the run number once resolved, so a load-by-
      // UUID still ends up showing (and re-loadable by) the short form.
      setRunQuery(data.run_number != null ? String(data.run_number) : data.run_id);
    } catch (err) {
      // explore_node marks a run "completed" BEFORE narrate_node writes the
      // report, so a 404 here often means "not yet", not "never" - the case
      // anyone following a View report link straight after an ingest hits.
      // Wait it out once, with visible elapsed time, instead of showing a
      // red error for something that is still on its way.
      if (err instanceof ApiError && err.status === 404) {
        try {
          setWaitingFor("0s");
          const status = await pollReportReady(query, (ms) => setWaitingFor(`${Math.round(ms / 1000)}s`));
          if (status.report?.available) {
            const data = await apiFetch<ReportRecord>(`/reports/${query}`);
            setReport(data);
            setRunQuery(data.run_number != null ? String(data.run_number) : data.run_id);
            return;
          }
        } catch {
          // Fall through to the original error - the backend's own message
          // distinguishes "still being written" from "this run has none",
          // and it is more specific than anything invented here.
        } finally {
          setWaitingFor(null);
        }
      }
      setError(err);
    } finally {
      setLoading(false);
      setWaitingFor(null);
    }
  }

  useEffect(() => {
    if (params.get("run")) loadReport(params.get("run")!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!report?.chart_refs?.length) return;
    loadPlotly().then(() => {
      report.chart_refs!.forEach((chart, i) => {
        const el = document.getElementById(`chart-${i}`);
        if (el && window.Plotly) {
          try {
            // The panel's own caption (styled to match this page's type
            // system) is the only title shown - Plotly's native title,
            // rendered a second time inside the SVG in its own font, was
            // pure redundant chrome (the same string, twice, in two
            // different typefaces).
            const layout = themedLayout({ ...(chart.figure_json.layout as Record<string, unknown>), title: undefined, margin: { t: 16, r: 16, b: 40, l: 48 } });
            const data = withDesignColors(chart.figure_json.data as unknown[], chart.chart_type);
            window.Plotly.newPlot(el, data, layout, { responsive: true, displayModeBar: false });
          } catch {
            el.innerHTML = '<p class="text-sm text-ink-muted">Chart could not be rendered.</p>';
          }
        }
      });
    });
  }, [report]);

  const sections = report?.narrative_text ? splitReportSections(report.narrative_text) : null;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-page-title text-ink">Reports</h1>
        <div className="flex items-center gap-3">
          <input
            value={runQuery}
            onChange={(e) => setRunQuery(e.target.value)}
            placeholder="run number (e.g. 17) or full run id"
            className="w-80 rounded-sm border border-border-strong px-3 py-1.5 text-sm"
          />
          <Button variant="primary" onClick={() => loadReport(runQuery)} loading={loading} loadingText="Loading...">
            Load
          </Button>
          {runQuery.trim() && (
            <Link
              to={`/export?run=${encodeURIComponent(runQuery.trim())}`}
              className="text-sm font-medium text-brand-600 underline"
            >
              Export this run as a deck
            </Link>
          )}
        </div>
      </div>

      {waitingFor !== null && (
        <p className="mb-4 text-sm text-ink-muted">
          Exploration finished and the narrative is still being written &mdash; waiting {waitingFor}...
        </p>
      )}

      <ErrorMessage error={error} />

      {report && (
        <div className="mb-6">
          <PrivacySection privacy={privacy} />
        </div>
      )}

      {sections && (
        <>
          {/* Run identity + generation mode - both stay plainly visible
              regardless of mode (hard constraint, never demoted). */}
          <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
            <RunLabel runNumber={report!.run_number} runId={report!.run_id} />
            <span className="text-ink-muted" aria-hidden="true">/</span>
            <span className="text-ink-muted">Generation mode:</span>
            <Badge value={report!.generation_mode} />
            {/* Carry this exact run into Ask/Predict (?run=), the same way
                Sources does - reading a report is the most likely moment to
                want to interrogate or forecast the same data, and neither
                screen should need an id pasted to do it. */}
            <span className="text-ink-muted" aria-hidden="true">/</span>
            <Link className="text-brand-600 hover:underline" to={askLink(report!.run_id, report!.run_number)}>
              Ask about this run
            </Link>
            <span className="text-ink-muted" aria-hidden="true">&middot;</span>
            <Link className="text-brand-600 hover:underline" to={predictLink(report!.run_id, report!.run_number)}>
              Predict from this run
            </Link>
          </div>

          {/* Quality context rendered FIRST and visually distinct - a full
              bordered, tinted panel, never demoted to plain-card weight or a
              trailing caveat. A fluent report reading as authoritative over
              repaired/unvalidated data is the one thing this page must never
              let happen. */}
          <div className="panel mb-8 rounded-md border border-status-caution bg-status-caution-tint p-6">
            <h2 className="mb-2 text-card-title text-status-caution">Data quality context</h2>
            <div className="max-w-[68ch] whitespace-pre-wrap text-sm leading-relaxed text-ink">{sections.quality}</div>
          </div>

          {/* Narrative + recommendations read as a DOCUMENT (reading scale,
              measured width, no card chrome) - distinct in kind from the
              data panels below, not just another equal-weight box. */}
          <div className="mb-8 max-w-[72ch]">
            <h2 className="mb-3 text-card-title text-ink">Narrative</h2>
            <div className="whitespace-pre-wrap text-base leading-relaxed text-ink">{sections.body}</div>
          </div>

          {sections.recommendations && (
            <div className="mb-10 max-w-[72ch]">
              <h2 className="mb-3 text-card-title text-ink">Recommendations</h2>
              <div className="flex flex-col gap-3">
                {sections.recommendations
                  .split("\n")
                  .filter((line) => line.trim())
                  .map((line, i) => (
                    <RecommendationLine
                      key={i}
                      line={line.replace(/^-\s*/, "")}
                      knownClaimIds={new Set((report?.grounded_claims ?? []).map((c) => c.claim_id))}
                    />
                  ))}
              </div>
            </div>
          )}

          {/* Charts: a real panel grid, not a flat scroll of equal-weight
              boxes - each chart is its own bordered panel so the reader can
              scan the set rather than page through one long column. */}
          {report?.chart_refs && report.chart_refs.length > 0 && (
            <div className="mb-8">
              <h2 className="mb-3 text-card-title text-ink">Charts</h2>
              <div ref={chartsContainerRef} className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {report.chart_refs.map((chart, i) => (
                  <div key={chart.chart_id} className="panel rounded-md border border-border bg-surface p-4">
                    <p className="text-sm font-medium text-ink">{chart.title}</p>
                    <div id={`chart-${i}`} className="mt-1" />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Evidence: supporting reference material, collapsed by default,
              always last - it backs the narrative above, it doesn't compete
              with it for the reader's first attention. */}
          {report?.grounded_claims && report.grounded_claims.length > 0 && (
            <div className="panel rounded-md border border-border bg-surface p-5">
              <details id="evidence-section">
                <summary className="cursor-pointer text-sm font-medium text-brand-600">
                  {report.grounded_claims.length} grounded claim{report.grounded_claims.length === 1 ? "" : "s"} - click to expand
                </summary>
                <ul className="mt-3 flex flex-col gap-2">
                  {report.grounded_claims.map((c) => (
                    <li key={c.claim_id} id={c.claim_id} className="scroll-mt-24 rounded-sm bg-surface-sunken p-3 text-sm text-ink">
                      <span className="mr-2 font-mono text-xs text-ink-faint">{c.claim_id}</span>
                      {c.claim_text}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}
        </>
      )}
    </div>
  );
}
