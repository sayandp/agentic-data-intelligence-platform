// plotly.js-dist-min ships no types. Declared here rather than pulling in
// @types/plotly.js, which types the entire library - this app only ever
// calls newPlot, because chart type and data are decided server-side
// (app/narrative/charts.py, app/analytics/chart_specs.py) and lib/plotly.ts
// only draws what it is given. A narrow declaration keeps that true:
// reaching for anything else here would not compile.
declare module "plotly.js-dist-min" {
  const plotly: {
    newPlot: (el: HTMLElement, data: unknown, layout: unknown, config?: unknown) => void;
  };
  export default plotly;
}
