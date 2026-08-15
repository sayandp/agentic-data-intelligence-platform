"""Marketing chart SPECS, on the same path the analytics charts use.

This module produces specs in the SHAPE app/analytics/chart_specs.py
defines - structure plus `colour_roles`, never baked colours - and reuses
its constants so there is one vocabulary of colour roles, not two. The
existing consumers then render them with no marketing-specific code:

    frontend/src/lib/plotly.ts     applyAnalyticsColours()  -> theme tokens
    backend/app/export/chart_images.py                      -> deck palette

Chart type is decided by DATA SHAPE, deterministically, never by an LLM and
never by a heuristic over the values:

    a measure over time            -> line   (ordered; single series, accent)
    a measure compared across      -> bar    (unordered categories)
      ad sets
    the distribution of a rate     -> histogram (ordered; single accent)

Colour follows the rules already in force: ordered data uses the sequential
ramp, unordered categories the categorical palette, a single continuous
series the accent. No role here can name a status colour, because the role
vocabulary has no such member.
"""

from __future__ import annotations

import pandas as pd

from app.analytics.chart_specs import CHART_SPEC_VERSION, ROLE_ACCENT, ROLE_CATEGORICAL
from app.analytics.roles import ColumnRole
from app.marketing.config import MarketingConfig

#: Ad sets shown on the comparison bar. Beyond this the bars are too thin to
#: read and the chart stops being a comparison.
MAX_ADSETS_CHARTED = 12

#: Bins for the CTR distribution. Matches the exploration histogram's own
#: bin count so two histograms in one report do not disagree about grain.
CTR_HISTOGRAM_BINS = 30


def _spec(kind: str, title: str, data: list[dict], layout: dict, colour_roles: list[str]) -> dict:
    return {
        "chart_id": f"marketing-{kind}",
        "analysis": "marketing",
        "kind": kind,
        "title": title,
        "colour_roles": colour_roles,
        "spec_version": CHART_SPEC_VERSION,
        "figure_json": {"data": data, "layout": layout},
    }


def _daily(df: pd.DataFrame, date_col: str, column: str, how: str = "sum") -> tuple[list, list]:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    grouped = df.assign(_d=dates.dt.date).groupby("_d")[column]
    series = grouped.sum() if how == "sum" else grouped.mean()
    series = series.dropna()
    return [str(d) for d in series.index], [float(v) for v in series.values]


def marketing_charts(df: pd.DataFrame, roles: dict, config: MarketingConfig) -> list[dict]:
    """Every chart this run's columns support, in report order.

    A chart whose inputs are absent is simply not produced - the missing
    role is already reported by the applicability output, so an empty
    picture would add nothing.
    """
    charts: list[dict] = []
    date_col = roles.get(ColumnRole.EVENT_DATE)
    spend_col = roles.get(ColumnRole.SPEND)
    campaign_col = roles.get(ColumnRole.CAMPAIGN_ID)

    # -- spend over time: ORDERED, single series, accent --
    if date_col and spend_col and spend_col in df.columns:
        x, y = _daily(df, date_col, spend_col)
        if x:
            charts.append(
                _spec(
                    "spend_over_time",
                    "Spend over time",
                    [{"x": x, "y": y, "type": "scatter", "mode": "lines", "name": "spend", "line": {"width": 2}}],
                    {
                        "xaxis": {"title": "date"},
                        "yaxis": {"title": "spend"},
                        "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_ACCENT],
                )
            )

    # -- CPA over time: ORDERED, single series, accent --
    if date_col and "cpa" in df.columns and df["cpa"].notna().any():
        x, y = _daily(df, date_col, "cpa", how="mean")
        if x:
            charts.append(
                _spec(
                    "cpa_over_time",
                    "Cost per acquisition over time",
                    [{"x": x, "y": y, "type": "scatter", "mode": "lines", "name": "cpa", "line": {"width": 2}}],
                    {
                        "xaxis": {"title": "date"},
                        # Days with no conversions have no CPA and are absent
                        # from this line rather than drawn as zero.
                        "yaxis": {"title": "cost per acquisition"},
                        "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                        "showlegend": False,
                    },
                    [ROLE_ACCENT],
                )
            )

    # -- ad-set comparison: UNORDERED categories, categorical palette --
    if campaign_col and campaign_col in df.columns and spend_col and spend_col in df.columns:
        totals = df.groupby(campaign_col, dropna=False)[spend_col].sum().sort_values(ascending=False)
        totals = totals.head(MAX_ADSETS_CHARTED)
        if len(totals) > 1:
            labels = [str(i) for i in totals.index]
            charts.append(
                _spec(
                    "adset_spend",
                    "Spend by ad set",
                    [
                        {
                            "type": "bar",
                            "x": labels,
                            "y": [float(v) for v in totals.values],
                            "hovertemplate": "%{x}: %{y:,.2f}<extra></extra>",
                        }
                    ],
                    {
                        "xaxis": {"title": "", "automargin": True},
                        "yaxis": {"title": "spend"},
                        "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                        "showlegend": False,
                    },
                    # An ad set is an unordered category and the axis names
                    # each one, so colour is a second channel rather than an
                    # invented ranking.
                    [ROLE_CATEGORICAL],
                )
            )

    # -- CTR distribution: ORDERED measure, single accent --
    if "ctr" in df.columns and df["ctr"].notna().sum() > 1:
        values = df["ctr"].dropna()
        charts.append(
            _spec(
                "ctr_distribution",
                "Click-through rate distribution",
                [
                    {
                        "type": "histogram",
                        "x": [float(v) for v in values],
                        "nbinsx": CTR_HISTOGRAM_BINS,
                    }
                ],
                {
                    "xaxis": {"title": "click-through rate"},
                    "yaxis": {"title": "ad-set days"},
                    "margin": {"t": 16, "r": 16, "b": 48, "l": 64},
                    "showlegend": False,
                },
                # A distribution is ordered along its axis: one colour. A hue
                # per bar would assert that the bins differ in kind.
                [ROLE_ACCENT],
            )
        )

    return charts
