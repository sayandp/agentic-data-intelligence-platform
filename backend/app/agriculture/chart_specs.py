"""Agriculture chart SPECS, on the same path every other chart uses.

Produces specs in the SHAPE app/analytics/chart_specs.py defines - structure
plus `colour_roles`, never baked colours - and reuses its constants so there is
one vocabulary of colour roles, not three. The existing consumers then render
them with no agriculture-specific code:

    frontend/src/lib/plotly.ts     applyAnalyticsColours()  -> theme tokens
    backend/app/export/chart_images.py                      -> deck palette

Chart type is decided by DATA SHAPE, deterministically:

    yield over seasons        -> line      (ordered; single series, accent)
    district comparison       -> bar       (unordered categories)
    crop share                -> bar       (unordered categories)
    yield distribution        -> histogram (ordered; single accent)

No role here can name a status colour, because the role vocabulary has no such
member.
"""

from __future__ import annotations

import pandas as pd

from app.agriculture.config import AgricultureConfig
from app.agriculture.preprocess import DERIVED_YIELD
from app.analytics.chart_specs import CHART_SPEC_VERSION, ROLE_ACCENT, ROLE_CATEGORICAL
from app.analytics.roles import ColumnRole

#: Districts shown on a comparison bar. Beyond this the bars are too thin to
#: read and the chart stops being a comparison.
MAX_DISTRICTS_CHARTED = 15

#: Bins for the yield histogram. Matches the exploration histogram's own bin
#: count so two histograms in one report do not disagree about grain.
YIELD_HISTOGRAM_BINS = 30


def _spec(kind: str, title: str, data: list[dict], layout: dict, colour_roles: list[str]) -> dict:
    return {
        "chart_id": f"agriculture-{kind}",
        "analysis": "agriculture",
        "kind": kind,
        "title": title,
        "colour_roles": colour_roles,
        "spec_version": CHART_SPEC_VERSION,
        "figure_json": {"data": data, "layout": layout},
    }


def agriculture_charts(df: pd.DataFrame, roles: dict, config: AgricultureConfig) -> list[dict]:
    """Every chart this run's columns support, in report order.

    A chart whose inputs are absent is simply not produced - the missing role
    is already reported by the applicability output, so an empty picture would
    add nothing.
    """
    charts: list[dict] = []
    district = roles.get(ColumnRole.DISTRICT)
    crop = roles.get(ColumnRole.CROP)
    year = roles.get(ColumnRole.CROP_YEAR)
    production = roles.get(ColumnRole.PRODUCTION_QUANTITY)

    # -- yield over seasons: ORDERED, single series, accent --
    if year and DERIVED_YIELD in df.columns and year in df.columns:
        series = (
            pd.to_numeric(df[DERIVED_YIELD], errors="coerce")
            .groupby(df[year])
            .mean()
            .dropna()
            .sort_index()
        )
        if not series.empty:
            charts.append(
                _spec(
                    "yield_over_seasons",
                    "Average yield by crop year",
                    [
                        {
                            "x": [str(i) for i in series.index],
                            "y": [float(v) for v in series.values],
                            "type": "scatter",
                            "mode": "lines+markers",
                            "name": "yield",
                            "line": {"width": 2},
                        }
                    ],
                    {
                        "xaxis": {"title": "crop year"},
                        "yaxis": {"title": "yield"},
                        "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_ACCENT],
                )
            )

    # -- district comparison: UNORDERED categories --
    if district and DERIVED_YIELD in df.columns and district in df.columns:
        by_district = (
            pd.to_numeric(df[DERIVED_YIELD], errors="coerce")
            .groupby(df[district])
            .mean()
            .dropna()
            .sort_values(ascending=False)
            .head(MAX_DISTRICTS_CHARTED)
        )
        if not by_district.empty:
            charts.append(
                _spec(
                    "yield_by_district",
                    "Average yield by district",
                    [
                        {
                            "x": [str(i) for i in by_district.index],
                            "y": [float(v) for v in by_district.values],
                            "type": "bar",
                            "name": "yield",
                        }
                    ],
                    {
                        "xaxis": {"title": "district"},
                        "yaxis": {"title": "yield"},
                        "margin": {"t": 16, "r": 16, "b": 96, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_CATEGORICAL],
                )
            )

    # -- crop share: UNORDERED categories --
    if crop and production and {crop, production} <= set(df.columns):
        by_crop = (
            pd.to_numeric(df[production], errors="coerce")
            .groupby(df[crop])
            .sum()
            .dropna()
            .sort_values(ascending=False)
            .head(config.top_crops)
        )
        if not by_crop.empty:
            charts.append(
                _spec(
                    "crop_share",
                    "Production by crop",
                    [
                        {
                            "x": [str(i) for i in by_crop.index],
                            "y": [float(v) for v in by_crop.values],
                            "type": "bar",
                            "name": "production",
                        }
                    ],
                    {
                        "xaxis": {"title": "crop"},
                        "yaxis": {"title": "production"},
                        "margin": {"t": 16, "r": 16, "b": 96, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_CATEGORICAL],
                )
            )

    # -- yield distribution: ORDERED, single accent --
    if DERIVED_YIELD in df.columns:
        values = pd.to_numeric(df[DERIVED_YIELD], errors="coerce").dropna()
        if len(values) >= 2:
            charts.append(
                _spec(
                    "yield_distribution",
                    "Distribution of yield",
                    [
                        {
                            "x": [float(v) for v in values.values],
                            "type": "histogram",
                            "nbinsx": YIELD_HISTOGRAM_BINS,
                            "name": "yield",
                        }
                    ],
                    {
                        "xaxis": {"title": "yield"},
                        "yaxis": {"title": "count"},
                        "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_ACCENT],
                )
            )

    return charts
