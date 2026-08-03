import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import type { ReportRecord } from "../api/types";
import { Badge, Button, Card, ErrorMessage, Muted, RunLabel } from "../components/ui";
import { loadPlotly } from "../lib/plotly";

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

function RecommendationLine({ line, knownClaimIds }: { line: string; knownClaimIds: Set<string> }) {
  const match = line.match(CLAIM_REFERENCE_PATTERN);
  if (!match) return <p className="text-sm text-slate-700">{line}</p>;
  const [, text, claimId] = match;
  if (!knownClaimIds.has(claimId)) {
    return <p className="text-sm text-slate-700">{text}</p>;
  }
  return (
    <p className="text-sm text-slate-700">
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
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const chartsContainerRef = useRef<HTMLDivElement>(null);

  async function loadReport(query: string) {
    if (!query) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const data = await apiFetch<ReportRecord>(`/reports/${query}`);
      setReport(data);
      // Normalize the box to the run number once resolved, so a load-by-
      // UUID still ends up showing (and re-loadable by) the short form.
      setRunQuery(data.run_number != null ? String(data.run_number) : data.run_id);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
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
            window.Plotly.newPlot(el, chart.figure_json.data, chart.figure_json.layout, { responsive: true });
          } catch {
            el.innerHTML = '<p class="text-sm text-slate-500">Chart could not be rendered.</p>';
          }
        }
      });
    });
  }, [report]);

  const sections = report?.narrative_text ? splitReportSections(report.narrative_text) : null;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Reports</h1>
      <div className="mb-6 flex items-center gap-3">
        <input
          value={runQuery}
          onChange={(e) => setRunQuery(e.target.value)}
          placeholder="run number (e.g. 17) or full run id"
          className="w-96 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
        />
        <Button variant="primary" onClick={() => loadReport(runQuery)} loading={loading} loadingText="Loading...">
          Load
        </Button>
      </div>

      <ErrorMessage error={error} />

      {sections && (
        <>
          <div className="mb-4 flex items-center gap-2 text-sm">
            <RunLabel runNumber={report!.run_number} runId={report!.run_id} />
          </div>

          {/* Quality context rendered FIRST and visually distinct - not an optional UI detail. */}
          <div className="mb-6 rounded-2xl border border-amber-300 bg-amber-50 p-6">
            <h2 className="mb-2 text-lg font-semibold text-amber-900">Data quality context</h2>
            <pre className="text-sm text-amber-900">{sections.quality}</pre>
          </div>

          <div className="mb-6 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">Generation mode:</span>
            <Badge value={report!.generation_mode} />
          </div>

          <Card title="Narrative">
            <pre className="text-sm text-slate-700">{sections.body}</pre>
          </Card>

          {sections.recommendations && (
            <Card title="Recommendations">
              <div className="flex flex-col gap-2">
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
            </Card>
          )}

          {report?.chart_refs && report.chart_refs.length > 0 && (
            <Card title="Charts">
              <div ref={chartsContainerRef} className="flex flex-col gap-6">
                {report.chart_refs.map((chart, i) => (
                  <div key={chart.chart_id}>
                    <Muted>{chart.title}</Muted>
                    <div id={`chart-${i}`} className="mt-2" />
                  </div>
                ))}
              </div>
            </Card>
          )}

          {report?.grounded_claims && report.grounded_claims.length > 0 && (
            <Card title="Evidence">
              <details id="evidence-section">
                <summary className="cursor-pointer text-sm font-medium text-brand-600">
                  {report.grounded_claims.length} grounded claim{report.grounded_claims.length === 1 ? "" : "s"} - click to expand
                </summary>
                <ul className="mt-3 flex flex-col gap-2">
                  {report.grounded_claims.map((c) => (
                    <li key={c.claim_id} id={c.claim_id} className="scroll-mt-24 rounded-lg bg-slate-50 p-3 text-sm">
                      <span className="mr-2 font-mono text-xs text-slate-400">{c.claim_id}</span>
                      {c.claim_text}
                    </li>
                  ))}
                </ul>
              </details>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
