"""What "value" means, and the asymmetry that decides it.

The monetary role names a numeric column but does not say whether it holds
a LINE TOTAL or a UNIT PRICE. On Online Retail II it is `Price` - the price
of one unit - so summing it ranked products by how expensive one is rather
than by revenue.

The two available errors are not equally bad, and these tests encode that:
summing a unit price understates volume sellers (recoverable once seen);
multiplying a column that is ALREADY a total inflates every figure silently
and still looks plausible. So derivation requires POSITIVE evidence of a
unit price, never the absence of evidence of a total.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics.engine import run_business_analytics
from app.analytics.findings import AnalysisFindingType
from app.analytics.pareto import run_abc_pareto
from app.analytics.rfm import run_rfm
from app.analytics.roles import ColumnRole, SemanticColumnDetector, confirmed_detection
from app.analytics.value_basis import monetary_shape, quantity_needs_confirmation, resolve_value_basis

BASE = pd.Timestamp("2024-01-01")


def _volume_vs_luxury(price_column: str = "Price") -> pd.DataFrame:
    """`cheap` moves 50 units a row over 200 rows at 2.00 (20,000 of value);
    `lux` sells 5 units at 500.00 (2,500). By unit price alone `lux` wins;
    by revenue `cheap` wins by 8x. Which one Pareto names is the question."""
    rows = [("cheap", 2.0, 50)] * 200 + [("lux", 500.0, 1)] * 5
    return pd.DataFrame(rows, columns=["Description", price_column, "Quantity"])


# ---- classification ----


@pytest.mark.parametrize(
    "column,expected",
    [
        ("Price", "unit_price"),
        ("unit_price", "unit_price"),
        ("UnitPrice", "unit_price"),
        ("rate", "unit_price"),
        ("unit_cost", "unit_price"),
        ("total", "total"),
        ("Amount", "total"),
        ("revenue", "total"),
        ("line_total", "total"),
        ("net_amount", "total"),
        ("Sales", "total"),
        # Checked total-first ON PURPOSE: `total_price` is a total that
        # happens to contain "price", and reading it the other way round is
        # the inflating error.
        ("total_price", "total"),
        # Neither shape -> used as-is. A bare `cost` could be a unit cost or
        # a total cost, and guessing wrong is the expensive direction.
        ("cost", "unknown"),
        ("widgets_moved", "unknown"),
    ],
)
def test_monetary_shape_classification(column, expected):
    assert monetary_shape(column) == expected


# ---- derivation ----


def test_a_unit_price_with_a_quantity_is_multiplied():
    detection = SemanticColumnDetector().detect(_volume_vs_luxury())
    basis = resolve_value_basis(detection)

    assert basis.derived is True
    assert basis.monetary_column == "Price"
    assert basis.quantity_column == "Quantity"
    assert basis.label == "Price x Quantity"


def test_a_total_shaped_column_is_never_multiplied_even_with_a_quantity():
    """The error this guards is the worse of the two: inflating every figure
    by the quantity, silently, with a plausible-looking result."""
    df = _volume_vs_luxury(price_column="line_total")
    basis = resolve_value_basis(SemanticColumnDetector().detect(df))

    assert basis.derived is False
    assert basis.quantity_column is None
    assert "NOT multiplied" in basis.note


def test_an_unrecognised_name_is_used_as_is_rather_than_guessed():
    """Absence of evidence that a column is a total is not evidence that it
    is a unit price."""
    df = pd.DataFrame(
        {"thing": ["a", "b"] * 20, "widgets_moved": np.linspace(1.5, 40.5, 40), "Quantity": [2] * 40}
    )
    basis = resolve_value_basis(SemanticColumnDetector().detect(df))

    assert basis is None or basis.derived is False


def test_no_quantity_column_means_the_monetary_column_is_used_as_is():
    df = pd.DataFrame({"Description": [f"p{i % 5}" for i in range(40)], "Price": np.linspace(1, 50, 40)})
    basis = resolve_value_basis(SemanticColumnDetector().detect(df))

    assert basis.derived is False
    assert "no quantity column was detected" in basis.note


def test_the_derived_series_is_the_product_row_by_row():
    df = pd.DataFrame({"Description": ["a", "b"], "Price": [2.0, 500.0], "Quantity": [50, 1]})
    basis = resolve_value_basis(
        confirmed_detection(2, item_id="Description", monetary="Price", quantity="Quantity")
    )

    assert list(basis.series(df)) == [100.0, 500.0]


# ---- what it changes downstream ----


def test_pareto_ranks_by_revenue_not_by_how_expensive_one_unit_is():
    """THE defect. By unit price `lux` (500.00) outranks `cheap` (2.00);
    by revenue `cheap` contributes 8x more."""
    df = _volume_vs_luxury()
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))

    bands = {
        entity: f.payload.band
        for f in result.findings
        if f.finding_type is AnalysisFindingType.CONCENTRATION_BAND
        for entity in f.payload.top_entities
    }
    assert bands["cheap"] == "A"
    assert bands["lux"] != "A"
    assert result.parameters["value_definition"]["derived"] is True


def test_pareto_would_rank_the_other_way_on_the_unit_price_alone():
    """Pins the CONTRAST rather than only the fixed behaviour: rename the
    column to a total-shaped name and the same numbers rank `lux` top,
    which is exactly the wrong answer this change exists to prevent."""
    df = _volume_vs_luxury(price_column="line_total")
    result = run_abc_pareto(df, SemanticColumnDetector().detect(df))

    band_a = [
        entity
        for f in result.findings
        if f.finding_type is AnalysisFindingType.CONCENTRATION_BAND and f.payload.band == "A"
        for entity in f.payload.top_entities
    ]
    # 200 rows of 2.00 still sums to 400 vs lux's 2,500 when NOT multiplied.
    assert "lux" in band_a
    assert result.parameters["value_definition"]["derived"] is False


def test_rfm_monetary_ranks_by_what_an_entity_spent():
    """`bulk` spends 1,000 over 10 orders of 100; `single` spends 300 on one
    item priced 300. Ranking by unit price alone puts `single` on top."""
    rows = [("bulk", BASE + pd.Timedelta(days=i), 100.0, 1) for i in range(10)]
    rows.append(("single", BASE + pd.Timedelta(days=3), 300.0, 1))
    df = pd.DataFrame(rows, columns=["Customer", "InvoiceDate", "Price", "Quantity"])
    detection = confirmed_detection(
        len(df), entity_id="Customer", event_date="InvoiceDate", monetary="Price", quantity="Quantity"
    )

    result = run_rfm(df, detection)

    assert result.parameters["value_definition"]["derived"] is True
    assert result.parameters["value_column"] == "Price x Quantity"


def test_every_analysis_reports_which_quantity_it_summed():
    """A revenue total and a unit-price total look equally plausible alone,
    so which one is on screen can never be left implicit."""
    findings = run_business_analytics("run-basis", _volume_vs_luxury())

    assert findings.value_definition["derived"] is True
    assert findings.value_definition["label"] == "Price x Quantity"
    assert "unit price" in findings.value_definition["note"]

    pareto = [r for r in findings.results if r.analysis == "abc_pareto"][0]
    assert pareto.parameters["value_definition"]["label"] == "Price x Quantity"


def test_the_note_says_plainly_which_basis_was_used():
    derived = resolve_value_basis(SemanticColumnDetector().detect(_volume_vs_luxury()))
    direct = resolve_value_basis(SemanticColumnDetector().detect(_volume_vs_luxury("line_total")))

    assert "Value computed as" in derived.note
    assert "Value taken directly from" in direct.note


# ---- the gap goes to a human, never a guess ----


def test_a_unit_price_with_no_quantity_is_surfaced_for_confirmation():
    """The analyses still run - on the unit price - but the gap is a
    question, not something to paper over with a guessed multiplier."""
    df = pd.DataFrame({"Description": [f"p{i % 5}" for i in range(40)], "Price": np.linspace(1, 50, 40)})
    detection = SemanticColumnDetector().detect(df)
    assert detection.best(ColumnRole.QUANTITY) is None, "fixture must have no usable quantity"

    gap = quantity_needs_confirmation(detection)

    assert gap is not None
    assert gap["role"] == "quantity"
    assert "unit price" in gap["reason"]


def test_no_confirmation_is_asked_for_when_the_column_is_already_a_total():
    df = pd.DataFrame({"Description": [f"p{i % 5}" for i in range(40)], "revenue": np.linspace(1, 50, 40)})

    assert quantity_needs_confirmation(SemanticColumnDetector().detect(df)) is None


def test_no_confirmation_is_asked_for_when_a_quantity_was_detected():
    assert quantity_needs_confirmation(SemanticColumnDetector().detect(_volume_vs_luxury())) is None


def test_confirming_a_quantity_switches_the_value_definition_to_derived():
    """The confirm flow's payoff: answering the question changes what every
    summed figure below means, which is why it is asked at all."""
    # Wholesale pack sizes: whole and positive, but far too large for the
    # detector to call a per-line quantity on its own. Exactly the case
    # where a human knows something the statistics cannot.
    df = pd.DataFrame(
        {
            "Description": [f"p{i % 5}" for i in range(40)],
            "Price": np.linspace(1, 50, 40),
            "units_shifted": np.arange(1100, 1140),
        }
    )
    assert SemanticColumnDetector().detect(df).best(ColumnRole.QUANTITY) is None

    before = run_business_analytics("r", df)
    after = run_business_analytics("r", df, {"quantity": "units_shifted"})

    assert before.value_definition["derived"] is False
    assert before.quantity_confirmation is not None
    assert after.value_definition["derived"] is True
    assert after.value_definition["label"] == "Price x units_shifted"
    assert after.quantity_confirmation is None
