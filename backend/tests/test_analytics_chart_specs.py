"""The port from analyticsCharts.ts must be FAITHFUL, not approximate.

frontend/src/lib/analyticsCharts.ts derived these figures in the browser, so
nothing was persisted and the deck had nothing to render. Moving the
derivation to the backend is only safe if the figures do not change - a
refactor, not a redesign.

tests/fixtures/analytics_charts_golden.json was captured from the LIVE
frontend for run 13 immediately before that code was deleted: the findings
it was given, and the Plotly figures it actually drew, read straight off the
rendered nodes. These tests replay that input through the backend and
require the same figures back.

Colours are compared separately and deliberately. The backend persists
colour ROLES, not colours, so that the screen can stay theme-reactive and
the deck can print. What must match is the role ASSIGNMENT - which trace is
the accent, which is neutral, which cycle the categorical palette - because
that is the part that carries meaning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analytics.chart_specs import chart_for_result, charts_for_results

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "analytics_charts_golden.json").read_text())

#: The frontend applies themedLayout() on top of each spec, which merges in
#: theme colours and background. Those are a rendering concern the backend
#: deliberately does not persist, so they are not part of the comparison.
THEME_LAYOUT_KEYS = {
    "paper_bgcolor",
    "plot_bgcolor",
    "font",
    "colorway",
    "hoverlabel",
    "legend",
    "template",
    "modebar",
    "autosize",
    "height",
    "width",
    "computed",
    "_subplots",
    "dragmode",
    "hovermode",
}

#: Keys Plotly itself adds to a trace or axis once it has rendered.
PLOTLY_RUNTIME_KEYS = {"uid", "_input", "_expandedInput", "autorange", "type", "range", "domain"}


def _frontend(title_fragment: str) -> dict:
    for chart in GOLDEN["frontend_charts"]:
        if title_fragment.lower() in chart["title"].lower():
            return chart
    raise AssertionError(f"no golden chart titled like {title_fragment!r}")


def _result(analysis: str) -> dict:
    for result in GOLDEN["results"]:
        if result["analysis"] == analysis:
            return result
    raise AssertionError(f"no golden result for {analysis!r}")


def _backend(analysis: str) -> dict:
    result = _result(analysis)
    spec = chart_for_result(result["analysis"], result["findings"])
    assert spec is not None, f"the backend derives no chart for {analysis}"
    return spec


def _title_text(title) -> str | None:
    """The axis title as a plain string.

    Once rendered, Plotly's axis title is a String object carrying extra
    properties, so JSON.stringify in the capture spread it into character
    keys - {"0": "m", "1": "e", ..., "font": {...}}. The characters are all
    there, in order; this puts them back rather than weakening the
    comparison to skip titles.
    """
    if title is None or isinstance(title, str):
        return title
    if isinstance(title, dict):
        if isinstance(title.get("text"), str):
            return title["text"]
        indexed = sorted(((int(k), v) for k, v in title.items() if k.isdigit()), key=lambda kv: kv[0])
        if indexed:
            return "".join(v for _, v in indexed)
    return None


def _clean_layout(layout: dict) -> dict:
    """Layout minus what the theme layer and Plotly's runtime add."""
    out = {}
    for key, value in layout.items():
        if key in THEME_LAYOUT_KEYS or key.startswith("_"):
            continue
        if isinstance(value, dict) and key.startswith(("xaxis", "yaxis")):
            value = {k: v for k, v in value.items() if k not in PLOTLY_RUNTIME_KEYS and not k.startswith("_")}
            # An absent title and an empty one are the same thing; the capture
            # cannot distinguish them, so both normalise to "".
            value = {**value, "title": _title_text(value.get("title")) or ""} if "title" in value else value
        out[key] = value
    return out


def _numbers_equal(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == pytest.approx(b, rel=1e-9, abs=1e-9)
    return a == b


def _series_equal(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_series_equal(x, y) for x, y in zip(a, b))
    return _numbers_equal(a, b)


# ---- the data is identical, point for point ----


@pytest.mark.parametrize(
    ("analysis", "title", "series_keys"),
    [
        ("abc_pareto", "Pareto", ("x", "y")),
        ("rfm", "RFM", ("x", "y")),
        ("cohort_retention", "Cohort", ("z", "x", "y")),
        ("behavioural_segmentation", "Behavioural", ("x", "y", "text")),
    ],
)
def test_the_backend_reproduces_the_frontends_data_exactly(analysis, title, series_keys):
    backend = _backend(analysis)["figure_json"]["data"]
    frontend = _frontend(title)["data"]

    assert len(backend) == len(frontend), f"{analysis}: trace count changed in the port"
    for index, (mine, theirs) in enumerate(zip(backend, frontend)):
        for key in series_keys:
            if key not in theirs:
                continue
            assert _series_equal(mine.get(key), theirs[key]), f"{analysis} trace {index}: `{key}` differs from the frontend"


@pytest.mark.parametrize(
    ("analysis", "title"),
    [
        ("abc_pareto", "Pareto"),
        ("rfm", "RFM"),
        ("cohort_retention", "Cohort"),
        ("behavioural_segmentation", "Behavioural"),
    ],
)
def test_the_backend_reproduces_the_frontends_trace_shape(analysis, title):
    """Type, mode, name, orientation, hovertemplate - everything that decides
    what the reader sees other than colour."""
    backend = _backend(analysis)["figure_json"]["data"]
    frontend = _frontend(title)["data"]

    for index, (mine, theirs) in enumerate(zip(backend, frontend)):
        for key in ("type", "mode", "name", "orientation", "textposition", "hovertemplate", "hoverinfo", "hoverongaps"):
            if key in theirs:
                assert mine.get(key) == theirs[key], f"{analysis} trace {index}: `{key}` differs"


@pytest.mark.parametrize(
    ("analysis", "title"),
    [
        ("abc_pareto", "Pareto"),
        ("rfm", "RFM"),
        ("cohort_retention", "Cohort"),
        ("behavioural_segmentation", "Behavioural"),
    ],
)
def test_the_backend_reproduces_the_frontends_axes_and_margins(analysis, title):
    backend = _clean_layout(_backend(analysis)["figure_json"]["layout"])
    frontend = _clean_layout(_frontend(title)["layout"])

    for key in ("margin", "showlegend"):
        if key in frontend:
            assert backend.get(key) == frontend[key], f"{analysis}: layout `{key}` differs"
    for axis in ("xaxis", "yaxis"):
        if axis not in frontend:
            continue
        for key in ("title", "dtick", "automargin", "autorange", "range"):
            if key in frontend[axis]:
                assert backend.get(axis, {}).get(key) == frontend[axis][key], f"{analysis}: {axis}.{key} differs"


# ---- the colour RULES survived the port ----


def test_the_pareto_curve_keeps_one_accent_against_a_neutral_reference():
    assert _backend("abc_pareto")["colour_roles"] == ["accent", "neutral"]


def test_segment_shares_stay_a_single_accent_not_a_hue_per_segment():
    """Cycling hues across named segments would colour "promising" and
    "lost" differently and imply a verdict the analysis never made."""
    spec = _backend("rfm")
    assert spec["colour_roles"] == ["accent"]
    assert len(spec["figure_json"]["data"]) == 1


def test_clusters_are_unordered_so_they_take_the_categorical_palette():
    spec = _backend("behavioural_segmentation")
    assert set(spec["colour_roles"]) == {"categorical"}
    assert len(spec["colour_roles"]) == len(spec["figure_json"]["data"])


def test_cohort_periods_are_ordered_so_they_take_the_sequential_ramp():
    spec = _backend("cohort_retention")
    assert spec["colorscale_role"] == "sequential_zero_transparent"
    assert not spec["colour_roles"], "an ordered surface is coloured by its scale, not per trace"


def test_no_colour_is_baked_into_a_persisted_spec():
    """A persisted colour would freeze the theme it was generated under and
    make the Analytics page stop following Default/Dark/Aurora."""
    blob = json.dumps(charts_for_results(GOLDEN["results"]))

    for figure_key in ('"color"', '"colorscale"', '"colorway"'):
        assert figure_key not in blob, f"{figure_key} was persisted into a chart spec"


def test_an_unobserved_cohort_cell_stays_null_rather_than_becoming_zero():
    """A period that has not happened yet must not read as 0% retention."""
    z = _backend("cohort_retention")["figure_json"]["data"][0]["z"]

    assert any(cell is None for row in z for cell in row), "the cohort triangle lost its gaps"


# ---- selection is a closed switch, not a heuristic ----


def test_every_analysis_that_had_a_chart_still_has_one():
    charts = {c["analysis"] for c in charts_for_results(GOLDEN["results"])}

    assert charts == {"abc_pareto", "rfm", "cohort_retention", "behavioural_segmentation"}


def test_a_rule_table_deliberately_gets_no_chart():
    """market_basket reads better as a table; inventing a picture for it
    would be decoration."""
    assert chart_for_result("market_basket", [{"finding_type": "association_rule", "payload": {}}]) is None


def test_an_analysis_with_no_findings_gets_no_chart():
    assert chart_for_result("rfm", []) is None


def test_a_result_that_did_not_run_is_skipped():
    charts = charts_for_results([{"analysis": "rfm", "ran": False, "findings": [{"finding_type": "segment_profile"}]}])

    assert charts == []


# ---------------------------------------------------------------------------
# RECORDED DIVERGENCE FROM THE GOLDEN FIXTURE
#
# The fixture captures what frontend/src/lib/analyticsCharts.ts drew before
# the port. The cohort heatmap now DELIBERATELY differs from it, and the
# fixture is deliberately NOT regenerated - it is the record of what the
# frontend did, and rewriting it would erase the only evidence that the port
# itself was faithful.
#
# What differs, and why:
#   zmin/zmax   the frontend let the scale span 0-100. Period 0 is 1.0 for
#               every cohort by construction, so it pinned the top of the
#               scale and pushed real retention (2.6-49.6% on this run) into
#               the palest fifth of the ramp. Unreadable in the Default
#               theme, which is the demo theme.
#   colorbar    now states the fitted domain, because a scale a reader
#               assumes is absolute and is not is worse than a hard-to-read
#               one - two runs' heatmaps are no longer comparable by colour.
#               Also switched to Plotly's OBJECT form: plotly.js v3 silently
#               ignores a bare string, so this label had never rendered in
#               the browser at all.
#   title       carries the domain too, for the deck slide caption.
#
# Everything else about this chart - z, x, y, type, hoverongaps, the ramp
# itself, the transparent stop for unobserved cells, axes and margins - is
# still asserted against the fixture by the tests above.
# ---------------------------------------------------------------------------


def test_the_cohort_domain_excludes_period_zero():
    """Period 0 is 1.0 for every cohort by definition. Letting it set the
    top of the scale is the whole defect."""
    from app.analytics.chart_specs import _fitted_retention_domain

    matrix = [[1.0, 0.25, 0.20], [1.0, 0.18, None]]
    low, high = _fitted_retention_domain(matrix, [0, 1, 2])

    # Observed span here is 18-25, so 5% of it is 0.35 and the 0.5 floor
    # applies instead - the floor exists so a narrow range still gets
    # visible headroom.
    assert high < 100, "period 0 is still stretching the scale to the top"
    assert high == pytest.approx(25.5, abs=0.01)
    assert low == pytest.approx(17.5, abs=0.01)


def test_the_cohort_domain_is_keyed_on_the_period_not_the_column_index():
    """So a matrix that ever starts somewhere other than period 0 keeps
    working."""
    from app.analytics.chart_specs import _fitted_retention_domain

    # Period 0 sits in the SECOND column here.
    low, high = _fitted_retention_domain([[0.30, 1.0, 0.20]], [1, 0, 2])

    assert high < 100
    assert high == pytest.approx(30.5, abs=0.01)


def test_a_matrix_with_nothing_beyond_period_zero_keeps_an_absolute_scale():
    """Nothing to fit to, so no range is invented."""
    from app.analytics.chart_specs import _fitted_retention_domain

    assert _fitted_retention_domain([[1.0], [1.0]], [0]) is None


def test_a_run_with_no_retention_beyond_period_zero_still_produces_a_chart():
    spec = chart_for_result(
        "cohort_retention",
        [{
            "finding_type": "retention_matrix",
            "payload": {
                "finding_type": "retention_matrix",
                "granularity": "month",
                "cohort_labels": ["2010-01"],
                "periods_since_acquisition": [0],
                "retained_share": [[1.0]],
            },
        }],
    )
    trace = spec["figure_json"]["data"][0]

    assert "zmin" not in trace and "zmax" not in trace
    assert spec["title"] == "Cohort retention"


def test_the_fitted_domain_is_stated_where_a_reader_will_see_it():
    """Otherwise two runs' heatmaps are not comparable and nothing says so."""
    spec = _backend("cohort_retention")
    trace = spec["figure_json"]["data"][0]

    assert "scale" in trace["colorbar"]["title"]["text"]
    assert "not 0-100" in spec["title"]


def test_the_colorbar_title_uses_the_object_form_plotly_js_actually_renders():
    """A bare string is silently dropped by plotly.js v3 - the label never
    appeared in the browser, only in the kaleido-rendered deck."""
    trace = _backend("cohort_retention")["figure_json"]["data"][0]

    assert isinstance(trace["colorbar"]["title"], dict)
    assert "text" in trace["colorbar"]["title"]


def test_period_zero_still_renders_it_just_stops_defining_the_scale():
    trace = _backend("cohort_retention")["figure_json"]["data"][0]

    assert trace["z"][0][0] == pytest.approx(100.0), "period 0 was dropped from the data"
    assert trace["zmax"] < 100, "period 0 is still setting the top of the scale"


def test_a_stale_persisted_spec_is_re_derived_rather_than_served():
    """A fix here has to reach runs analysed BEFORE it, or it applies only
    where it is least needed."""
    from app.analytics.chart_specs import CHART_SPEC_VERSION, charts_are_current

    assert charts_are_current(None) is False
    assert charts_are_current([]) is False
    assert charts_are_current([{"spec_version": CHART_SPEC_VERSION - 1}]) is False
    assert charts_are_current([{"chart_id": "no-version-at-all"}]) is False
    assert charts_are_current([{"spec_version": CHART_SPEC_VERSION}]) is True
