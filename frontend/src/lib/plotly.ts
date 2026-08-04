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

export function chartAccent(): string {
  return token("--chart-1", "#0B6E6E");
}

/** The neutral used for already-known data (e.g. a forecast's actuals),
 *  kept distinct from the accent so the accent still marks the one thing
 *  that is genuinely new information. */
export function chartNeutral(): string {
  return token("--chart-2", "#4B5563");
}

/** Fill for an uncertainty band. */
export function chartBand(): string {
  return token("--chart-band", "rgba(11,110,110,0.15)");
}

/** The ordered categorical palette. Used positionally when a figure has
 *  several traces and the backend named no colors. */
export function chartPalette(): string[] {
  return [
    token("--chart-1", "#0B6E6E"),
    token("--chart-2", "#4B5563"),
    token("--chart-3", "#1E40AF"),
    token("--chart-4", "#9A6700"),
    token("--chart-5", "#15803D"),
    token("--chart-6", "#B91C1C"),
  ];
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
  const palette = chartPalette();
  return data.map((trace, index) => {
    const t = trace as Record<string, unknown>;
    const next: Record<string, unknown> = { ...t };
    // Positional so a multi-trace figure is distinguishable; a single-trace
    // figure always lands on the accent.
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
