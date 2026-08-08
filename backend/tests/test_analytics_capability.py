"""PART 0 - capability detection, and the first analysis gated on it.

The brief's warning is the design constraint: "if capability detection is
unreliable, everything downstream is noise", and "a method that silently
never fires is dead code". So these tests care as much about REFUSALS as
about successes - a detector that says yes to everything is exactly as
useless as one that says no to everything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics.applicability import AnalysisKind, build_applicability_report
from app.analytics.findings import AnalysisFindingType, BusinessAnalyticsFindings
from app.analytics.pareto import run_abc_pareto
from app.analytics.roles import (
    DEFAULT_MAX_NEGATIVE_FRACTION,
    ColumnRole,
    Confidence,
    SemanticColumnDetector,
    apply_confirmed_roles,
)


def _wide_orders(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """One row per order - the common analytics export shape."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "order_id": [f"T{i:05d}" for i in range(n)],
            "customer_id": rng.integers(1, 60, n),
            "order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D"),
            "quantity": rng.integers(1, 5, n),
            "revenue": np.round(rng.lognormal(3, 0.7, n), 2),
        }
    )


def _line_items(transactions: int = 200, seed: int = 1) -> pd.DataFrame:
    """One row per LINE ITEM - the shape basket analysis needs."""
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(transactions):
        for product in rng.choice(["bread", "milk", "eggs", "jam", "tea"], rng.integers(2, 4), replace=False):
            rows.append(
                {
                    "order_id": f"T{t:04d}",
                    "customer_id": int(rng.integers(1, 40)),
                    "product": product,
                    "quantity": int(rng.integers(1, 4)),
                    "revenue": float(np.round(rng.lognormal(2, 0.5), 2)),
                }
            )
    return pd.DataFrame(rows)


# ---- detection ----


def test_detects_every_role_on_a_wide_orders_table():
    detection = SemanticColumnDetector().detect(_wide_orders())
    assigned = {role: c.column for role, c in detection.assigned().items()}

    assert assigned[ColumnRole.ENTITY_ID] == "customer_id"
    assert assigned[ColumnRole.TRANSACTION_ID] == "order_id"
    assert assigned[ColumnRole.EVENT_DATE] == "order_date"
    assert assigned[ColumnRole.MONETARY] == "revenue"
    assert assigned[ColumnRole.QUANTITY] == "quantity"


def test_a_line_item_table_still_yields_a_transaction_id():
    """REGRESSION: an early version required a transaction id to be
    near-unique, which is only true of the WIDE shape. In a line-item table
    the id repeats once per basket line - so basket analysis would have been
    permanently inapplicable on exactly the data it exists for."""
    detection = SemanticColumnDetector().detect(_line_items())
    assigned = {role: c.column for role, c in detection.assigned().items()}

    assert assigned[ColumnRole.TRANSACTION_ID] == "order_id"
    assert assigned[ColumnRole.ITEM_ID] == "product"
    assert assigned[ColumnRole.ENTITY_ID] == "customer_id"


def test_an_integer_key_is_not_offered_as_a_quantity():
    """REGRESSION: `customer_id` holding 1..40 is whole, non-negative and
    small - it passes every numeric test a quantity does. Only the name
    separates them, and without that check an id was being offered as a
    measured quantity."""
    detection = SemanticColumnDetector().detect(_line_items())
    quantity = detection.best(ColumnRole.QUANTITY)

    assert quantity is not None
    assert quantity.column == "quantity"


def test_a_per_row_unique_id_is_never_an_entity():
    """The entity/transaction distinction is the whole point: a column with
    one value per row identifies rows, not repeating real-world things."""
    df = pd.DataFrame({"order_id": [f"T{i}" for i in range(100)], "revenue": np.arange(1.0, 101.0)})
    detection = SemanticColumnDetector().detect(df)

    assert detection.best(ColumnRole.ENTITY_ID) is None
    assert detection.best(ColumnRole.TRANSACTION_ID).column == "order_id"


def test_a_row_counter_is_not_an_entity_identifier():
    df = pd.DataFrame({"row_num": list(range(60)) * 2, "revenue": np.linspace(1, 100, 120)})
    detection = SemanticColumnDetector().detect(df)
    entity = detection.best(ColumnRole.ENTITY_ID)
    assert entity is None or entity.column != "row_num"


def test_a_mostly_negative_column_is_never_monetary():
    """Renamed from "a negative column is never monetary": the rule is now a
    FRACTION ceiling, so a few refunds no longer disqualify a price column.
    The assertion is unchanged - `balance_change` is ~50% negative, which is
    what this always actually tested and is still rejected."""
    df = pd.DataFrame({"customer": ["a", "b"] * 30, "balance_change": np.linspace(-50, 50, 60)})
    detection = SemanticColumnDetector().detect(df)

    assert detection.best(ColumnRole.MONETARY) is None
    rejected = detection.rejected(ColumnRole.MONETARY)
    assert any("negative" in r.reasons[0] for r in rejected)


def test_a_text_date_is_not_an_event_date():
    """Datetime typing is settled ONCE, upstream, by the shared coercion
    module. Re-deciding here is the per-module heuristic drift that
    app/column_kind.py exists to prevent."""
    df = pd.DataFrame({"customer": ["a", "b"] * 30, "when": ["not a date"] * 60})
    detection = SemanticColumnDetector().detect(df)
    assert detection.best(ColumnRole.EVENT_DATE) is None


def test_low_confidence_detections_are_surfaced_not_used():
    """The standing rule: stay silent rather than guess. A weak candidate is
    reported for a human to confirm, never consumed."""
    detection = SemanticColumnDetector().detect(_wide_orders())
    for candidate in detection.unconfirmed_candidates():
        assert candidate.confidence is Confidence.LOW
        assert detection.best(candidate.role) is None or detection.best(candidate.role).column != candidate.column


def test_detection_is_deterministic():
    df = _wide_orders()
    first = SemanticColumnDetector().detect(df).to_dict()
    second = SemanticColumnDetector().detect(df).to_dict()
    assert first == second


# ---- applicability ----


def test_applicability_lists_every_analysis_including_the_refusals():
    detection = SemanticColumnDetector().detect(_wide_orders())
    report = {r.analysis: r for r in build_applicability_report(detection)}

    assert set(report) == set(AnalysisKind), "every analysis must appear, applicable or not"
    assert report[AnalysisKind.ABC_PARETO].applicable
    assert report[AnalysisKind.RFM].applicable
    # No item id in a wide one-row-per-order table -> basket cannot run.
    assert not report[AnalysisKind.MARKET_BASKET].applicable


def test_a_refusal_names_the_precise_missing_requirement():
    """"Not applicable" with no reason is the failure mode this whole layer
    exists to prevent."""
    df = pd.DataFrame({"product": ["a", "b", "c"] * 20, "revenue": np.linspace(1, 100, 60)})
    report = {r.analysis: r for r in build_applicability_report(SemanticColumnDetector().detect(df))}

    cohort = report[AnalysisKind.COHORT_RETENTION]
    assert not cohort.applicable
    # `product` (3 distinct across 60 rows) legitimately fills the entity
    # slot, so the DATE is the only unmet requirement - and the refusal
    # names exactly that, rather than a generic "not applicable".
    assert cohort.missing == ["needs a date column; none detected"]
    assert cohort.resolved_columns["entity_id"] == "product"


def test_pareto_is_applicable_from_a_monetary_column_alone():
    """The simplest analysis has the loosest requirement on purpose - it is
    the pipeline proof, and must run on almost any business table."""
    df = pd.DataFrame({"revenue": np.linspace(1, 100, 60)})
    report = {r.analysis: r for r in build_applicability_report(SemanticColumnDetector().detect(df))}
    assert report[AnalysisKind.ABC_PARETO].applicable


# ---- ABC / Pareto, against hand-computable ground truth ----


def _bands(result):
    return {f.payload.band: f.payload for f in result.findings if f.finding_type is AnalysisFindingType.CONCENTRATION_BAND}


def test_pareto_ground_truth_on_a_planted_split():
    """Hand-computable: 2 entities of 200 (=400) then 8 of 12.5 (=100).
    Total 500. The first two account for exactly 80%."""
    df = pd.DataFrame(
        {
            "product": ["big1", "big2"] + [f"small{i}" for i in range(8)],
            "revenue": [200.0, 200.0] + [12.5] * 8,
        }
    )
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))

    assert result.ran
    bands = _bands(result)
    assert bands["A"].entity_count == 2
    assert bands["A"].value_total == pytest.approx(400.0)
    assert bands["A"].value_share == pytest.approx(0.8)
    assert bands["A"].entity_share == pytest.approx(0.2)
    assert set(bands["A"].top_entities) == {"big1", "big2"}
    # Shares over all bands must account for the whole.
    assert sum(b.value_share for b in bands.values()) == pytest.approx(1.0)
    assert sum(b.entity_count for b in bands.values()) == 10


def test_pareto_top_20_percent_share_matches_the_planted_split():
    df = pd.DataFrame(
        {
            "product": ["big1", "big2"] + [f"small{i}" for i in range(8)],
            "revenue": [200.0, 200.0] + [12.5] * 8,
        }
    )
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))
    curve = next(f for f in result.findings if f.finding_type is AnalysisFindingType.CONCENTRATION_CURVE)

    assert curve.payload.top_20_percent_value_share == pytest.approx(0.8)
    assert curve.payload.cumulative_value_share[-1] == pytest.approx(1.0)


def test_pareto_sums_values_per_entity_before_ranking():
    """Two rows for the same product are one entity contributing their sum,
    not two competing entities."""
    df = pd.DataFrame({"product": ["a", "a", "b"], "revenue": [30.0, 30.0, 40.0]})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))
    bands = _bands(result)

    assert sum(b.entity_count for b in bands.values()) == 2
    assert result.parameters["entity_count"] == 2
    assert result.parameters["total_value"] == pytest.approx(100.0)


def test_pareto_band_cutoffs_are_configurable_and_echoed():
    df = pd.DataFrame({"product": [f"p{i}" for i in range(10)], "revenue": [100.0] * 10})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df), band_cutoffs=(0.5, 0.9))

    assert result.parameters["band_cutoffs"] == [0.5, 0.9]
    bands = _bands(result)
    # Ten equal entities: half of them reach 50%.
    assert bands["A"].entity_count == 5


def test_pareto_rejects_incoherent_cutoffs():
    df = pd.DataFrame({"revenue": [1.0, 2.0]})
    with pytest.raises(ValueError):
        run_abc_pareto(df, SemanticColumnDetector().detect(df), band_cutoffs=(0.9, 0.8))


def test_pareto_refuses_without_a_monetary_column_and_says_why():
    df = pd.DataFrame({"product": ["a", "b", "c"] * 10, "note": ["x"] * 30})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))

    assert not result.ran
    assert result.findings == []
    assert "monetary" in result.not_run_reason


def test_pareto_refuses_on_an_all_zero_value_column_and_says_why():
    """An all-zero column is rejected as MONETARY before Pareto ever sees
    it ("no positive values"), which is the more precise statement of the
    two. Pareto keeps its own zero-total guard as defence in depth - it is
    unreachable through detection today, but a caller passing a confirmed
    role directly could still hit it."""
    df = pd.DataFrame({"product": ["a", "b"], "revenue": [0.0, 0.0]})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))

    assert not result.ran
    assert result.findings == []
    assert "monetary" in result.not_run_reason


def test_pareto_is_deterministic_including_ties():
    """Every entity identical - the ranking must still be stable, or the
    same data would produce different bands run to run."""
    df = pd.DataFrame({"product": [f"p{i}" for i in range(20)], "revenue": [5.0] * 20})
    detection = SemanticColumnDetector().detect(df)
    first = run_abc_pareto(df, detection).model_dump()
    second = run_abc_pareto(df, detection).model_dump()
    assert first == second


def test_pareto_curve_is_capped_and_says_when_it_downsampled():
    df = pd.DataFrame({"product": [f"p{i:05d}" for i in range(3000)], "revenue": np.linspace(1, 3000, 3000)})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))
    curve = next(f for f in result.findings if f.finding_type is AnalysisFindingType.CONCENTRATION_CURVE)

    assert len(curve.payload.entity_rank) <= 501
    assert curve.evidence.parameters["downsampled"] is True
    assert curve.payload.cumulative_value_share[-1] == pytest.approx(1.0)


# ---- the vocabulary ban, enforced structurally ----

_CAUSAL_WORDS = (
    "cause", "caused", "causes", "drive", "driven", "driver", "drives",
    "impact", "because", "effect", "influence", "leads_to", "responsible",
)


def test_no_causal_vocabulary_in_any_analytics_schema():
    """Same defence exploration uses: the Narrative Agent can only inherit
    vocabulary these schemas actually contain."""
    import app.analytics.findings as findings_module

    blob = json_schema_text(findings_module)
    for word in _CAUSAL_WORDS:
        assert word not in blob, f"causal vocabulary {word!r} present in the analytics schema"


def json_schema_text(module) -> str:
    import json

    from pydantic import BaseModel

    parts: list[str] = []
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            parts.append(json.dumps(obj.model_json_schema()))
    return " ".join(parts).lower()


def test_no_causal_vocabulary_in_a_real_result_payload():
    df = pd.DataFrame({"product": ["a", "b", "c"], "revenue": [50.0, 30.0, 20.0]})
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))
    blob = str(result.model_dump()).lower()
    for word in _CAUSAL_WORDS:
        assert word not in blob


def test_findings_envelope_round_trips():
    df = _wide_orders()
    detection = SemanticColumnDetector().detect(df)
    envelope = BusinessAnalyticsFindings(
        run_id="r1",
        detected_roles=detection.to_dict(),
        applicability=[a.to_dict() for a in build_applicability_report(detection)],
        results=[run_abc_pareto(df, detection)],
    )
    restored = BusinessAnalyticsFindings.model_validate(envelope.model_dump(mode="json"))
    assert len(restored.all_findings()) == len(envelope.all_findings())
    assert restored.schema_version == envelope.schema_version


def test_a_datetime_is_never_an_identifier():
    """REGRESSION, caught on the real ingested ecommerce table: `order_date`
    has many distinct values AND matches the "order" name hint, so it beat
    `order_id` to the transaction slot on a tie-break. Every analysis that
    groups by transaction would have silently grouped by timestamp."""
    detection = SemanticColumnDetector().detect(_wide_orders())

    assert detection.best(ColumnRole.TRANSACTION_ID).column == "order_id"
    assert detection.best(ColumnRole.ENTITY_ID).column == "customer_id"
    assert detection.best(ColumnRole.EVENT_DATE).column == "order_date"
    for role in (ColumnRole.ENTITY_ID, ColumnRole.TRANSACTION_ID, ColumnRole.ITEM_ID):
        best = detection.best(role)
        assert best is None or best.column != "order_date"


def test_revenue_outranks_cost_for_the_monetary_slot():
    """REGRESSION, caught on demo_full.csv: `revenue` and `cost` both match
    the monetary name hint, so a flat bonus left the choice to an
    alphabetical tie-break - `cost` won, and ABC/Pareto ranked products by
    what they consumed rather than by what they returned."""
    df = pd.DataFrame(
        {
            "product": [f"p{i}" for i in range(30)],
            "cost": np.linspace(5, 50, 30),
            "revenue": np.linspace(20, 300, 30),
        }
    )
    detection = SemanticColumnDetector().detect(df)

    assert detection.best(ColumnRole.MONETARY).column == "revenue"
    result = run_abc_pareto(df, detection)
    assert result.parameters["value_column"] == "revenue"


# ---- Human confirmation layered on top of detection ----


def test_a_confirmed_role_outranks_a_confidently_detected_one():
    """Precedence is an explicit rule, not a side effect of confirmed
    candidates scoring 1.0 - re-tuning an unrelated scorer must not be able
    to overturn a person's decision."""
    df = pd.DataFrame({"revenue": [10.0, 22.5, 31.0, 4.5] * 10, "cost": [1.0, 2.5, 3.0, 0.5] * 10})
    detected = SemanticColumnDetector().detect(df)
    assert detected.best(ColumnRole.MONETARY).column == "revenue"

    overridden = apply_confirmed_roles(detected, {"monetary": "cost"}, df.columns)

    assert overridden.best(ColumnRole.MONETARY).column == "cost"
    assert overridden.best(ColumnRole.MONETARY).confidence is Confidence.CONFIRMED


def test_confirmation_is_additive_so_refusals_can_still_be_explained():
    """Confirming one role must not erase what the detector saw about the
    others - the applicability report is generated from those candidates."""
    df = pd.DataFrame({"cust": [f"c{i % 5}" for i in range(20)], "units": [float(i % 4) for i in range(20)]})
    detected = SemanticColumnDetector().detect(df)
    before = len(detected.candidates)

    after = apply_confirmed_roles(detected, {"monetary": "units"}, df.columns)

    assert len(after.candidates) == before + 1
    assert after.rejected(ColumnRole.EVENT_DATE), "the reasons a date was ruled out must survive"


def test_a_confirmation_for_a_column_that_no_longer_exists_is_reported_not_raised():
    """A source can legitimately change shape between ingests. Failing the
    whole analytics pass over a stale answer would be worse than running
    without it and saying so."""
    df = pd.DataFrame({"cust": ["a", "b"], "units": [1.0, 2.0]})

    detection = apply_confirmed_roles(SemanticColumnDetector().detect(df), {"monetary": "old_total"}, df.columns)

    assert detection.best(ColumnRole.MONETARY) is None
    assert detection.stale_confirmations[0]["column"] == "old_total"
    assert "no column named" in detection.stale_confirmations[0]["why"]
    # Still reported as a stored confirmation, so the UI can offer to clear it.
    assert detection.confirmed_roles["monetary"] == "old_total"


def test_an_unknown_role_string_is_skipped_rather_than_crashing():
    df = pd.DataFrame({"cust": ["a", "b"], "units": [1.0, 2.0]})

    detection = apply_confirmed_roles(SemanticColumnDetector().detect(df), {"vibes": "units"}, df.columns)

    assert detection.stale_confirmations[0]["role"] == "vibes"


# ---- Monetary: a negative FRACTION ceiling, not a presence test ----


def test_a_column_with_0_0005_percent_negatives_qualifies_and_records_the_fraction():
    """Online Retail II's shape: 5 negatives in 1,067,371 rows, from bad-debt
    adjustments. An any-negative rule rejected `Price` - unambiguously the
    monetary column - and took four analyses down with it."""
    rng = np.random.default_rng(0)
    values = np.abs(rng.lognormal(2, 1, 200_000))
    values[0] = -500.0  # 1 in 200,000 = 0.0005%
    df = pd.DataFrame({"Price": values})

    detection = SemanticColumnDetector().detect(df)
    best = detection.best(ColumnRole.MONETARY)

    assert best is not None and best.column == "Price"
    assert best.details["negative_count"] == 1
    assert best.details["negative_fraction"] == pytest.approx(5e-06)
    # Recorded, not silent: the reason says it was accepted WITH returns.
    assert "negative value" in best.reasons[0]


def test_a_column_that_is_40_percent_negative_is_still_rejected():
    """The rule's real job: keeping profit/delta/change/variance columns out
    of the monetary slot. Those run 30-50% negative, an order of magnitude
    clear of transactional noise."""
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"profit_delta": rng.normal(0, 50, 10_000)})

    detection = SemanticColumnDetector().detect(df)

    assert detection.best(ColumnRole.MONETARY) is None
    rejected = [c for c in detection.rejected(ColumnRole.MONETARY) if c.column == "profit_delta"][0]
    assert "over the 5% ceiling" in rejected.reasons[0]
    # The measurement travels with the rejection too, not only the verdict.
    assert rejected.details["negative_fraction"] > 0.4


def test_the_negative_ceiling_is_configurable():
    df = pd.DataFrame({"amount": [10.0] * 90 + [-1.0] * 10})  # exactly 10% negative

    assert SemanticColumnDetector(max_negative_fraction=0.2).detect(df).best(ColumnRole.MONETARY) is not None
    assert SemanticColumnDetector(max_negative_fraction=0.05).detect(df).best(ColumnRole.MONETARY) is None


def test_the_ceiling_is_reported_alongside_the_measurement():
    """A fraction with no ceiling beside it is not something a reader can
    judge - they would have to know the rule to interpret the number."""
    df = pd.DataFrame({"amount": [10.0] * 99 + [-1.0]})

    best = SemanticColumnDetector().detect(df).best(ColumnRole.MONETARY)

    assert best.details["max_negative_fraction"] == DEFAULT_MAX_NEGATIVE_FRACTION
