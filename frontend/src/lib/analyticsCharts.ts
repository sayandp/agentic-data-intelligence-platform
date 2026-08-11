import { chartAccent, chartCategorical, chartColorscale, chartNeutral, chartSequential, themedLayout } from "./plotly";
import type { AnalysisFindingRecord } from "../api/types";

// Chart selection is a SWITCH ON finding_type - a closed set decided by the
// backend's schema, never by an LLM and never by a heuristic over the data.
// Same rule app/narrative/charts.py follows for report charts: the shape of
// the finding determines the shape of the picture, deterministically.
//
// Colours come from the theme tokens (lib/plotly.ts), so these follow
// Default/Dark/Aurora like every other chart in the app.

export interface ChartSpec {
  data: unknown[];
  layout: Record<string, unknown>;
}

/** Returns null when a finding type has no chart worth drawing - a rule
 *  table reads better as a table, and inventing a picture for it would be
 *  decoration. */
export function chartForFinding(finding: AnalysisFindingRecord): ChartSpec | null {
  const p = finding.payload;
  switch (finding.finding_type) {
    case "concentration_curve":
      return paretoCurve(p);
    case "retention_matrix":
      return cohortHeatmap(p);
    default:
      return null;
  }
}

/** The Pareto curve: cumulative value share against cumulative entity
 *  share, with the diagonal drawn for reference. The gap between the two
 *  IS the concentration. */
function paretoCurve(p: Record<string, unknown>): ChartSpec {
  const x = (p.cumulative_entity_share as number[]).map((v) => v * 100);
  const y = (p.cumulative_value_share as number[]).map((v) => v * 100);
  return {
    data: [
      {
        x,
        y,
        type: "scatter",
        mode: "lines",
        name: "cumulative value",
        line: { color: chartAccent(), width: 2 },
      },
      {
        // Perfect equality. Without it a reader has no reference for
        // whether the curve is bowed at all.
        x: [0, 100],
        y: [0, 100],
        type: "scatter",
        mode: "lines",
        name: "even contribution",
        line: { color: chartNeutral(), width: 1, dash: "dot" },
        hoverinfo: "skip",
      },
    ],
    layout: themedLayout({
      xaxis: { title: "cumulative share of entities (%)", range: [0, 100] },
      yaxis: { title: "cumulative share of value (%)", range: [0, 100] },
      margin: { t: 16, r: 16, b: 48, l: 56 },
      showlegend: true,
    }),
  };
}

/** The cohort triangle. `null` cells are periods that have NOT HAPPENED yet
 *  for that cohort - Plotly renders them as gaps, which is exactly right:
 *  an unobserved period must not look like 0% retention. */
function cohortHeatmap(p: Record<string, unknown>): ChartSpec {
  const matrix = p.retained_share as (number | null)[][];
  return {
    data: [
      {
        type: "heatmap",
        z: matrix.map((row) => row.map((v) => (v === null ? null : v * 100))),
        x: p.periods_since_acquisition as number[],
        y: p.cohort_labels as string[],
        // ORDERED: "periods since acquisition" has a direction, so this
        // is a single-hue ramp, never the categorical palette. Zero stays
        // transparent so an unobserved cell reads as absent, not as 0%.
        colorscale: [[0, "rgba(0,0,0,0)"], ...chartColorscale().slice(1)],
        hoverongaps: false,
        colorbar: { title: "% retained" },
      },
    ],
    layout: themedLayout({
      xaxis: { title: `periods since acquisition (${p.granularity})`, dtick: 1 },
      yaxis: { title: "cohort", autorange: "reversed" },
      margin: { t: 16, r: 16, b: 48, l: 88 },
    }),
  };
}

/** A/B/C is an ORDERED partition - A holds the most value, C the least -
 *  so the bands take three steps of the sequential ramp rather than three
 *  unrelated hues. Three arbitrary colours would say "these are different
 *  kinds of thing"; the ramp says "these are the same thing, ranked",
 *  which is what an ABC split actually is. */
export function paretoBandColor(band: string): string {
  const ramp = chartSequential();
  const step: Record<string, number> = { A: 4, B: 2, C: 1 };
  return ramp[step[band] ?? 1];
}

/** Segment sizes as a horizontal bar - readable with long segment names in
 *  a way a treemap is not, and it keeps value share comparable across
 *  segments on a shared axis. */
export function segmentBar(findings: AnalysisFindingRecord[]): ChartSpec | null {
  if (!findings.length) return null;
  const labels = findings.map((f) => String(f.payload.segment));
  return {
    data: [
      {
        type: "bar",
        orientation: "h",
        x: findings.map((f) => (f.payload.value_share as number) * 100),
        y: labels,
        // ONE accent, not the categorical palette. That palette is built
        // from the four STATUS colours, so cycling it here painted
        // "promising" red and "lost" green - colour asserting a verdict
        // opposite to the segment's meaning. Bar length already carries
        // the comparison; colour would only add a false signal.
        marker: { color: chartAccent() },
        hovertemplate: "%{y}: %{x:.1f}% of value<extra></extra>",
      },
    ],
    layout: themedLayout({
      xaxis: { title: "share of value (%)" },
      yaxis: { title: "", automargin: true },
      margin: { t: 16, r: 16, b: 48, l: 16 },
    }),
  };
}

/** Cluster scatter: recency against monetary, sized by frequency. Three
 *  dimensions is what RFM has, and the two most legible go on the axes. */
export function clusterScatter(findings: AnalysisFindingRecord[]): ChartSpec | null {
  if (!findings.length) return null;
  return {
    data: findings.map((f, i) => {
      const centre = f.payload.centre as Record<string, number>;
      return {
        type: "scatter",
        mode: "markers+text",
        name: String(f.payload.segment),
        x: [centre.recency_days],
        y: [centre.monetary],
        text: [String(f.payload.segment)],
        textposition: "top center",
        marker: {
          // A cluster id is an UNORDERED category, so the categorical
          // palette is exactly right here - and now safe, because that
          // palette is no longer the status colours. One accent for every
          // cluster made a multi-cluster scatter unreadable.
          color: chartCategorical()[i % chartCategorical().length],
          size: Math.max(12, Math.min(48, (centre.frequency ?? 1) * 8)),
        },
      };
    }),
    layout: themedLayout({
      xaxis: { title: "mean recency (days)" },
      yaxis: { title: "mean value" },
      margin: { t: 16, r: 16, b: 48, l: 64 },
      showlegend: false,
    }),
  };
}
