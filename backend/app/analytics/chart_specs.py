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


def _cohort_heatmap(payload: dict) -> dict:
    """The cohort triangle. `null` cells are periods that have NOT HAPPENED
    yet for that cohort - Plotly renders them as gaps, which is exactly
    right: an unobserved period must not look like 0% retention."""
    matrix = payload.get("retained_share") or []
    granularity = payload.get("granularity")
    return {
        "kind": "cohort_heatmap",
        "title": "Cohort retention",
        "colour_roles": [],
        "colorscale_role": SCALE_SEQUENTIAL_ZERO_TRANSPARENT,
        "figure_json": {
            "data": [
                {
                    "type": "heatmap",
                    "z": [_pct_list(row) for row in matrix],
                    "x": payload.get("periods_since_acquisition") or [],
                    "y": payload.get("cohort_labels") or [],
                    "hoverongaps": False,
                    "colorbar": {"title": "% retained"},
                }
            ],
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
    return spec


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
