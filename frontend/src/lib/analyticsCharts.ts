import { chartSequential } from "./plotly";

// The chart DERIVATION that used to live here has moved to the backend
// (app/analytics/chart_specs.py) and is gone from this file rather than
// kept alongside it.
//
// It was the only place those figures existed: the browser built them at
// render time, so nothing was persisted and the PowerPoint deck had no spec
// to render. Reimplementing them server-side and leaving this copy in place
// would have created two implementations that must agree - the exact
// failure this project has already hit twice (two resolve_run functions;
// --chart-1..6 turning out to be the status palette).
//
// The Analytics page now renders the persisted spec and resolves only its
// COLOUR ROLES against the live theme, via applyAnalyticsColours in
// lib/plotly.ts. backend/tests/test_analytics_chart_specs.py replays this
// file's captured output through the backend and requires the same figures
// back, point for point.

/** A/B/C is an ORDERED partition - A holds the most value, C the least - so
 *  the bands take three steps of the sequential ramp rather than three
 *  unrelated hues. Three arbitrary colours would say "these are different
 *  kinds of thing"; the ramp says "these are the same thing, ranked", which
 *  is what an ABC split actually is.
 *
 *  Still here, and deliberately: this colours a SWATCH in the band table,
 *  not a chart. It is a piece of table styling with no figure behind it, so
 *  there is nothing for the backend to persist and nothing that could
 *  disagree with the deck. The band letter is always printed next to it, so
 *  the band never depends on colour alone. */
export function paretoBandColor(band: string): string {
  const ramp = chartSequential();
  const step: Record<string, number> = { A: 4, B: 2, C: 1 };
  return ramp[step[band] ?? 1];
}
