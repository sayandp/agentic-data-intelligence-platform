"""Analyses 2-7 against planted ground truth.

Each fixture is constructed so the right answer is known by hand, because a
test that only asserts "it produced findings" would pass on arithmetic that
is silently wrong.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics.basket import run_market_basket
from app.analytics.cohorts import run_cohort_retention, run_historical_clv, run_retention_churn
from app.analytics.engine import run_business_analytics
from app.analytics.findings import AnalysisFindingType
from app.analytics.rfm import DEFAULT_SEGMENT_RULES, assign_segment, run_rfm
from app.analytics.roles import SemanticColumnDetector, confirmed_detection
from app.analytics.segmentation import run_behavioural_segmentation

DAY = pd.Timedelta(days=1)
BASE = pd.Timestamp("2024-01-01")


def _detect(df):
    return SemanticColumnDetector().detect(df)


def _confirmed(df, **roles):
    """Roles supplied as if a human had confirmed them.

    Detection deliberately refuses on tiny tables - two customers is not
    evidence of anything - so a hand-computable fixture cannot rely on it.
    Confirming the roles keeps the ANALYSIS logic testable independently of
    the DETECTOR's tuning, and exercises the confirmation path itself.
    """
    return confirmed_detection(len(df), **roles)


def _payloads(result, kind):
    return [f.payload for f in result.findings if f.finding_type is kind]


# ---- 2. RFM ----


def test_rfm_rule_table_is_a_pure_lookup():
    """The naming logic is a table, testable without any data at all - which
    is the point of keeping it as data rather than buried thresholds."""
    assert assign_segment(5, 5, 5) == "champions"
    assert assign_segment(1, 1, 1) == "lost"
    assert assign_segment(1, 5, 5) == "at_risk"
    assert assign_segment(5, 1, 1) == "promising"
    # Every rule in the table is reachable by its own midpoint.
    for name, (r_lo, r_hi), (f_lo, f_hi), (m_lo, m_hi) in DEFAULT_SEGMENT_RULES:
        assert assign_segment((r_lo + r_hi) // 2, (f_lo + f_hi) // 2, (m_lo + m_hi) // 2) is not None


def test_rfm_scores_recency_inverted():
    """Recency is the one dimension where a LOWER raw value is better, and
    the classic place an RFM implementation is silently wrong."""
    rows = []
    for entity, days_ago in (("recent", 1), ("stale", 300)):
        rows.append({"customer_id": entity, "order_date": BASE + 400 * DAY - days_ago * DAY, "revenue": 100.0})
    df = pd.DataFrame(rows)
    result = run_rfm(df, _confirmed(df, entity_id="customer_id", event_date="order_date", monetary="revenue"))

    assert result.ran
    segments = {p.segment: p for p in _payloads(result, AnalysisFindingType.SEGMENT_PROFILE)}
    recent_r = max(p.centre["r_score"] for p in segments.values() if p.centre["recency_days"] < 100)
    stale_r = max(p.centre["r_score"] for p in segments.values() if p.centre["recency_days"] > 100)
    assert recent_r > stale_r


def test_rfm_segment_shares_account_for_every_entity():
    """No customer is silently dropped - that is what `unsegmented` is for."""
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame(
        {
            "customer_id": rng.integers(1, 80, n),
            "order_date": BASE + pd.to_timedelta(rng.integers(0, 300, n), unit="D"),
            "revenue": np.round(rng.lognormal(3, 0.6, n), 2),
        }
    )
    result = run_rfm(df, _detect(df))
    payloads = _payloads(result, AnalysisFindingType.SEGMENT_PROFILE)

    assert sum(p.entity_count for p in payloads) == df["customer_id"].nunique()
    assert sum(p.entity_share for p in payloads) == pytest.approx(1.0, abs=1e-4)
    assert sum(p.value_share for p in payloads) == pytest.approx(1.0, abs=1e-4)


def test_rfm_echoes_the_full_rule_table():
    """A segment name whose definition is invisible is the "magic threshold"
    the brief ruled out."""
    df = pd.DataFrame(
        {"customer_id": ["a", "b"] * 15, "order_date": BASE + pd.to_timedelta(range(30), unit="D"), "revenue": [10.0] * 30}
    )
    result = run_rfm(df, _detect(df))
    rules = result.parameters["segment_rules"]
    assert len(rules) == len(DEFAULT_SEGMENT_RULES)
    assert {r["segment"] for r in rules} == {n for n, *_ in DEFAULT_SEGMENT_RULES}


# ---- 3. cohort retention ----


def test_cohort_retention_ground_truth():
    """Planted: cohort Jan has 2 customers, both return in Feb (100% at
    offset 1). Cohort Feb has 1 customer who never returns."""
    rows = [
        {"customer_id": "a", "order_date": pd.Timestamp("2024-01-05")},
        {"customer_id": "b", "order_date": pd.Timestamp("2024-01-06")},
        {"customer_id": "a", "order_date": pd.Timestamp("2024-02-05")},
        {"customer_id": "b", "order_date": pd.Timestamp("2024-02-06")},
        {"customer_id": "c", "order_date": pd.Timestamp("2024-02-10")},
        {"customer_id": "a", "order_date": pd.Timestamp("2024-03-05")},
    ]
    df = pd.DataFrame(rows)
    result = run_cohort_retention(df, _detect(df))

    assert result.ran
    matrix = _payloads(result, AnalysisFindingType.RETENTION_MATRIX)[0]
    assert matrix.granularity == "month"
    assert matrix.cohort_labels[:2] == ["2024-01", "2024-02"]
    assert matrix.cohort_sizes[:2] == [2, 1]
    # Offset 0 is always everyone; offset 1 for the Jan cohort is both.
    assert matrix.retained_share[0][0] == pytest.approx(1.0)
    assert matrix.retained_share[0][1] == pytest.approx(1.0)
    # Feb cohort (c) never returns.
    assert matrix.retained_share[1][1] == pytest.approx(0.0)


def test_cohort_future_periods_are_none_not_zero():
    """A cohort acquired last month has NO data for offset 6 yet. Reporting
    that as 0% retention would be a fabricated number."""
    rows = [
        {"customer_id": "a", "order_date": pd.Timestamp("2024-01-05")},
        {"customer_id": "a", "order_date": pd.Timestamp("2024-03-05")},
        {"customer_id": "z", "order_date": pd.Timestamp("2024-03-10")},
    ]
    df = pd.DataFrame(rows)
    matrix = _payloads(run_cohort_retention(df, _detect(df)), AnalysisFindingType.RETENTION_MATRIX)[0]

    last_cohort = matrix.retained_share[-1]
    assert last_cohort[0] == pytest.approx(1.0)
    assert any(v is None for v in last_cohort[1:]), "unobserved periods must be None, never 0.0"


def test_cohort_granularity_is_configurable():
    # `b` must first appear on a LATER day than `a`, or both land in the
    # same daily cohort and there is nothing to compare - which is what the
    # single-cohort refusal correctly said when this fixture was wrong.
    df = pd.DataFrame(
        {
            "customer_id": ["a"] * 10 + ["b"] * 10,
            "order_date": list(pd.date_range("2024-01-01", periods=10))
            + list(pd.date_range("2024-01-03", periods=10)),
        }
    )
    result = run_cohort_retention(df, _confirmed(df, entity_id="customer_id", event_date="order_date"), granularity="day")
    assert result.ran
    assert result.parameters["granularity"] == "day"


def test_cohort_rejects_an_unknown_granularity():
    df = pd.DataFrame({"customer_id": ["a", "b"], "order_date": [BASE, BASE]})
    with pytest.raises(ValueError):
        run_cohort_retention(df, _detect(df), granularity="fortnight")


def test_cohort_refuses_with_a_single_cohort():
    df = pd.DataFrame(
        {"customer_id": ["a", "b", "c"] * 5, "order_date": [pd.Timestamp("2024-01-05")] * 15}
    )
    result = run_cohort_retention(df, _confirmed(df, entity_id="customer_id", event_date="order_date"))
    assert not result.ran
    assert "at least two" in result.not_run_reason


# ---- 4. behavioural segmentation ----


def test_segmentation_finds_two_planted_clusters():
    """Two well-separated groups: frequent big spenders vs one-off small
    ones. If clustering cannot find this, it cannot find anything."""
    rows = []
    for i in range(40):
        for _ in range(8):
            rows.append({"customer_id": f"big{i}", "order_date": BASE + pd.Timedelta(days=i % 30), "revenue": 500.0})
    for i in range(40):
        rows.append({"customer_id": f"small{i}", "order_date": BASE + pd.Timedelta(days=200 + i % 30), "revenue": 5.0})
    df = pd.DataFrame(rows)

    result = run_behavioural_segmentation(df, _detect(df))
    assert result.ran, result.not_run_reason
    assert result.parameters["chosen_k"] == 2
    assert result.parameters["silhouette"] >= result.parameters["silhouette_floor"]
    assert result.parameters["seed"] == 42


def test_segmentation_is_seeded_and_reproducible():
    rows = []
    rng = np.random.default_rng(7)
    for i in range(60):
        for _ in range(int(rng.integers(1, 6))):
            rows.append(
                {
                    "customer_id": f"c{i}",
                    "order_date": BASE + pd.Timedelta(days=int(rng.integers(0, 200))),
                    "revenue": float(rng.lognormal(3, 0.8)),
                }
            )
    df = pd.DataFrame(rows)
    detection = _detect(df)
    first = run_behavioural_segmentation(df, detection).model_dump()
    second = run_behavioural_segmentation(df, detection).model_dump()
    assert first == second


def test_segmentation_reports_no_stable_segmentation_rather_than_inventing_one():
    """The Modeling Agent's baseline gate, applied to clustering: raising
    the floor above what the data supports must produce an honest refusal,
    not weaker segments."""
    rng = np.random.default_rng(3)
    rows = [
        {
            "customer_id": f"c{i}",
            "order_date": BASE + pd.Timedelta(days=int(rng.integers(0, 100))),
            "revenue": float(rng.uniform(10, 12)),
        }
        for i in range(80)
    ]
    df = pd.DataFrame(rows)
    result = run_behavioural_segmentation(
        df, _confirmed(df, entity_id="customer_id", event_date="order_date", monetary="revenue"), silhouette_floor=0.99
    )

    assert not result.ran
    assert "no stable segmentation found" in result.not_run_reason
    assert result.findings == []
    # The evidence for the refusal is kept, not discarded.
    assert "best_silhouette" in result.parameters
    assert result.parameters["silhouette_floor"] == 0.99


def test_segmentation_refuses_on_too_few_entities():
    df = pd.DataFrame(
        {"customer_id": ["a", "b", "c"], "order_date": [BASE] * 3, "revenue": [1.0, 2.0, 3.0]}
    )
    result = run_behavioural_segmentation(df, _confirmed(df, entity_id="customer_id", event_date="order_date", monetary="revenue"))
    assert not result.ran
    assert "at least" in result.not_run_reason


# ---- 5. market basket ----


def _planted_basket() -> pd.DataFrame:
    """80 transactions where bread+butter always co-occur, plus noise."""
    rows = []
    for t in range(80):
        for item in ("bread", "butter"):
            rows.append({"order_id": f"T{t}", "product": item})
    for t in range(80, 160):
        rows.append({"order_id": f"T{t}", "product": "tea"})
        rows.append({"order_id": f"T{t}", "product": "jam"})
    return pd.DataFrame(rows)


def test_market_basket_finds_the_planted_rule():
    df = _planted_basket()
    result = run_market_basket(df, _detect(df))

    assert result.ran, result.not_run_reason
    rules = _payloads(result, AnalysisFindingType.ASSOCIATION_RULE)
    pairs = {(tuple(r.antecedent), tuple(r.consequent)) for r in rules}
    assert (("bread",), ("butter",)) in pairs
    planted = next(r for r in rules if r.antecedent == ["bread"] and r.consequent == ["butter"])
    assert planted.confidence == pytest.approx(1.0)
    assert planted.lift > 1.0


def test_market_basket_filters_to_lift_above_one():
    df = _planted_basket()
    result = run_market_basket(df, _detect(df))
    for rule in _payloads(result, AnalysisFindingType.ASSOCIATION_RULE):
        assert rule.lift > 1.0


def test_market_basket_skips_single_item_transactions_and_says_so():
    """Not "no rules found" - that would suggest it ran and found nothing
    interesting, rather than that it was never eligible."""
    df = pd.DataFrame({"order_id": [f"T{i}" for i in range(100)], "product": ["bread"] * 100})
    result = run_market_basket(df, _confirmed(df, transaction_id="order_id", item_id="product"))

    assert not result.ran
    assert "single-item" in result.not_run_reason
    assert result.findings == []


def test_market_basket_support_threshold_suppresses_weak_rules():
    df = _planted_basket()
    strict = run_market_basket(df, _detect(df), min_support=0.99)
    assert not strict.ran
    assert "support" in strict.not_run_reason


# ---- 6. retention / churn ----


def test_churn_window_is_stated_and_configurable():
    """The brief: do not invent a churn definition silently."""
    rows = [
        {"customer_id": "active", "order_date": BASE + 100 * DAY},
        {"customer_id": "active", "order_date": BASE + 190 * DAY},
        {"customer_id": "lapsed", "order_date": BASE},
    ]
    df = pd.DataFrame(rows)
    result = run_retention_churn(df, _detect(df), inactivity_window_days=30)

    assert result.ran
    payload = _payloads(result, AnalysisFindingType.REPEAT_BEHAVIOUR)[0]
    assert payload.inactivity_window_days == 30
    assert payload.entity_count == 2
    assert payload.repeat_entity_count == 1
    assert payload.repeat_rate == pytest.approx(0.5)
    # `lapsed` last seen 190 days before the observation end -> inactive.
    assert payload.inactive_entity_count == 1
    assert result.parameters["inactivity_window_days"] == 30


def test_churn_rejects_a_nonsensical_window():
    df = pd.DataFrame({"customer_id": ["a", "b"], "order_date": [BASE, BASE]})
    with pytest.raises(ValueError):
        run_retention_churn(df, _detect(df), inactivity_window_days=0)


def test_churn_gap_distribution_ground_truth():
    """One customer, events 10 days apart -> median gap exactly 10."""
    rows = [{"customer_id": "a", "order_date": BASE + i * 10 * DAY} for i in range(5)]
    rows += [{"customer_id": "b", "order_date": BASE + i * 10 * DAY} for i in range(5)]
    df = pd.DataFrame(rows)
    payload = _payloads(run_retention_churn(df, _detect(df)), AnalysisFindingType.REPEAT_BEHAVIOUR)[0]
    assert payload.gap_days_median == pytest.approx(10.0)


# ---- 7. historical CLV ----


def test_clv_is_revenue_based_and_says_so_without_a_margin():
    """Never assume a margin - a revenue figure read as profit is the most
    misleading thing this analysis could produce."""
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame(
        {
            "customer_id": rng.integers(1, 40, n),
            "order_date": BASE + pd.to_timedelta(rng.integers(0, 200, n), unit="D"),
            "revenue": np.round(rng.lognormal(3, 0.5, n), 2),
        }
    )
    result = run_historical_clv(df, _detect(df))

    assert result.ran
    assert result.parameters["value_basis"] == "revenue"
    assert result.parameters["margin_rate"] is None
    assert result.parameters["descriptive_not_predictive"] is True
    for payload in _payloads(result, AnalysisFindingType.LIFETIME_VALUE):
        assert payload.value_basis == "revenue"


def test_clv_applies_a_supplied_margin_and_records_it():
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame(
        {
            "customer_id": rng.integers(1, 40, n),
            "order_date": BASE + pd.to_timedelta(rng.integers(0, 200, n), unit="D"),
            "revenue": np.round(rng.lognormal(3, 0.5, n), 2),
        }
    )
    detection = _detect(df)
    revenue_based = run_historical_clv(df, detection)
    margin_based = run_historical_clv(df, detection, margin_rate=0.25)

    assert margin_based.parameters["value_basis"] == "margin"
    assert margin_based.parameters["margin_rate"] == 0.25
    r_total = sum(p.historical_value_per_entity for p in _payloads(revenue_based, AnalysisFindingType.LIFETIME_VALUE))
    m_total = sum(p.historical_value_per_entity for p in _payloads(margin_based, AnalysisFindingType.LIFETIME_VALUE))
    assert m_total == pytest.approx(r_total * 0.25, rel=1e-6)


def test_clv_rejects_an_impossible_margin():
    df = pd.DataFrame({"customer_id": ["a", "b"], "order_date": [BASE, BASE], "revenue": [1.0, 2.0]})
    with pytest.raises(ValueError):
        run_historical_clv(df, _detect(df), margin_rate=1.5)


def test_clv_ground_truth_on_a_planted_customer():
    """One customer, 4 orders of 25 -> AOV 25, frequency 4, value 100."""
    df = pd.DataFrame(
        {
            "customer_id": ["solo"] * 4,
            "order_date": [BASE + i * 10 * DAY for i in range(4)],
            "revenue": [25.0] * 4,
        }
    )
    payloads = _payloads(
        run_historical_clv(df, _confirmed(df, entity_id="customer_id", event_date="order_date", monetary="revenue")),
        AnalysisFindingType.LIFETIME_VALUE,
    )
    assert len(payloads) == 1
    p = payloads[0]
    assert p.average_order_value == pytest.approx(25.0)
    assert p.purchase_frequency == pytest.approx(4.0)
    assert p.historical_value_per_entity == pytest.approx(100.0)
    assert p.observed_lifespan_days == pytest.approx(30.0)


# ---- engine ----


def test_engine_assigns_unique_citable_finding_ids():
    """A GroundedClaim cites these, so they must be stable and unique across
    the whole run's output, not per-analysis."""
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame(
        {
            "order_id": [f"T{i}" for i in range(n)],
            "customer_id": rng.integers(1, 50, n),
            "order_date": BASE + pd.to_timedelta(rng.integers(0, 300, n), unit="D"),
            "revenue": np.round(rng.lognormal(3, 0.6, n), 2),
        }
    )
    findings = run_business_analytics("run-1", df).all_findings()

    ids = [f.id for f in findings]
    assert all(ids), "every finding must carry an id"
    assert len(ids) == len(set(ids)), "ids must be unique across the whole run"


def test_engine_reports_every_analysis_even_when_it_cannot_run():
    df = pd.DataFrame({"revenue": np.linspace(1, 100, 50)})
    out = run_business_analytics("run-2", df)

    assert len(out.results) == 7
    assert {r.analysis for r in out.results if not r.ran}, "refusals must be present, not omitted"
    for result in out.results:
        assert result.ran or result.not_run_reason, "a refusal must always carry a reason"


def test_engine_survives_one_analysis_raising(monkeypatch):
    """One failing analysis must never take the other six down."""
    import app.analytics.engine as engine

    def boom(*_args, **_kwargs):
        raise RuntimeError("planted failure")

    monkeypatch.setattr(engine, "_ANALYSES", ((engine.AnalysisKind.ABC_PARETO, boom),) + engine._ANALYSES[1:])
    df = pd.DataFrame({"product": ["a", "b", "c"], "revenue": [1.0, 2.0, 3.0]})
    out = engine.run_business_analytics("run-3", df)

    failed = next(r for r in out.results if r.analysis == "abc_pareto")
    assert not failed.ran
    assert "planted failure" in failed.not_run_reason
    assert len(out.results) == 7


def test_engine_output_round_trips_through_json():
    rng = np.random.default_rng(2)
    n = 200
    df = pd.DataFrame(
        {
            "customer_id": rng.integers(1, 40, n),
            "order_date": BASE + pd.to_timedelta(rng.integers(0, 200, n), unit="D"),
            "revenue": np.round(rng.lognormal(3, 0.5, n), 2),
        }
    )
    from app.analytics.findings import BusinessAnalyticsFindings

    out = run_business_analytics("run-4", df)
    restored = BusinessAnalyticsFindings.model_validate(out.model_dump(mode="json"))
    assert len(restored.all_findings()) == len(out.all_findings())


_CAUSAL_WORDS = ("cause", "caused", "drive", "driven", "driver", "impact", "because", "effect", "influence", "responsible")


def test_no_causal_vocabulary_in_any_analysis_output():
    rng = np.random.default_rng(5)
    rows = []
    for t in range(120):
        for item in rng.choice(["bread", "milk", "eggs"], 2, replace=False):
            rows.append(
                {
                    "order_id": f"T{t}",
                    "customer_id": int(rng.integers(1, 30)),
                    "product": str(item),
                    "order_date": BASE + pd.Timedelta(days=int(rng.integers(0, 200))),
                    "revenue": float(np.round(rng.lognormal(3, 0.5), 2)),
                }
            )
    df = pd.DataFrame(rows)
    blob = str(run_business_analytics("run-5", df).model_dump()).lower()
    for word in _CAUSAL_WORDS:
        assert word not in blob, f"causal vocabulary {word!r} reached an analysis payload"


# ---- Narrative grounding accepts these findings on IDENTICAL terms ----


def test_narrative_can_ground_a_claim_on_an_analytics_finding():
    """The brief: "extend grounding to accept these findings as claim
    sources - the SAME two-stage grounded generation, the SAME post-checks,
    no exceptions". So a claim citing an analytics id must survive grounding
    exactly as one citing an exploration id does."""
    from app.narrative.agent import NarrativeAgent
    from app.narrative.models import GenerationMode, GroundedClaim, GroundedClaimsResponse
    from app.narrative.pipeline import generate_narrative_report
    from tests.fakes import FakeLLMClient
    from tests.test_narrative_pipeline import _clean_findings

    exploration, frame = _clean_findings()
    analytics = run_business_analytics(
        "run-ground",
        pd.DataFrame({"product": ["a", "b", "c"], "revenue": [50.0, 30.0, 20.0]}),
    )
    analytics_id = analytics.all_findings()[0].id

    claim = GroundedClaim(claim_text="Band A accounts for most of the total.", finding_ids=[analytics_id], values=[])
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(default=GroundedClaimsResponse(claims=[claim])), sleep=lambda _s: None
    )

    report = generate_narrative_report(exploration, frame, agent, analytics_findings=analytics)

    # Stage 1 accepted it, so the run did NOT fall back for want of a
    # grounded claim. (Stage 2 uses the same fake client, so the report
    # itself may still degrade - what matters here is that the claim was not
    # rejected as citing an unknown finding.)
    assert report.fallback_reason is None or "unknown finding_ids" not in report.fallback_reason
    if report.generation_mode is GenerationMode.LLM:
        assert any(analytics_id in c.finding_ids for c in report.grounded_claims)


def test_a_claim_citing_no_real_finding_is_still_rejected():
    """Extending the id set must not have loosened the check itself."""
    from app.narrative.agent import NarrativeAgent
    from app.narrative.models import GenerationMode, GroundedClaim, GroundedClaimsResponse
    from app.narrative.pipeline import generate_narrative_report
    from tests.fakes import FakeLLMClient
    from tests.test_narrative_pipeline import _clean_findings

    exploration, frame = _clean_findings()
    analytics = run_business_analytics(
        "run-ground-2", pd.DataFrame({"product": ["a", "b"], "revenue": [10.0, 20.0]})
    )
    claim = GroundedClaim(claim_text="Invented.", finding_ids=["no-such-finding"], values=[])
    agent = NarrativeAgent(
        llm_client=FakeLLMClient(default=GroundedClaimsResponse(claims=[claim])), sleep=lambda _s: None
    )

    report = generate_narrative_report(exploration, frame, agent, analytics_findings=analytics)

    assert report.generation_mode is GenerationMode.TEMPLATE
    assert "no-such-finding" in report.fallback_reason
