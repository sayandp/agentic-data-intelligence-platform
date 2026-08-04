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

// DESIGN.md's One Accent Rule: the app's single accent (signal teal), never
// Plotly's own default trace palette (#636efa - a blue-violet close enough
// to the banned violet family to read as the same drift). Chart TYPE/data
// stays entirely backend-decided; this only fills in a color when a trace
// doesn't already specify one, so a chart drawn anywhere in the app matches
// the rest of the interface instead of Plotly's stock look.
export const CHART_ACCENT = "#0B6E6E";

export function withDesignColors(data: unknown[]): unknown[] {
  return data.map((trace) => {
    const t = trace as Record<string, unknown>;
    const next: Record<string, unknown> = { ...t };
    if (t.type === "bar" || t.type === "histogram") {
      const marker = (t.marker as Record<string, unknown>) ?? {};
      next.marker = { ...marker, color: marker.color ?? CHART_ACCENT };
    } else if (t.type === "scatter") {
      const mode = String(t.mode ?? "");
      if (mode.includes("markers")) {
        const marker = (t.marker as Record<string, unknown>) ?? {};
        next.marker = { ...marker, color: marker.color ?? CHART_ACCENT };
      }
      if (mode.includes("lines") && !t.fill) {
        const line = (t.line as Record<string, unknown>) ?? {};
        next.line = { ...line, color: line.color ?? CHART_ACCENT };
      }
    }
    return next;
  });
}
