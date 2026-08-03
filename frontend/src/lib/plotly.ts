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
