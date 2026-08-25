"""Part 3: the deeper marketing analyses.

Two themes run through this file. The rules must FIRE on the shape they claim
to detect and stay silent otherwise - a fatigue rule that fires on every ad set
is a rule that has found nothing. And the machinery the brief said to reuse
must actually be reused, which is asserted structurally rather than by reading
the imports.
"""

import ast
import inspect
from pathlib import Path

import pandas as pd
import pytest

from app.marketing.config import MarketingConfig
from app.marketing.depth import (
    DEPTH_RULES,
    adset_efficiency_ranking,
    adset_fatigue_trend,
    adset_period_movement,
    spend_concentration,
)
from app.marketing.engine import run_marketing
from app.marketing.findings import MarketingFindingType, MarketingSeverity, SEVERITY_OF
from app.semantic_roles import detect_for_run

CAUSAL_WORDS = ("because", "caused", "due to", "drove", "led to", "resulted in", "explains")


#: Spend per ad set. "Runaway" is deliberately extreme so the account MEAN
#: and MEDIAN CPA disagree - without that, a rule ranking against the wrong
#: one produces identical output and the test proves nothing.
_SPEND = {"Prospecting": 200.0, "Retargeting": 120.0, "Lookalike": 40.0, "Scaling": 60.0, "Runaway": 900.0}


def ads_frame(
    days: int = 28,
    fatiguing: str | None = "Retargeting",
    adsets: tuple[str, ...] = ("Prospecting", "Retargeting", "Lookalike", "Scaling"),
) -> pd.DataFrame:
    """A realistic ad-platform export.

    `fatiguing` names the ad set whose frequency climbs while CTR falls.
    "Scaling" is the control: its frequency ALSO climbs, but its CTR climbs
    with it. Without that ad set, a fatigue rule that ignored performance
    entirely would produce identical output and no test would notice.
    """
    rows = []
    for day in range(days):
        date = (pd.Timestamp("2026-01-01") + pd.Timedelta(days=day)).date().isoformat()
        for adset in adsets:
            tiring = adset == fatiguing
            scaling = adset == "Scaling"
            impressions = 10000 + day * 10
            ctr = 0.05 - (day * 0.001 if tiring else 0.0) + (day * 0.0005 if scaling else 0.0)
            clicks = max(1, int(impressions * ctr))
            rows.append(
                {
                    "Reporting starts": date,
                    "Ad set name": adset,
                    "Amount spent (USD)": _SPEND.get(adset, 100.0),
                    "Impressions": impressions,
                    "Link clicks": clicks,
                    # Frequency rises for BOTH the fatiguing ad set and the
                    # scaling one; only their performance direction differs.
                    "Frequency": 2.0 + (day * 0.12 if (tiring or scaling) else 0.0),
                    "Results": max(1, int(clicks * 0.05)),
                    "Purchase conversion value": _SPEND.get(adset, 100.0) * 1.5,
                }
            )
    frame = pd.DataFrame(rows)
    # The connector normalises dates before the pack sees them.
    frame["Reporting starts"] = pd.to_datetime(frame["Reporting starts"])
    return frame


def prepared(frame: pd.DataFrame):
    from app.marketing.preprocess import prepare

    detection = detect_for_run(frame)
    work, _report, roles = prepare(frame, detection, MarketingConfig())
    return work, roles


# ---- the machinery is reused, not rebuilt ----


def test_the_pareto_engine_is_called_not_reimplemented():
    """The brief said reuse the Pareto machinery. Asserted on the import
    graph: app/marketing/depth.py must call the analytics engine, and must
    not contain band arithmetic of its own."""
    source = Path(inspect.getfile(spend_concentration)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.analytics.pareto"
        for alias in node.names
    }
    assert "run_abc_pareto" in imported, "spend concentration must call the analytics Pareto engine"

    # No second band assignment: the cutoffs live in one module.
    for marker in ("cumsum", "band_of", "DEFAULT_BAND_CUTOFFS ="):
        assert marker not in source, f"depth.py contains {marker!r} - that is a second Pareto implementation"


def test_period_movement_uses_the_comparison_delta():
    """Part 1's Delta, not a second set of change arithmetic."""
    source = Path(inspect.getfile(adset_period_movement)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.comparison.models"
        for alias in node.names
    }
    assert "Delta" in imported


def test_every_depth_rule_is_registered_with_the_engine():
    from app.marketing.rules import RULES

    for rule in DEPTH_RULES:
        assert rule in RULES, f"{rule.__name__} is not registered, so the engine will never run it"


# ---- they fire on the shape they claim, and not otherwise ----


def test_fatigue_fires_only_on_the_ad_set_whose_frequency_rises_as_performance_falls():
    """The discriminating test. A rule that fires on every ad set has found
    nothing."""
    work, roles = prepared(ads_frame(fatiguing="Retargeting"))

    findings, _skipped = adset_fatigue_trend(work, roles, MarketingConfig())

    scopes = {f.payload.scope for f in findings}
    assert scopes == {"Retargeting"}, f"expected only Retargeting to fatigue, got {scopes}"
    # "Scaling" rises on frequency TOO. A rule that only checked frequency
    # would flag it, and would be reporting growth as fatigue.
    assert "Scaling" not in scopes, "an ad set whose performance rose with its frequency is not fatiguing"


def test_fatigue_is_silent_when_nothing_fatigues():
    work, roles = prepared(ads_frame(fatiguing=None))

    findings, _skipped = adset_fatigue_trend(work, roles, MarketingConfig())

    assert findings == []


def test_fatigue_never_claims_one_trend_explains_the_other():
    """Two co-occurring movements. The columns cannot support more."""
    work, roles = prepared(ads_frame(fatiguing="Retargeting"))

    findings, _skipped = adset_fatigue_trend(work, roles, MarketingConfig())

    blob = " ".join(f.payload.compared_against for f in findings).lower()
    assert "co-occurring" in blob
    for word in CAUSAL_WORDS:
        assert word not in blob, f"fatigue finding used causal language: {word}"


def test_spend_concentration_reports_both_spend_and_return():
    work, roles = prepared(ads_frame())

    findings, _skipped = spend_concentration(work, roles, MarketingConfig())

    measured = {f.payload.measured_over for f in findings}
    assert measured == {"spend", "return"}
    for finding in findings:
        assert finding.payload.entity_count >= 1
        assert 0 < finding.payload.value_share <= 1
        assert finding.payload.top_entities


def test_spend_concentration_says_so_when_there_is_no_return_column():
    frame = ads_frame().drop(columns=["Purchase conversion value"])
    work, roles = prepared(frame)

    findings, skipped = spend_concentration(work, roles, MarketingConfig())

    assert {f.payload.measured_over for f in findings} == {"spend"}
    assert any("conversion-value" in s["reason"] for s in skipped), "the absent measurement must be reported"


def test_efficiency_ranking_states_the_median_value_it_compared_against():
    """The NUMBER must be in the sentence, not just the word "median". A
    reader cannot judge "above the median" without knowing what it was."""
    work, roles = prepared(ads_frame())

    findings, _skipped = adset_efficiency_ranking(work, roles, MarketingConfig())

    assert findings, "ad sets with differing CPA must produce a ranking"
    for finding in findings:
        assert finding.payload.threshold > 0
        assert finding.evidence.parameters["account_median"] == pytest.approx(finding.payload.threshold)
        # The median's value, formatted as the rule formats it.
        assert f"{finding.payload.threshold:,.4g}" in finding.payload.compared_against, (
            "the comparison basis must state the median VALUE, not merely the word"
        )


def test_efficiency_ranking_uses_the_median_not_the_mean():
    """One runaway ad set drags a mean far enough that most ad sets sit
    "below average", which is arithmetic rather than a finding. The fixture
    includes such an ad set so the two answers actually differ."""
    frame = ads_frame(adsets=("Prospecting", "Retargeting", "Lookalike", "Scaling", "Runaway"))
    work, roles = prepared(frame)

    from app.analytics.roles import ColumnRole

    by_adset = work.groupby(roles[ColumnRole.CAMPAIGN_ID], observed=True)["cpa"].mean().dropna()
    median, mean = float(by_adset.median()), float(by_adset.mean())
    assert mean > median * 1.1, "the fixture must skew, or this test cannot distinguish the two"

    findings, _skipped = adset_efficiency_ranking(work, roles, MarketingConfig())
    flagged = {f.payload.scope for f in findings if f.payload.metric == "cpa"}

    assert flagged == {str(a) for a in by_adset[by_adset > median].index}
    assert flagged != {str(a) for a in by_adset[by_adset > mean].index}, (
        "median and mean flag the same ad sets here, so this test proves nothing"
    )


def test_efficiency_ranking_refuses_below_the_configured_minimum():
    """A median over two ad sets is a statement about two numbers."""
    frame = ads_frame()
    frame = frame[frame["Ad set name"].isin(["Prospecting", "Retargeting"])]
    work, roles = prepared(frame)

    findings, _skipped = adset_efficiency_ranking(work, roles, MarketingConfig(min_adsets_for_ranking=3))

    assert findings == []


def test_period_movement_ignores_an_immaterial_move():
    """Without a materiality floor every ad set reports a movement every run,
    and the one that matters is buried.

    Asserted as a strict inequality on a frame that HAS immaterial moves: an
    "at most as many" comparison passes when the floor does nothing at all,
    which is exactly the version falsification caught.
    """
    work, roles = prepared(ads_frame(fatiguing=None))

    with_floor, _ = adset_period_movement(work, roles, MarketingConfig(min_relative_movement=0.05))
    without_floor, _ = adset_period_movement(work, roles, MarketingConfig(min_relative_movement=0.0))

    assert len(without_floor) > len(with_floor), (
        "this frame has no immaterial moves, so the floor is untested here"
    )
    for finding in with_floor:
        relative = finding.evidence.parameters.get("relative_change")
        assert relative is None or abs(relative) >= 0.05


def test_period_movement_reports_a_move_from_zero_rather_than_hiding_it():
    """Delta.relative is undefined from a zero baseline. That IS material - a
    metric appearing from nothing - so it must not be filtered out by a
    comparison against a threshold it cannot be measured against."""
    from app.comparison.models import Delta

    delta = Delta(label="cpa", before=0.0, after=12.0)

    assert delta.relative is None
    assert delta.changed


# ---- the pack's existing guarantees still hold ----


def test_every_depth_finding_states_rule_observed_threshold_and_basis():
    work, roles = prepared(ads_frame())
    config = MarketingConfig()

    for rule in DEPTH_RULES:
        findings, _skipped = rule(work, roles, config)
        for finding in findings:
            payload = finding.payload.model_dump()
            assert payload["finding_type"], f"{rule.__name__} produced a finding with no rule"
            assert payload["compared_against"], f"{rule.__name__} produced a finding with no comparison basis"
            if finding.finding_type is not MarketingFindingType.SPEND_CONCENTRATION:
                assert payload["observed"] is not None
                assert payload["threshold"] is not None


def test_derived_metrics_state_the_within_run_basis():
    """CPA, ROAS and CTR are derived AFTER baseline profiling, so no stored
    baseline exists for them. Every finding about one must say so."""
    work, roles = prepared(ads_frame())

    findings, _skipped = adset_period_movement(work, roles, MarketingConfig())

    assert findings
    for finding in findings:
        assert "within this run" in finding.payload.compared_against


def test_no_depth_finding_is_on_the_auto_apply_allowlist():
    """Nothing new is auto-applicable. Every depth finding either escalates
    or is informational."""
    for finding_type in (
        MarketingFindingType.ADSET_PERIOD_MOVEMENT,
        MarketingFindingType.ADSET_FATIGUE_TREND,
        MarketingFindingType.ADSET_EFFICIENCY_RANK,
        MarketingFindingType.SPEND_CONCENTRATION,
    ):
        assert SEVERITY_OF[finding_type] in {
            MarketingSeverity.WARNING,
            MarketingSeverity.IMPROVEMENT,
            MarketingSeverity.KEY_VALUE,
        }


def test_one_depth_rule_raising_never_sinks_the_rest(monkeypatch):
    """The engine's existing guarantee must cover the new rules too."""

    def _explode(df, roles, config, baseline_profile=None):
        raise RuntimeError("boom")

    # Patched where the engine BINDS it, not where it is defined: the engine
    # does `from app.marketing.rules import RULES` at import, so rebinding the
    # source module leaves the engine holding the original tuple.
    monkeypatch.setattr(
        "app.marketing.engine.RULES",
        (*[r for r in DEPTH_RULES if r is not spend_concentration], _explode),
    )

    frame = ads_frame()
    result = run_marketing("r", frame, detect_for_run(frame))

    assert result.applicable
    assert any("boom" in s.get("reason", "") for s in result.skipped_rules)
    assert result.findings, "the surviving rules must still have produced findings"


# ---- charts ----


def test_the_depth_charts_go_through_the_existing_spec_path():
    frame = ads_frame()
    result = run_marketing("r", frame, detect_for_run(frame))

    ids = {c["chart_id"] for c in result.charts}
    assert "marketing-adset_spend_vs_return" in ids
    assert any(c.startswith("marketing-adset_") and "vs_median" in c for c in ids)

    for chart in result.charts:
        assert chart["figure_json"]["data"], "a chart spec must carry its figure"
        assert chart["spec_version"], "specs are versioned"


def test_no_depth_chart_colour_resolves_to_a_status_token():
    frame = ads_frame()
    result = run_marketing("r", frame, detect_for_run(frame))

    for chart in result.charts:
        for role in chart["colour_roles"]:
            assert role in {"accent", "categorical", "sequential"}, f"{chart['chart_id']} uses colour role {role!r}"
