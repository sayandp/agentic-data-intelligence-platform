"""THE chart spec for a business analysis. One derivation, two consumers.

Ported verbatim from frontend/src/lib/analyticsCharts.ts, which used to be
the only place these figures existed. That was the bug: the browser derived
them at render time, so nothing was persisted and the deck builder had
nothing to draw. The fix is not a second implementation next to the first -
this project has been bitten twice by two things that had to agree and
didn't (two resolve_run functions; --chart-1..6 being the status palette) -
so the frontend derivation is DELETED and both the Analytics page and the
PowerPoint deck read what this module produces.

WHY NO COLOURS ARE PERSISTED HERE
---------------------------------
A figure with baked colours would freeze the theme it was generated under.
The Analytics page must keep following Default/Dark/Aurora at runtime, and
the deck renders on white paper with its own print palette. So this module
persists the STRUCTURE plus a `colour_roles` annotation naming what each
trace MEANS - "accent", "neutral", "categorical", "sequential" - and each
consumer resolves those roles against its own palette:

    frontend/src/lib/plotly.ts     applyAnalyticsColours()  -> theme tokens
    backend/app/export/chart_images.py                      -> deck palette

The role assignment is the part that carries meaning, and it lives here,
once. That is what keeps the screen and the slide saying the same thing
about the same data.

The colour RULES themselves are unchanged from the frontend original:
  - ordered data (cohort periods) uses the sequential ramp
  - unordered categories (clusters) use the categorical palette
  - a single series (RFM segment shares, the Pareto curve) uses one accent
  - no role can name a status colour; there is no role that maps to one
"""

from __future__ import annotations

from typing import Any

#: Bumped whenever a change here would render an ALREADY-PERSISTED spec
#: differently. Readers re-derive anything older, which is what lets a fix
#: to this module reach runs that were analysed before it - otherwise the
#: fix silently applies only to runs ingested afterwards, which is the
#: least useful place for it.
#:
#: 2: the cohort heatmap's colour domain is fitted to observed retention
#:    instead of spanning 0-100 (period 0 is 1.0 by construction and was
#:    pushing every real value into the palest fifth of the ramp).
CHART_SPEC_VERSION = 2

#: What a trace means, resolved to a colour by whoever is drawing it.
ROLE_ACCENT = "accent"
ROLE_NEUTRAL = "neutral"
ROLE_CATEGORICAL = "categorical"

#: An ordered surface coloured by a continuous ramp rather than per trace.
#: `zero_transparent` keeps an unobserved cohort cell absent rather than
#: painting it as the ramp's first step, which would read as a real 0%.
SCALE_SEQUENTIAL_ZERO_TRANSPARENT = "sequential_zero_transparent"


def _pct_list(values: Any) -> list[float | None]:
    return [None if v is None else float(v) * 100 for v in (values or [])]


def _pareto_curve(payload: dict) -> dict:
    """Cumulative value share against cumulative entity share, with the
    diagonal drawn for reference. The gap between the two IS the
    concentration."""
    return {
        "kind": "pareto_curve",
        "title": "ABC / Pareto concentration",
        "colour_roles": [ROLE_ACCENT, ROLE_NEUTRAL],
        "figure_json": {
            "data": [
                {
                    "x": _pct_list(payload.get("cumulative_entity_share")),
                    "y": _pct_list(payload.get("cumulative_value_share")),
                    "type": "scatter",
                    "mode": "lines",
                    "name": "cumulative value",
                    "line": {"width": 2},
                },
                {
                    # Perfect equality. Without it a reader has no reference
                    # for whether the curve is bowed at all.
                    "x": [0, 100],
                    "y": [0, 100],
                    "type": "scatter",
                    "mode": "lines",
                    "name": "even contribution",
                    "line": {"width": 1, "dash": "dot"},
                    "hoverinfo": "skip",
                },
            ],
            "layout": {
                "xaxis": {"title": "cumulative share of entities (%)", "range": [0, 100]},
                "yaxis": {"title": "cumulative share of value (%)", "range": [0, 100]},
                "margin": {"t": 16, "r": 16, "b": 48, "l": 56},
                "showlegend": True,
            },
        },
    }


#: Headroom added either side of the observed retention range, as a share
#: of that range. Enough that the darkest and palest observed cells are not
#: pinned to the very ends of the ramp, small enough not to give the wasted
#: span back.
DOMAIN_PADDING = 0.05


def _fitted_retention_domain(matrix: list, periods: list) -> tuple[float, float] | None:
    """The colour domain, fitted to the retention that carries information.

    Period 0 is EXCLUDED. It is 1.0 for every cohort by construction - the
    definition of a cohort, not a finding about one - and letting it set the
    top of the scale is what pushed real retention into the bottom fifth of
    the ramp. Nearly every cell then rendered as the palest step and cohorts
    could not be told apart, worst of all in the Default theme.

    Returns None when there is nothing to fit to, in which case the caller
    leaves the scale absolute rather than inventing a range.
    """
    observed = [
        value * 100
        for row in matrix or []
        for index, value in enumerate(row or [])
        # Keyed on the PERIOD, not the column index, so this stays correct
        # if a matrix ever starts somewhere other than period 0.
        if value is not None and not (index < len(periods or []) and periods[index] == 0)
    ]
    if not observed:
        return None

    low, high = min(observed), max(observed)
    padding = max((high - low) * DOMAIN_PADDING, 0.5)
    return max(0.0, low - padding), min(100.0, high + padding)


def _cohort_heatmap(payload: dict) -> dict:
    """The cohort triangle. `null` cells are periods that have NOT HAPPENED
    yet for that cohort - Plotly renders them as gaps, which is exactly
    right: an unobserved period must not look like 0% retention.

    The colour domain is FITTED to the observed retention rather than the
    theoretical 0-100. The ramp itself is unchanged; only the range it is
    stretched over. Because that makes two runs' heatmaps not directly
    comparable by colour, the domain is stated on the colourbar - a scale a
    reader assumes is absolute, and is not, is worse than a hard-to-read one.
    """
    matrix = payload.get("retained_share") or []
    granularity = payload.get("granularity")
    periods = payload.get("periods_since_acquisition") or []

    domain = _fitted_retention_domain(matrix, periods)
    trace: dict = {
        "type": "heatmap",
        "z": [_pct_list(row) for row in matrix],
        "x": periods,
        "y": payload.get("cohort_labels") or [],
        "hoverongaps": False,
        # The OBJECT form, not a bare string: plotly.js v3 silently ignores
        # `colorbar.title` as a string, so this label never rendered in the
        # browser at all (Python plotly accepts both, which is why it showed
        # up in the deck and nowhere else).
        "colorbar": {"title": {"text": "% retained"}},
    }
    title = "Cohort retention"
    if domain is not None:
        low, high = domain
        trace["zmin"] = round(low, 2)
        trace["zmax"] = round(high, 2)
        # Period 0 still renders - it is simply above the top of the scale
        # now, and clamps to the darkest step.
        trace["colorbar"] = {"title": {"text": f"% retained<br>(scale {low:.0f}-{high:.0f}%)"}}
        title = f"Cohort retention (colour scaled {low:.0f}-{high:.0f}%, not 0-100)"

    return {
        "kind": "cohort_heatmap",
        "title": title,
        "colour_roles": [],
        "colorscale_role": SCALE_SEQUENTIAL_ZERO_TRANSPARENT,
        "figure_json": {
            "data": [trace],
            "layout": {
                "xaxis": {"title": f"periods since acquisition ({granularity})", "dtick": 1},
                "yaxis": {"title": "cohort", "autorange": "reversed"},
                "margin": {"t": 16, "r": 16, "b": 48, "l": 88},
            },
        },
    }


def _segment_bar(findings: list[dict]) -> dict:
    """Segment sizes as a horizontal bar - readable with long segment names
    in a way a treemap is not, and it keeps value share comparable across
    segments on a shared axis."""
    payloads = [f.get("payload") or {} for f in findings]
    return {
        "kind": "segment_bar",
        "title": "Share of value by segment",
        # ONE accent, not the categorical palette. Bar length already
        # carries the comparison; cycling hues across named segments would
        # colour "promising" and "lost" differently and imply a verdict the
        # analysis never made.
        "colour_roles": [ROLE_ACCENT],
        "figure_json": {
            "data": [
                {
                    "type": "bar",
                    "orientation": "h",
                    "x": [float(p.get("value_share") or 0) * 100 for p in payloads],
                    "y": [str(p.get("segment")) for p in payloads],
                    "hovertemplate": "%{y}: %{x:.1f}% of value<extra></extra>",
                }
            ],
            "layout": {
                "xaxis": {"title": "share of value (%)"},
                "yaxis": {"title": "", "automargin": True},
                "margin": {"t": 16, "r": 16, "b": 48, "l": 16},
            },
        },
    }


def _cluster_scatter(findings: list[dict]) -> dict:
    """Recency against monetary, sized by frequency. Three dimensions is
    what RFM has, and the two most legible go on the axes."""
    data = []
    for finding in findings:
        payload = finding.get("payload") or {}
        centre = payload.get("centre") or {}
        frequency = centre.get("frequency")
        data.append(
            {
                "type": "scatter",
                "mode": "markers+text",
                "name": str(payload.get("segment")),
                "x": [centre.get("recency_days")],
                "y": [centre.get("monetary")],
                "text": [str(payload.get("segment"))],
                "textposition": "top center",
                "marker": {"size": max(12, min(48, float(1 if frequency is None else frequency) * 8))},
            }
        )
    return {
        "kind": "cluster_scatter",
        "title": "Behavioural segments",
        # A cluster id is an UNORDERED category, so the categorical palette
        # is exactly right - one accent for every cluster made a
        # multi-cluster scatter unreadable.
        "colour_roles": [ROLE_CATEGORICAL] * len(data),
        "figure_json": {
            "data": data,
            "layout": {
                "xaxis": {"title": "mean recency (days)"},
                "yaxis": {"title": "mean value"},
                "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                "showlegend": False,
            },
        },
    }


def chart_for_result(analysis: str, findings: list[dict]) -> dict | None:
    """The one chart for one analysis, or None when there is none worth
    drawing.

    Selection is a SWITCH ON finding_type first, then on the analysis name -
    the same order and the same closed set the frontend used. Never a
    heuristic over the data, never an LLM. A market-basket rule table reads
    better as a table, so it deliberately has no chart; inventing a picture
    for it would be decoration.
    """
    by_type = {f.get("finding_type"): f for f in findings if f.get("finding_type")}

    if "concentration_curve" in by_type:
        spec = _pareto_curve(by_type["concentration_curve"].get("payload") or {})
    elif "retention_matrix" in by_type:
        spec = _cohort_heatmap(by_type["retention_matrix"].get("payload") or {})
    elif analysis == "behavioural_segmentation" and findings:
        spec = _cluster_scatter(findings)
    elif analysis == "rfm" and findings:
        spec = _segment_bar(findings)
    else:
        return None

    spec["analysis"] = analysis
    spec["chart_id"] = f"{analysis}-{spec['kind']}"
    spec["spec_version"] = CHART_SPEC_VERSION
    return spec


def charts_are_current(charts: list[dict] | None) -> bool:
    """Whether persisted specs were produced by the CURRENT generator.

    False for absent charts and for charts from an older version, so both
    take the same re-derivation path rather than one being handled and the
    other quietly served stale.
    """
    if not charts:
        return False
    return all(chart.get("spec_version") == CHART_SPEC_VERSION for chart in charts)


def charts_for_results(results: list[dict]) -> list[dict]:
    """Every analysis's chart, in the order the analyses ran.

    Pure over persisted findings, which is what lets a run analysed before
    charts were persisted get the same figures derived on demand rather than
    a blank space or a second code path.
    """
    charts = []
    for result in results:
        if not result.get("ran"):
            continue
        chart = chart_for_result(str(result.get("analysis")), list(result.get("findings") or []))
        if chart is not None:
            charts.append(chart)
    return charts
