// Shared by every page that renders a deterministic, backend-computed chart
// (ReportsPage.tsx's report charts, PredictPage.tsx's forecast chart) - one
// CDN-load implementation, never duplicated per page. Chart TYPE/data is
// always decided server-side (app/narrative/charts.py, app/modeling/
// automl.py) - this module only ever draws what it's given.
declare global {
  interface Window {
    Plotly?: { newPlot: (el: HTMLElement, data: unknown, layout: unknown, config?: unknown) => void };
  }
}

const PLOTLY_CDN_URL = "https://cdn.plot.ly/plotly-2.35.2.min.js";

export function loadPlotly(): Promise<void> {
  if (window.Plotly) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PLOTLY_CDN_URL;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("could not load Plotly from CDN"));
    document.head.appendChild(script);
  });
}

// Chart COLORS follow the active theme; chart TYPE and data do not exist
// here at all - those are chosen deterministically by the backend
// (app/narrative/charts.py, app/modeling/automl.py) and this module only
// ever draws what it is given. Reading the live custom properties rather
// than hardcoding hexes is what lets a `data-theme` swap re-color a chart
// with the rest of the interface.

/** Resolves a theme token to its current value. Falls back to the Default
 *  theme's value so a chart still renders if it is somehow drawn before the
 *  stylesheet is live. */
function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// THREE PALETTES, AND WHY THEY ARE SEPARATE.
//
// Colour here may describe data. It may never assert a VERDICT the data
// does not carry. This project shipped that bug once: the palette below
// used to be --chart-1..6, whose values WERE the four status colours, and
// a segment chart cycling it positionally painted "promising" red and
// "lost" green - the exact opposite of what those segments meant.
//
//   accent      one series, one colour
//   categorical unordered categories (products, regions, cluster ids)
//   sequential  ORDERED data only (a cohort's periods, A/B/C bands)
//
// Status tokens are absent from this module by construction: no function
// below reads a --status-* property, and no --chart-* property resolves to
// a status value in any theme (asserted by test). The chart layer cannot
// reach them, rather than being trusted not to.
export function chartAccent(): string {
  return token("--chart-accent", "#0B6E6E");
}

/** The neutral used for already-known data (e.g. a forecast's actuals),
 *  kept distinct from the accent so the accent still marks the one thing
 *  that is genuinely new information. */
export function chartNeutral(): string {
  return token("--chart-neutral", "#4B5563");
}

/** Fill for an uncertainty band. */
export function chartBand(): string {
  return token("--chart-band", "rgba(11,110,110,0.15)");
}

/** UNORDERED categories. Six hues chosen by simulating deuteranopia and
 *  protanopia and maximising the minimum pairwise separation (63.3 / 34.1 /
 *  28.0 for default / dark / aurora), then ordered by luminance so the
 *  series stay tellable apart in greyscale or on a monochrome print. */
export function chartCategorical(): string[] {
  return [
    token("--chart-cat-1", "#0B6E6E"),
    token("--chart-cat-2", "#404040"),
    token("--chart-cat-3", "#8C6D1F"),
    token("--chart-cat-4", "#CC79A7"),
    token("--chart-cat-5", "#56B4E9"),
    token("--chart-cat-6", "#E69F00"),
  ];
}

/** ORDERED data only - a single hue, light to dark. A rank, a score band
 *  or a period since acquisition has a direction; giving it six unrelated
 *  hues would hide the one thing that matters about it. */
export function chartSequential(): string[] {
  return [
    token("--chart-seq-1", "#CFE7E7"),
    token("--chart-seq-2", "#A9D4D4"),
    token("--chart-seq-3", "#4FA3A3"),
    token("--chart-seq-4", "#12807E"),
    token("--chart-seq-5", "#0A3D3D"),
  ];
}

/** The same ramp as a Plotly colorscale, for a continuous ordered surface
 *  such as a cohort triangle. */
export function chartColorscale(): Array<[number, string]> {
  const ramp = chartSequential();
  return ramp.map((color, i) => [i / (ramp.length - 1), color] as [number, string]);
}

/** Transparent paper/plot backgrounds plus themed grid, axis and font
 *  colors, merged into whatever layout the backend produced. Transparent
 *  is what lets a chart sit on a tinted or frosted panel without punching
 *  a white rectangle through it. */
export function themedLayout(layout: Record<string, unknown>): Record<string, unknown> {
  const grid = token("--chart-grid", "#E2E5EA");
  const axis = token("--chart-axis", "#6B7280");
  const ink = token("--chart-ink", "#12161C");

  const axisDefaults = {
    gridcolor: grid,
    zerolinecolor: grid,
    linecolor: grid,
    tickcolor: axis,
    tickfont: { color: axis },
    title: { font: { color: axis } },
  };

  const existingX = (layout.xaxis as Record<string, unknown>) ?? {};
  const existingY = (layout.yaxis as Record<string, unknown>) ?? {};

  return {
    ...layout,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: ink },
    legend: { ...((layout.legend as Record<string, unknown>) ?? {}), font: { color: ink } },
    xaxis: { ...axisDefaults, ...existingX, title: { ...axisDefaults.title, ...((existingX.title as object) ?? {}) } },
    yaxis: { ...axisDefaults, ...existingY, title: { ...axisDefaults.title, ...((existingY.title as object) ?? {}) } },
  };
}

export function withDesignColors(data: unknown[]): unknown[] {
  // THE SINGLE-SERIES RULE. One trace gets ONE colour, always the accent.
  // A lone bar or histogram already carries its comparison in the bar
  // LENGTHS; colouring each bar differently adds no information and quietly
  // implies the categories differ in kind. Only a genuine multi-series
  // figure earns the categorical palette, and then positionally.
  const palette = data.length > 1 ? chartCategorical() : [chartAccent()];
  return data.map((trace, index) => {
    const t = trace as Record<string, unknown>;
    const next: Record<string, unknown> = { ...t };
    const color = palette[index % palette.length];
    if (t.type === "bar" || t.type === "histogram") {
      const marker = (t.marker as Record<string, unknown>) ?? {};
      next.marker = { ...marker, color: marker.color ?? color };
    } else if (t.type === "scatter") {
      const mode = String(t.mode ?? "");
      if (mode.includes("markers")) {
        const marker = (t.marker as Record<string, unknown>) ?? {};
        next.marker = { ...marker, color: marker.color ?? color };
      }
      if (mode.includes("lines") && !t.fill) {
        const line = (t.line as Record<string, unknown>) ?? {};
        next.line = { ...line, color: line.color ?? color };
      }
    }
    return next;
  });
}
