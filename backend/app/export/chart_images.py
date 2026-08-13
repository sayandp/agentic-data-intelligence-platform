"""Server-side PNGs of the SAME figures the dashboard draws.

The figure JSON is not rebuilt here. It is read verbatim out of
Report.chart_refs - the exact object the browser is handed - and rendered
by kaleido. That is the whole point: a slide and a screen showing different
things for the same run would make both untrustworthy, and the only way to
guarantee they agree is for there to be one spec, produced once, by
app/narrative/charts.py.

NO LLM. Nothing here decides what a chart shows; it decides pixels.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

#: 16:9 at a size that stays legible when a deck is projected.
CHART_WIDTH_PX = 1200
CHART_HEIGHT_PX = 620
CHART_SCALE = 2  # retina-ish, so text is not soft on a large screen

#: Print-facing colours. The deck has no `data-theme`, so it cannot read the
#: app's CSS custom properties - these are the DEFAULT theme's resolved
#: values, hardcoded here and nowhere else. The three-palette separation
#: still holds: the categorical hues below are the same measured,
#: dichromacy-checked set, and no status colour appears among them.
DECK_ACCENT = "#0B6E6E"
DECK_CATEGORICAL = ["#0B6E6E", "#404040", "#8C6D1F", "#CC79A7", "#56B4E9", "#E69F00"]
DECK_GRID = "#E2E5EA"
DECK_AXIS = "#6B7280"
DECK_INK = "#12161C"


def _apply_deck_colours(figure: dict, chart_type: str | None = None) -> dict:
    """Same rule the UI applies (frontend/src/lib/plotly.ts), so a slide and
    a screen colour the same figure the same way.

    `chart_type` is the BACKEND's classification, not the Plotly trace type:
    a pre-binned histogram is emitted as a `bar` trace, so the trace type
    alone cannot tell unordered category frequencies from the shape of a
    continuous distribution.
    """
    data = list(figure.get("data") or [])

    # One trace of UNORDERED categories: a colour per bar. The categories
    # differ in kind and the axis already names each, so colour is a second
    # channel rather than an invented meaning.
    if chart_type == "bar" and len(data) == 1:
        trace = dict(data[0])
        x_values = trace.get("x")
        if isinstance(x_values, list) and len(x_values) > 1:
            marker = dict(trace.get("marker") or {})
            marker.setdefault("color", [DECK_CATEGORICAL[i % len(DECK_CATEGORICAL)] for i in range(len(x_values))])
            trace["marker"] = marker
            data = [trace]
            figure = {**figure, "data": data}

    palette = DECK_CATEGORICAL if len(data) > 1 else [DECK_ACCENT]

    coloured = []
    for index, trace in enumerate(data):
        trace = dict(trace)
        colour = palette[index % len(palette)]
        kind = trace.get("type")
        if kind in ("bar", "histogram"):
            marker = dict(trace.get("marker") or {})
            marker.setdefault("color", colour)
            trace["marker"] = marker
        elif kind == "scatter":
            mode = str(trace.get("mode") or "")
            if "markers" in mode:
                marker = dict(trace.get("marker") or {})
                marker.setdefault("color", colour)
                trace["marker"] = marker
            if "lines" in mode and not trace.get("fill"):
                line = dict(trace.get("line") or {})
                line.setdefault("color", colour)
                trace["line"] = line
        coloured.append(trace)

    layout = dict(figure.get("layout") or {})
    axis = {
        "gridcolor": DECK_GRID,
        "zerolinecolor": DECK_GRID,
        "linecolor": DECK_GRID,
        "tickcolor": DECK_AXIS,
        "tickfont": {"color": DECK_AXIS},
    }
    layout.update(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"color": DECK_INK, "size": 15},
        margin={"t": 24, "r": 24, "b": 56, "l": 72},
        # The slide carries the caption, so a title inside the image would
        # be the same string twice in two different typefaces.
        title=None,
    )
    layout["xaxis"] = {**axis, **(layout.get("xaxis") or {})}
    layout["yaxis"] = {**axis, **(layout.get("yaxis") or {})}
    return {"data": coloured, "layout": layout}


#: The default theme's neutral and sequential ramp, resolved for print.
#: Analytics figures persist COLOUR ROLES rather than colours (see
#: app/analytics/chart_specs.py) precisely so the deck can use these while
#: the screen uses its live theme tokens - one spec, two palettes.
DECK_NEUTRAL = "#4B5563"
DECK_SEQUENTIAL = ["#CFE7E7", "#A9D4D4", "#4FA3A3", "#12807E", "#0A3D3D"]

_ROLE_COLOURS = {"accent": DECK_ACCENT, "neutral": DECK_NEUTRAL}


def _paint(trace: dict, colour: str) -> dict:
    """Colour whichever channel this trace actually draws with."""
    trace = dict(trace)
    mode = str(trace.get("mode") or "")
    kind = trace.get("type")

    if kind in ("bar", "histogram") or "markers" in mode:
        marker = dict(trace.get("marker") or {})
        marker.setdefault("color", colour)
        trace["marker"] = marker
    if "lines" in mode and not trace.get("fill"):
        line = dict(trace.get("line") or {})
        line.setdefault("color", colour)
        trace["line"] = line
    return trace


def _apply_role_colours(chart: dict) -> dict:
    """Resolve an analytics chart's colour ROLES against the deck palette.

    The roles are the frontend's rules, carried in the spec rather than
    re-decided here: ordered surfaces get the sequential ramp, unordered
    categories get the categorical palette, a single series gets one accent.
    No role maps to a status colour, so a slide cannot assert a verdict in
    colour any more than the screen can.
    """
    figure = chart.get("figure_json") or {}
    data = list(figure.get("data") or [])
    roles = list(chart.get("colour_roles") or [])

    coloured = []
    categorical_index = 0
    for index, trace in enumerate(data):
        role = roles[index] if index < len(roles) else None
        if role == "categorical":
            colour = DECK_CATEGORICAL[categorical_index % len(DECK_CATEGORICAL)]
            categorical_index += 1
        elif role in _ROLE_COLOURS:
            colour = _ROLE_COLOURS[role]
        else:
            coloured.append(dict(trace))
            continue
        coloured.append(_paint(trace, colour))

    if chart.get("colorscale_role") == "sequential_zero_transparent" and coloured:
        # Zero stays transparent so an unobserved cohort cell reads as
        # absent rather than as a real 0%.
        steps = [(i / (len(DECK_SEQUENTIAL) - 1), c) for i, c in enumerate(DECK_SEQUENTIAL)]
        scale = [[0.0, "rgba(0,0,0,0)"]] + [[position, colour] for position, colour in steps[1:]]
        first = dict(coloured[0])
        first.setdefault("colorscale", scale)
        coloured[0] = first

    layout = dict(figure.get("layout") or {})
    return {"data": coloured, "layout": layout}


def render_analytics_chart_png(chart: dict) -> bytes | None:
    """A persisted analytics chart spec as a PNG, in the deck's palette."""
    figure = _apply_role_colours(chart)
    resolved = _apply_deck_colours(figure)
    try:
        return pio.to_image(
            go.Figure(resolved), format="png", width=CHART_WIDTH_PX, height=CHART_HEIGHT_PX, scale=CHART_SCALE
        )
    except Exception:  # noqa: BLE001 - a chart is additive; the deck is not
        return None


def render_chart_png(figure_json: dict, chart_type: str | None = None) -> bytes | None:
    """PNG bytes, or None when this figure cannot be drawn.

    Returns None rather than raising: one unrenderable chart must not cost
    the whole deck. The caller states on the slide that the image was
    unavailable, which is more useful than a deck that failed to build.
    """
    try:
        figure = go.Figure(_apply_deck_colours(figure_json, chart_type))
        return pio.to_image(
            figure, format="png", width=CHART_WIDTH_PX, height=CHART_HEIGHT_PX, scale=CHART_SCALE
        )
    except Exception:  # noqa: BLE001 - a chart is additive; the deck is not
        return None
