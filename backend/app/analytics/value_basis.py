"""What "value" means for this table - decided ONCE, reported always.

The monetary role names a numeric column. It does not say whether that
column holds a LINE TOTAL or a UNIT PRICE, and the difference decides
whether summing it means anything.

On Online Retail II the monetary column is `Price`: the price of one unit.
Summing it ranked products by how expensive one is, which is not a business
quantity at all - one item priced at 500 outranked an item priced at 2 that
sold 10,000 units.
The line total (`Price * Quantity`) is the figure every one of these
analyses was actually reaching for.

THE ASYMMETRY THAT DRIVES THE DESIGN. Two errors are available and they are
not equally bad:

  - Summing a unit price when a quantity exists understates volume sellers.
    Wrong, and recoverable once seen.
  - Multiplying a column that is ALREADY a line total by quantity inflates
    every figure by the quantity, silently, and the result still looks
    plausible. Far worse.

So derivation is opt-in on POSITIVE evidence that the column is unit-price-
shaped, never on the absence of evidence that it is a total. An unrecognised
name is used as-is.

NO LLM. Names and the presence of a quantity role decide this, deterministically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from app.analytics.roles import _NAME_HINTS, ColumnRole, RoleDetection

#: Reused from the detector so the two cannot disagree about what a
#: quantity-shaped NAME looks like.
QUANTITY_NAME_HINT = _NAME_HINTS[ColumnRole.QUANTITY]

#: Names that say "this is already a line total". Used as-is, never
#: multiplied. Deliberately generous: a false match here costs the
#: understating error, a false miss costs the inflating one.
_TOTAL_SHAPED = re.compile(
    r"(^|_)(total|totals|amount|amt|revenue|sales|turnover|gmv|subtotal|"
    r"linetotal|line_total|net|gross|spend|value|income|charge|paid|payment)($|_)",
    re.I,
)

#: Names that say "this is the price of ONE unit". Only these are eligible
#: for derivation.
_UNIT_PRICE_SHAPED = re.compile(
    r"(^|_)(price|unitprice|unit_price|rate|tariff|fare|each|per_unit|unitcost|unit_cost)($|_)",
    re.I,
)


@dataclass(frozen=True)
class ValueBasis:
    """The series every value-summing analysis must use, and its provenance.

    `label` and `note` exist so a reader can always tell WHICH quantity a
    number describes. A revenue figure and a unit-price figure differ by
    orders of magnitude and look equally reasonable in isolation.
    """

    monetary_column: str
    quantity_column: str | None
    derived: bool
    label: str
    note: str

    def series(self, df: pd.DataFrame) -> pd.Series:
        """The per-row value. Multiplication happens HERE and nowhere else,
        so no analysis can accidentally use a different definition."""
        values = pd.to_numeric(df[self.monetary_column], errors="coerce")
        if not self.derived or self.quantity_column is None:
            return values
        quantity = pd.to_numeric(df[self.quantity_column], errors="coerce")
        return values * quantity

    def to_dict(self) -> dict:
        return {
            "monetary_column": self.monetary_column,
            "quantity_column": self.quantity_column,
            "derived": self.derived,
            "label": self.label,
            "note": self.note,
        }


def monetary_shape(column: str) -> str:
    """"total", "unit_price", or "unknown" - from the name alone.

    Checked total-first: a column called `total_price` is a total that
    happens to contain the word price, and reading it the other way is the
    inflating error.
    """
    if _TOTAL_SHAPED.search(column):
        return "total"
    if _UNIT_PRICE_SHAPED.search(column):
        return "unit_price"
    return "unknown"


def resolve_value_basis(detection: RoleDetection) -> ValueBasis | None:
    """Decide what "value" means for this table. None when no monetary role
    was detected at all - the caller already refuses in that case."""
    monetary = detection.best(ColumnRole.MONETARY)
    if monetary is None:
        return None

    quantity = detection.best(ColumnRole.QUANTITY)
    shape = monetary_shape(monetary.column)

    # Derivation needs BOTH a quantity to multiply by and positive evidence
    # that this column is a unit price. Either missing means as-is.
    if quantity is not None and shape == "unit_price":
        return ValueBasis(
            monetary_column=monetary.column,
            quantity_column=quantity.column,
            derived=True,
            label=f"{monetary.column} x {quantity.column}",
            note=(
                f"Value computed as `{monetary.column}` x `{quantity.column}`. "
                f"`{monetary.column}` reads as a unit price, so summing it alone would rank by how "
                "expensive one unit is rather than by how much value changed hands."
            ),
        )

    if shape == "total":
        why = f"`{monetary.column}` reads as a line total already"
        if quantity is not None:
            why += f", so it is NOT multiplied by `{quantity.column}`"
    elif quantity is None:
        why = "no quantity column was detected, so there is nothing to multiply by"
    else:
        why = (
            f"`{monetary.column}` does not read as a unit price, and multiplying a column that is "
            "already a total would inflate every figure here"
        )

    return ValueBasis(
        monetary_column=monetary.column,
        quantity_column=None,
        derived=False,
        label=monetary.column,
        note=f"Value taken directly from `{monetary.column}` - {why}.",
    )


def quantity_needs_confirmation(detection: RoleDetection) -> dict | None:
    """True case: the monetary column reads as a UNIT PRICE, so a line total
    is what these analyses want - but no quantity column cleared the
    confidence floor.

    Rather than guess a multiplier (which would silently change every figure
    on the page), this reports the gap so the confirm-a-role flow can ask.
    Returns None when there is nothing to ask about: no monetary role, a
    quantity already assigned, or a monetary column that is a total anyway.
    """
    monetary = detection.best(ColumnRole.MONETARY)
    if monetary is None or detection.best(ColumnRole.QUANTITY) is not None:
        return None
    if monetary_shape(monetary.column) != "unit_price":
        return None

    # Zero-score rejections are KEPT - they carry the reason a column was
    # ruled out ("80995 is too large to read as a per-line quantity"), which
    # is exactly what a person needs in order to overrule it.
    #
    # But a column that is not NUMERIC can never be a multiplier, so
    # offering one is not a suggestion, it is noise. Unfiltered, this
    # proposed `Country` and `Description` as quantities on Online Retail II
    # while the actual `Quantity` column never made the list.
    rejected = [
        c
        for c in detection.rejected(ColumnRole.QUANTITY)
        if not any("not a numeric column" in reason for reason in c.reasons)
    ]
    # Every rejection scores 0, so the underlying sort falls through to
    # alphabetical and buried `Quantity` behind `Customer ID`. A column
    # NAMED like the role is the likeliest answer and belongs first - the
    # name is only a tiebreak here, never evidence that overrides the
    # statistics.
    rejected.sort(key=lambda c: (0 if QUANTITY_NAME_HINT.search(c.column) else 1, -c.score, c.column))
    candidates = rejected[:3]
    return {
        "role": "quantity",
        "reason": (
            f"`{monetary.column}` reads as a unit price, so these analyses would normally rank by "
            f"`{monetary.column}` x quantity. No quantity column was detected, so value is being "
            f"summed from `{monetary.column}` alone."
        ),
        "candidates": [c.to_dict() for c in candidates],
    }
