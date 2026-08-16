"""The Marketing Agent: preparation, rules, and the boundaries it must keep.

The two that matter most are at the bottom: no marketing rule can reach the
auto-apply allowlist (ad spend is real money), and a non-ads CSV is refused
with the missing role named rather than analysed anyway.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.roles import ColumnRole
from app.marketing.config import MarketingConfig
from app.marketing.engine import run_marketing
from app.marketing.findings import MarketingFindingType, MarketingSeverity
from app.marketing.preprocess import drop_summary_rows, parse_money_series, parse_ratio_series, prepare
from app.numeric_text import parse_number
from app.semantic_roles import detect_for_run


def ads_frame(days: int = 28, adsets=("Prospecting", "Retargeting", "Lookalike"), **overrides) -> pd.DataFrame:
    """A Meta-shaped export, with the quirks a real one has."""
    rows = []
    for index, day in enumerate(pd.date_range("2026-01-01", periods=days)):
        for adset in adsets:
            clicks = overrides.get("clicks", 200)
            conversions = overrides.get("conversions", 8)
            rows.append(
                {
                    "Reporting starts": day.strftime("%Y-%m-%d"),
                    "Ad set name": adset,
                    "Amount spent (USD)": f"${120 + index:,.2f}",
                    "Impressions": 10000 + index * 10,
                    "Link clicks": clicks,
                    "Frequency": overrides.get("frequency", 2.1),
                    "Results": conversions,
                    "Purchase conversion value": 900.0 + index,
                }
            )
    frame = pd.DataFrame(rows)
    frame["Reporting starts"] = pd.to_datetime(frame["Reporting starts"])
    return frame


def analyse(df: pd.DataFrame, config: MarketingConfig | None = None):
    return run_marketing("run-under-test", df, detect_for_run(df, {}), config=config or MarketingConfig())


def findings_of(result, finding_type: MarketingFindingType):
    return [f for f in result.findings if f.finding_type is finding_type]


# ---- export quirks: currency, percentages, separators ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.56", 1234.56),
        ("1,234.56", 1234.56),
        ("£99", 99.0),
        ("1.234,56", 1234.56),      # European grouping
        ("10 000", 10000.0),
        ("2.3%", 2.3),
        ("-45.10", -45.10),
        ("", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_a_number_is_recovered_from_however_the_platform_formatted_it(raw, expected):
    assert parse_number(raw) == expected


def test_a_currency_column_is_parsed_and_the_transformation_is_recorded():
    series = pd.Series(["$1,000.00", "$2,500.50", "$300.00"], name="Amount spent (USD)")
    parsed, note = parse_money_series(series)

    assert list(parsed) == [1000.0, 2500.5, 300.0]
    assert note["transformation"] == "parsed currency-formatted text to a number"
    assert note["example_in"] == "$1,000.00"


def test_an_already_numeric_column_is_left_alone_and_reports_no_transformation():
    series = pd.Series([1.0, 2.0], name="spend")
    parsed, note = parse_money_series(series)

    assert list(parsed) == [1.0, 2.0]
    assert note is None


def test_a_percentage_ctr_column_becomes_a_fraction():
    parsed, note = parse_ratio_series(pd.Series(["2.30%", "1.90%"], name="CTR"), percent_threshold=1.0)

    assert parsed.round(4).tolist() == [0.023, 0.019]
    assert "divided by 100" in note["transformation"]


def test_a_fractional_ctr_column_is_left_as_a_fraction():
    """Decided per COLUMN from its median - a single value cannot tell you
    which convention it is in."""
    parsed, _ = parse_ratio_series(pd.Series([0.023, 0.019, 0.021], name="CTR"), percent_threshold=1.0)

    assert parsed.round(4).tolist() == [0.023, 0.019, 0.021]


def test_the_percent_threshold_is_configurable_not_hardcoded():
    values = pd.Series([2.3, 1.9], name="CTR")

    as_percent, _ = parse_ratio_series(values, percent_threshold=1.0)
    as_fraction, _ = parse_ratio_series(values, percent_threshold=10.0)

    assert as_percent.round(4).tolist() == [0.023, 0.019]
    assert as_fraction.tolist() == [2.3, 1.9]


# ---- platform summary rows ----


@pytest.mark.parametrize("label", ["Total", "TOTALS", " grand total ", "Summary", "Account total"])
def test_a_platform_summary_row_is_excluded_from_row_level_analysis(label):
    """It already contains the sum of every row above it - summing it too is
    the quietest way to double every number on the page."""
    df = pd.DataFrame({"Ad set name": ["A", "B", label], "spend": [1.0, 2.0, 3.0]})

    kept, matched, dropped = drop_summary_rows(df, "Ad set name", MarketingConfig().summary_row_labels)

    assert dropped == 1
    assert matched == [label]
    assert kept["spend"].sum() == 3.0


def test_summary_row_labels_are_configurable():
    df = pd.DataFrame({"Ad set name": ["A", "Gesamt"], "spend": [1.0, 9.0]})

    kept, _, dropped = drop_summary_rows(df, "Ad set name", ("gesamt",))

    assert dropped == 1 and kept["spend"].sum() == 1.0


def test_the_summary_row_is_gone_before_any_total_is_computed():
    df = ads_frame(days=5)
    total_row = {c: 0 for c in df.columns}
    total_row.update({"Ad set name": "Total", "Amount spent (USD)": "$999,999.00", "Reporting starts": pd.NaT})
    df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)

    result = analyse(df)
    totals = findings_of(result, MarketingFindingType.ACCOUNT_TOTALS)[0].payload

    assert result.preprocessing.summary_rows_excluded == 1
    assert totals.total_spend < 999_999


# ---- derived metrics, and zero denominators ----


def test_derived_metrics_are_correct_on_a_hand_computable_fixture():
    df = pd.DataFrame(
        {
            "Reporting starts": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Ad set name": ["A", "A"],
            "Amount spent (USD)": [100.0, 200.0],
            "Impressions": [1000, 2000],
            "Link clicks": [50, 100],
            "Results": [10, 5],
            "Purchase conversion value": [500.0, 400.0],
        }
    )
    prepared, report, _ = prepare(df, detect_for_run(df, {}), MarketingConfig())

    assert prepared["cpa"].tolist() == [10.0, 40.0]     # 100/10, 200/5
    assert prepared["roas"].tolist() == [5.0, 2.0]      # 500/100, 400/200
    assert prepared["ctr"].tolist() == [0.05, 0.05]     # 50/1000, 100/2000
    formulas = {d.metric: d.formula for d in report.derived_metrics}
    assert "`Amount spent (USD)` / `Results`" == formulas["cpa"]


def test_an_adset_with_zero_conversions_has_no_cpa_and_says_so():
    """Never an infinity, never a silent NaN standing in for one."""
    df = pd.DataFrame(
        {
            "Reporting starts": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "Ad set name": ["A", "A"],
            "Amount spent (USD)": [100.0, 200.0],
            "Impressions": [1000, 2000],
            "Link clicks": [50, 100],
            "Results": [10, 0],
            "Purchase conversion value": [500.0, 0.0],
        }
    )
    prepared, report, _ = prepare(df, detect_for_run(df, {}), MarketingConfig())
    cpa = report.derived_metrics[0]

    assert prepared["cpa"].tolist()[0] == 10.0
    assert pd.isna(prepared["cpa"].tolist()[1])
    assert not any(prepared["cpa"].abs() == float("inf")), "a zero denominator produced an infinity"
    assert cpa.undefined_row_count == 1
    assert "zero conversions" in cpa.undefined_reason


def test_a_period_with_no_conversions_reports_an_undefined_blended_cpa():
    df = ads_frame(days=8, conversions=0)
    totals = findings_of(analyse(df), MarketingFindingType.ACCOUNT_TOTALS)[0].payload

    assert totals.blended_cpa is None
    assert "blended_cpa" in totals.undefined
    assert "undefined" in totals.undefined["blended_cpa"]


# ---- the rules ----


def test_frequency_above_the_fatigue_threshold_warns():
    result = analyse(ads_frame(days=8, frequency=5.0))
    fired = findings_of(result, MarketingFindingType.FREQUENCY_FATIGUE)

    assert fired, "frequency fatigue did not fire"
    assert fired[0].severity is MarketingSeverity.WARNING
    assert fired[0].payload.observed == 5.0
    assert fired[0].payload.threshold == MarketingConfig().frequency_fatigue_threshold


def test_the_fatigue_threshold_is_genuinely_configurable():
    df = ads_frame(days=8, frequency=3.0)

    assert not findings_of(analyse(df), MarketingFindingType.FREQUENCY_FATIGUE)
    lowered = analyse(df, MarketingConfig(frequency_fatigue_threshold=2.0))
    assert findings_of(lowered, MarketingFindingType.FREQUENCY_FATIGUE)


def test_a_cpa_spike_against_the_prior_window_warns():
    rows = []
    for index, day in enumerate(pd.date_range("2026-01-01", periods=14)):
        # Conversions collapse in the second week; spend holds.
        conversions = 10 if index < 7 else 2
        rows.append(
            {
                "Reporting starts": day,
                "Ad set name": "A",
                "Amount spent (USD)": 100.0,
                "Impressions": 1000,
                "Link clicks": 50,
                "Results": conversions,
                "Purchase conversion value": 300.0,
            }
        )
    result = analyse(pd.DataFrame(rows))
    fired = findings_of(result, MarketingFindingType.CPA_ABOVE_BASELINE)

    assert fired, "CPA spike did not fire"
    assert fired[0].severity is MarketingSeverity.WARNING
    assert fired[0].payload.observed == pytest.approx(50.0)   # 100/2
    assert "within-run comparison" in fired[0].payload.compared_against


def test_a_sharp_ctr_decline_warns():
    rows = []
    for index, day in enumerate(pd.date_range("2026-01-01", periods=14)):
        clicks = 100 if index < 7 else 20
        rows.append(
            {
                "Reporting starts": day,
                "Ad set name": "A",
                "Amount spent (USD)": 100.0,
                "Impressions": 10000,
                "Link clicks": clicks,
                "Results": 5,
                "Purchase conversion value": 300.0,
            }
        )
    fired = findings_of(analyse(pd.DataFrame(rows)), MarketingFindingType.CTR_DECLINE)

    assert fired, "CTR decline did not fire"
    assert fired[0].payload.observed == pytest.approx(0.002)


def test_budget_mispacing_only_fires_against_a_CONFIGURED_target():
    """A pacing rule that invents its own target measures against a number
    nobody chose."""
    df = ads_frame(days=8)

    without = analyse(df)
    assert not findings_of(without, MarketingFindingType.BUDGET_MISPACING)
    assert any("no daily budget target is configured" in s["reason"] for s in without.skipped_rules)

    with_target = analyse(df, MarketingConfig(daily_budget_target=10.0))
    assert findings_of(with_target, MarketingFindingType.BUDGET_MISPACING)


def test_roas_below_target_is_an_improvement_not_a_warning():
    result = analyse(ads_frame(days=8), MarketingConfig(roas_target=100.0))
    fired = findings_of(result, MarketingFindingType.ROAS_BELOW_TARGET)

    assert fired
    assert fired[0].severity is MarketingSeverity.IMPROVEMENT


def test_an_adset_worse_than_the_account_median_is_an_improvement():
    rows = []
    for day in pd.date_range("2026-01-01", periods=6):
        for adset, conversions in (("Good", 20), ("Also good", 18), ("Poor", 1)):
            rows.append(
                {
                    "Reporting starts": day,
                    "Ad set name": adset,
                    "Amount spent (USD)": 100.0,
                    "Impressions": 1000,
                    "Link clicks": 50,
                    "Results": conversions,
                    "Purchase conversion value": 300.0,
                }
            )
    fired = findings_of(analyse(pd.DataFrame(rows)), MarketingFindingType.ADSET_UNDERPERFORMING)

    assert [f.payload.scope for f in fired] == ["Poor"]
    assert fired[0].severity is MarketingSeverity.IMPROVEMENT


def test_every_warning_states_what_it_was_compared_against():
    """A threshold with no stated basis is a magic number by another route."""
    result = analyse(ads_frame(days=8, frequency=5.0), MarketingConfig(daily_budget_target=10.0, roas_target=99.0))

    breaches = [f for f in result.findings if f.severity is not MarketingSeverity.KEY_VALUE]
    assert breaches
    for finding in breaches:
        assert finding.payload.compared_against.strip(), f"{finding.finding_type} states no comparison basis"
        assert finding.payload.threshold is not None


def test_a_rule_that_cannot_run_says_which_requirement_it_was_missing():
    df = ads_frame(days=8).drop(columns=["Frequency"])
    result = analyse(df)

    reasons = {s["rule"]: s["reason"] for s in result.skipped_rules}
    assert MarketingFindingType.FREQUENCY_FATIGUE.value in reasons
    assert "frequency" in reasons[MarketingFindingType.FREQUENCY_FATIGUE.value]


# ---- qualification ----


def test_a_non_ads_csv_does_not_qualify_and_names_what_was_missing():
    df = pd.DataFrame(
        {
            "customer_id": [1, 2, 3, 4] * 5,
            "order_date": pd.date_range("2026-01-01", periods=20),
            "product": ["mug", "lamp", "jar", "cup"] * 5,
        }
    )
    result = analyse(df)

    assert result.applicable is False
    assert result.findings == []
    assert "spend" in " ".join(result.missing_roles)
    assert "amount-spent" in result.not_applicable_reason


def test_a_refusal_still_reports_which_roles_WERE_found():
    """So a user can see how close the source was, not just that it failed."""
    df = pd.DataFrame({"order_date": pd.date_range("2026-01-01", periods=10), "clicks": range(10)})
    result = analyse(df)

    assert result.applicable is False
    assert result.resolved_roles, "a refusal reported no roles at all"


def test_the_required_role_set_is_configurable():
    df = pd.DataFrame({"Reporting starts": pd.date_range("2026-01-01", periods=10), "Impressions": range(10)})

    assert analyse(df).applicable is False
    relaxed = analyse(df, MarketingConfig(required_roles=(ColumnRole.EVENT_DATE,)))
    assert relaxed.applicable is True


# ---- the boundary that matters most ----


def test_no_marketing_rule_can_reach_the_auto_apply_allowlist():
    """Ad spend is real money. A wrong automatic correction to a campaign is
    not recoverable by re-running an ingest, so every warning escalates to a
    person and nothing here is applicable by the gate.

    Asserted structurally rather than by inspection: the gate's allowlist is
    keyed on validation rule names, and no marketing finding type appears in
    it - nor could one, since this agent emits findings and never a fix spec.
    """
    from app.actions import ALLOWLIST_ACTIONS

    allowlisted = {getattr(a, "value", str(a)) for a in ALLOWLIST_ACTIONS}
    for finding_type in MarketingFindingType:
        assert finding_type.value not in allowlisted, f"{finding_type.value} reached the auto-apply allowlist"


def test_the_marketing_package_produces_no_fix_specs_at_all():
    """The structural half of the guarantee: a rule cannot auto-fix anything
    because nothing it returns is shaped like something the gate could
    apply."""
    result = analyse(ads_frame(days=8, frequency=5.0))

    for finding in result.findings:
        payload = finding.payload.model_dump()
        assert "action" not in payload
        assert "fix" not in payload
        assert "spec" not in payload


def test_every_warning_severity_matches_the_declared_table():
    """A rule cannot quietly emit itself at a different severity than the one
    the escalation path was built around."""
    from app.marketing.findings import SEVERITY_OF

    result = analyse(ads_frame(days=8, frequency=5.0), MarketingConfig(daily_budget_target=10.0, roas_target=99.0))
    for finding in result.findings:
        assert finding.severity is SEVERITY_OF[finding.finding_type]
        assert finding.payload.severity is SEVERITY_OF[finding.finding_type]


def test_a_commerce_csv_with_a_cost_column_does_NOT_qualify():
    """The false positive this gate exists for: almost any commerce export
    has a cost column and a date. Showing a Marketing tab on a retail
    dataset is a worse failure than refusing an ads file."""
    df = pd.DataFrame(
        {
            "order_date": pd.date_range("2026-01-01", periods=30),
            "cost": [10.0 + i for i in range(30)],
            "region": ["north", "south", "east"] * 10,
        }
    )
    result = analyse(df)

    assert result.applicable is False
    assert "nothing only an ad platform reports" in result.not_applicable_reason
    assert "impressions" in result.not_applicable_reason


def test_the_ad_specific_requirement_is_configurable():
    df = pd.DataFrame(
        {
            "order_date": pd.date_range("2026-01-01", periods=30),
            "cost": [10.0 + i for i in range(30)],
        }
    )

    assert analyse(df).applicable is False
    relaxed = analyse(df, MarketingConfig(required_any_of=()))
    assert relaxed.applicable is True


# ---- regressions from a real Kaggle export (KAG_conversion_data.csv) ----
#
# A user uploaded the Facebook ad-campaign dataset and the agent refused it.
# Two separate defects, both real:
#   1. the column is literally named `Spent`, which the spend name hint did
#      not match - `spent` was only reachable behind an "amount" prefix
#   2. the file is aggregated per AD with no date column at all, and a
#      reporting date gated the whole agent


def kaggle_style_frame(rows: int = 200) -> pd.DataFrame:
    """The KAG_conversion_data.csv shape: one row per ad, NO date column."""
    import numpy as np

    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "ad_id": range(700000, 700000 + rows),
            "xyz_campaign_id": rng.choice([916, 936, 1178], rows),
            "fb_campaign_id": rng.integers(100000, 180000, rows),
            "age": rng.choice(["30-34", "35-39", "40-44"], rows),
            "gender": rng.choice(["M", "F"], rows),
            "interest": rng.integers(2, 114, rows),
            "Impressions": rng.integers(500, 3_000_000, rows),
            "Clicks": rng.integers(0, 400, rows),
            "Spent": np.round(rng.uniform(0, 640, rows), 2),
            "Total_Conversion": rng.integers(0, 61, rows),
            "Approved_Conversion": rng.integers(0, 22, rows),
        }
    )


@pytest.mark.parametrize("column", ["Spent", "Spend", "Amount spent (USD)", "Cost", "cost"])
def test_a_spend_column_is_detected_however_the_platform_spells_it(column):
    """`Spent` is what Kaggle's Facebook export and Google Ads both write."""
    df = pd.DataFrame(
        {
            column: [10.0 + i for i in range(30)],
            "Impressions": range(1000, 1030),
            "Clicks": range(10, 40),
        }
    )
    assigned = detect_for_run(df, {}).assigned()

    assert ColumnRole.SPEND in assigned, f"`{column}` was not detected as spend"
    assert assigned[ColumnRole.SPEND].column == column


def test_an_ad_export_with_no_date_column_still_qualifies():
    """Real exports are often aggregated per ad with no date at all.
    Refusing them loses spend, clicks, conversions, blended CPA and CTR and
    ad-set comparison, none of which need a date."""
    result = analyse(kaggle_style_frame())

    assert result.applicable is True, result.not_applicable_reason
    assert "event_date" in result.missing_roles
    totals = findings_of(result, MarketingFindingType.ACCOUNT_TOTALS)[0].payload
    assert totals.total_spend > 0
    assert totals.blended_cpa is not None
    assert totals.blended_ctr is not None


def test_the_time_based_rules_skip_with_a_stated_reason_when_there_is_no_date():
    """The date requirement moved from the gate to the individual rules, so
    each must say why it did not run."""
    result = analyse(kaggle_style_frame())
    reasons = {s["rule"]: s["reason"] for s in result.skipped_rules}

    for rule in (
        MarketingFindingType.CPA_ABOVE_BASELINE,
        MarketingFindingType.CTR_DECLINE,
        MarketingFindingType.BUDGET_MISPACING,
    ):
        assert rule.value in reasons, f"{rule.value} neither ran nor said why"
        assert "date" in reasons[rule.value]


def test_the_grain_reports_that_nothing_was_aggregated_when_there_is_no_date():
    """Claiming a grain the data never reached would be a claim about a
    transformation that did not run."""
    result = analyse(kaggle_style_frame())

    assert "no reporting-date column" in result.preprocessing.grain
    assert result.preprocessing.rows_in == result.preprocessing.rows_out


def test_a_key_value_that_needs_an_absent_column_says_which_column():
    """A blank with no reason is the thing this agent exists to avoid. The
    Kaggle export has no conversion-value column, so ROAS is undefined - and
    that has to be distinguishable from a zero denominator."""
    totals = findings_of(analyse(kaggle_style_frame()), MarketingFindingType.ACCOUNT_TOTALS)[0].payload

    assert totals.blended_roas is None
    assert "conversion-value" in totals.undefined["blended_roas"]


def test_spend_alone_without_an_ad_signal_still_does_not_qualify():
    """Relaxing the date requirement must not relax the false-positive gate:
    a commerce CSV with a cost column is still refused."""
    df = pd.DataFrame({"cost": [10.0 + i for i in range(30)], "region": ["n", "s", "e"] * 10})

    assert analyse(df).applicable is False
