// Shared by every page that renders a deterministic, backend-computed chart
// (ReportsPage.tsx's report charts, PredictPage.tsx's forecast chart) - one
// load implementation, never duplicated per page. Chart TYPE/data is
// always decided server-side (app/narrative/charts.py, app/modeling/
// automl.py) - this module only ever draws what it's given.
declare global {
  interface Window {
    Plotly?: { newPlot: (el: HTMLElement, data: unknown, layout: unknown, config?: unknown) => void };
  }
}

type PlotlyModule = { newPlot: (el: HTMLElement, data: unknown, layout: unknown, config?: unknown) => void };

// Plotly is BUNDLED, not fetched from a CDN. It used to be a <script> tag
// pointing at cdn.plot.ly with no .catch(), so on a machine with no
// internet - or behind a firewall, or at a conference - every chart on
// every page silently stayed blank. A demo is exactly where that happens.
//
// Imported dynamically so the ~3MB library is a separate chunk fetched on
// first chart rather than part of the initial bundle: the pages with no
// chart on them never pay for it.
let plotlyPromise: Promise<void> | null = null;

export function loadPlotly(): Promise<void> {
  if (window.Plotly) return Promise.resolve();
  // One in-flight load shared by every caller - several charts mounting at
  // once must not each import it.
  if (plotlyPromise) return plotlyPromise;
  plotlyPromise = import("plotly.js-dist-min")
    .then((module) => {
      const plotly = ((module as { default?: PlotlyModule }).default ?? module) as PlotlyModule;
      window.Plotly = plotly;
    })
    .catch((error) => {
      // Reset so a later mount can retry rather than being stuck on a
      // rejected promise forever.
      plotlyPromise = null;
      throw error;
    });
  return plotlyPromise;
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

/** `chartType` is the BACKEND's classification (app/narrative/models.py::
 *  ChartType), not the Plotly trace type - and the difference matters. A
 *  pre-binned histogram is emitted as a `bar` trace, so the trace type alone
 *  cannot tell "frequencies of four unordered countries" from "the shape of
 *  a continuous distribution". Colouring the second one per-bar would be
 *  nonsense; colouring the first one per-bar is the whole point. */
/** Resolve a persisted analytics chart's colour ROLES against the live
 *  theme (app/analytics/chart_specs.py writes the roles; the deck's
 *  chart_images.py resolves the same roles against its print palette).
 *
 *  The spec carries no colours on purpose: a persisted colour would freeze
 *  the theme it was generated under, and this page has to keep following
 *  Default/Dark/Aurora. The role assignment - which trace is the accent,
 *  which is the neutral reference, which cycle the categorical palette - is
 *  the part that carries meaning, and it is decided once, on the server. */
export function applyAnalyticsColours(chart: {
  figure_json: { data: unknown[]; layout: Record<string, unknown> };
  colour_roles?: string[];
  colorscale_role?: string;
}): { data: unknown[]; layout: Record<string, unknown> } {
  const roles = chart.colour_roles ?? [];
  const categorical = chartCategorical();
  let categoricalIndex = 0;

  const data = chart.figure_json.data.map((trace, index) => {
    const t = { ...(trace as Record<string, unknown>) };
    const role = roles[index];
    let colour: string | null = null;
    if (role === "categorical") {
      colour = categorical[categoricalIndex % categorical.length];
      categoricalIndex += 1;
    } else if (role === "accent") {
      colour = chartAccent();
    } else if (role === "neutral") {
      colour = chartNeutral();
    }

    if (colour) {
      const mode = String(t.mode ?? "");
      if (t.type === "bar" || t.type === "histogram" || mode.includes("markers")) {
        // A single BAR trace whose x is several unordered categories gets a
        // colour PER BAR, not one colour for the trace. The categories differ
        // in kind and the axis already names each, so colour is a second
        // channel rather than an invented ranking - the same rule
        // withDesignColors applies to report charts.
        const categories = Array.isArray(t.x) ? (t.x as unknown[]).length : 0;
        if (role === "categorical" && t.type === "bar" && categories > 1) {
          t.marker = {
            ...((t.marker as Record<string, unknown>) ?? {}),
            color: Array.from({ length: categories }, (_, i) => categorical[i % categorical.length]),
          };
        } else {
          t.marker = { ...((t.marker as Record<string, unknown>) ?? {}), color: colour };
        }
      }
      if (mode.includes("lines")) {
        t.line = { ...((t.line as Record<string, unknown>) ?? {}), color: colour };
      }
    }
    return t;
  });

  if (chart.colorscale_role === "sequential_zero_transparent" && data.length > 0) {
    // Zero stays transparent so an unobserved cohort cell reads as absent
    // rather than as a real 0%.
    data[0] = {
      ...(data[0] as Record<string, unknown>),
      colorscale: [[0, "rgba(0,0,0,0)"], ...chartColorscale().slice(1)],
    };
  }

  return { data, layout: themedLayout(chart.figure_json.layout) };
}

export function withDesignColors(data: unknown[], chartType?: string): unknown[] {
  // A ONE-TRACE BAR OF UNORDERED CATEGORIES gets one colour PER BAR. The
  // categories genuinely differ in kind - four countries are not a scale -
  // and the x-axis already names each, so colour adds a second channel
  // rather than inventing a meaning.
  //
  // This is the case that made every chart on the report page monochrome:
  // eight charts, eight single traces, so all eight landed on the accent
  // and the categorical palette was never reached.
  if (chartType === "bar" && data.length === 1) {
    const t = data[0] as Record<string, unknown>;
    const categories = Array.isArray(t.x) ? t.x.length : 0;
    if (categories > 1) {
      const palette = chartCategorical();
      const marker = (t.marker as Record<string, unknown>) ?? {};
      return [
        {
          ...t,
          marker: {
            ...marker,
            color: marker.color ?? Array.from({ length: categories }, (_, i) => palette[i % palette.length]),
          },
        },
      ];
    }
  }

  // THE SINGLE-SERIES RULE, unchanged for everything else. A histogram's
  // bins are ORDERED and continuous, a line is one series, a scatter is one
  // cloud - each carries its comparison in position, and rainbow bins would
  // imply the bins differ in kind when they are the same measure sliced up.
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
