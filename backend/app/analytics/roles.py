"""Semantic column roles - the capability-detection layer every business
analysis is gated on.

WHY THIS EXISTS AT ALL. Each analysis in this agent needs a specific data
shape (an entity identifier, a date, a monetary column). On arbitrary
ingested business data most of those shapes are simply absent, so an agent
that assumed them would either crash or - far worse - silently never fire
and become dead code nobody noticed. This project has already been bitten by
exactly that failure mode once, in datetime handling. So detection comes
FIRST, is scored rather than assumed, and its refusals are reported as
loudly as its successes (app/analytics/applicability.py).

NO LLM ANYWHERE IN THIS MODULE. Roles are decided from dtype, cardinality,
null rate, sign, skew and name shape - all computed deterministically from
the repaired frame and the run's own ExplorationFindings. The same input
always yields the same roles.

NO CAUSAL VOCABULARY. Role names describe what a column IS, never what it
does to something else.
"""

from __future__ import annotations

import re
from enum import Enum

import pandas as pd

from app.column_kind import ColumnKind, column_kind


class ColumnRole(str, Enum):
    """The closed set of roles an analysis may ask for."""

    ENTITY_ID = "entity_id"           # customer-like: repeats, never unique per row
    TRANSACTION_ID = "transaction_id"  # near-unique per row
    ITEM_ID = "item_id"               # repeats, and groups WITHIN a transaction
    EVENT_DATE = "event_date"         # datetime, already typed upstream
    MONETARY = "monetary"             # non-negative, positive-skewed numeric
    QUANTITY = "quantity"             # small positive integers


class Confidence(str, Enum):
    """Banded rather than a bare float so the rule is legible in output.

    CONFIRMED is never produced by detection - it exists only for a role a
    human has explicitly confirmed, so a downstream reader can always tell a
    machine's inference from a person's decision.
    """

    CONFIRMED = "confirmed"   # a human said so
    HIGH = "high"             # >= 0.75
    MEDIUM = "medium"         # >= 0.5
    LOW = "low"               # below 0.5 - a candidate, never used unasked


#: An analysis may only consume a role detected at or above this band.
#: Anything weaker is surfaced as a candidate for a human to confirm - the
#: project's standing rule is to stay silent rather than guess.
MINIMUM_USABLE_CONFIDENCE = Confidence.MEDIUM

_USABLE_BANDS = {Confidence.CONFIRMED, Confidence.HIGH, Confidence.MEDIUM}


def is_usable(confidence: Confidence) -> bool:
    return confidence in _USABLE_BANDS


def _band(score: float) -> Confidence:
    if score >= 0.75:
        return Confidence.HIGH
    if score >= 0.5:
        return Confidence.MEDIUM
    return Confidence.LOW


# Name shapes are EVIDENCE, never proof - they only ever adjust a score that
# the column's actual statistics already established. A column called
# "customer_id" holding 5000 unique values out of 5000 rows is not an entity
# identifier no matter what it is called.
_NAME_HINTS: dict[ColumnRole, re.Pattern[str]] = {
    ColumnRole.ENTITY_ID: re.compile(r"(customer|client|user|account|member|buyer|shopper|subscriber)", re.I),
    ColumnRole.TRANSACTION_ID: re.compile(r"(order|transaction|invoice|receipt|booking|ticket|txn)", re.I),
    ColumnRole.ITEM_ID: re.compile(r"(item|product|sku|article|part|title|book|name)", re.I),
    ColumnRole.EVENT_DATE: re.compile(r"(date|time|timestamp|day|created|ordered|purchased)", re.I),
    ColumnRole.MONETARY: re.compile(r"(revenue|sales|amount|price|value|total|cost|spend|payment|charge|fee)", re.I),
    ColumnRole.QUANTITY: re.compile(r"(quantity|qty|units|count|items|number|volume)", re.I),
}

#: Names that mark an id as belonging to the ROW rather than to a repeating
#: real-world thing. Used to keep a row counter out of the entity slot.
_ROW_INDEX_NAME = re.compile(r"^(row|line)?[_\- ]?(index|idx|num|number|no|seq|rank|rk|id)$|^row$|^line$", re.I)

#: Revenue-side vs cost-side. Both are monetary; only one is the measure a
#: business ranks by when asking "what accounts for most of the total".
_REVENUE_NAME = re.compile(r"(revenue|sales|turnover|gmv|income|amount|total|price|value|spend|payment)", re.I)
_COST_NAME = re.compile(r"(cost|expense|cogs|fee|charge|discount|refund|tax)", re.I)

#: Anything that reads as a key. An integer key satisfies every numeric test
#: a quantity does, so the name is the only thing that separates them.
_IDENTIFIER_NAME = re.compile(r"(^|_)(id|key|code|uuid|guid|number|no)$|^(id|key|code)$", re.I)


class RoleCandidate:
    """One column's fitness for one role, with the reasoning kept."""

    def __init__(self, column: str, role: ColumnRole, score: float, reasons: list[str]):
        self.column = column
        self.role = role
        self.score = round(score, 3)
        self.confidence = _band(score)
        self.reasons = reasons

    def to_dict(self) -> dict:
        return {
            "column": self.column,
            "role": self.role.value,
            "score": self.score,
            "confidence": self.confidence.value,
            "reasons": self.reasons,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RoleCandidate({self.column!r}, {self.role.value}, {self.score}, {self.confidence.value})"


def _name_bonus(column: str, role: ColumnRole) -> float:
    return 0.2 if _NAME_HINTS[role].search(column) else 0.0


def _looks_like_a_measure(series: pd.Series) -> bool:
    """A continuous numeric column is a MEASUREMENT, not a key.

    Without this, `revenue` (floats, repeating enough to look "repeated")
    was scoring as an entity id - and ABC/Pareto then grouped revenue BY
    revenue. Integer-valued numerics stay eligible, because a real key
    (`customer_id`) is usually an integer.
    """
    if column_kind(series) is not ColumnKind.NUMERIC:
        return False
    non_null = series.dropna()
    if non_null.empty:
        return False
    return not bool((non_null == non_null.round()).all())


def _score_entity_id(series: pd.Series, column: str, rows: int) -> tuple[float, list[str]]:
    """Repeats, but is not a single constant. A per-row unique id is a
    transaction, not an entity - that distinction is the whole point."""
    reasons: list[str] = []
    non_null = series.dropna()
    if non_null.empty:
        return 0.0, ["column is entirely null"]
    if column_kind(series) is ColumnKind.DATETIME:
        # A timestamp is an EVENT_DATE and nothing else. `order_date` has
        # many distinct values and matches the "order" name hint, so without
        # this it outranked `order_id` for the transaction slot on a
        # tie-break - a silent mis-assignment that would have quietly
        # corrupted every analysis grouping by transaction.
        return 0.0, ["datetime column - an event date, not an identifier"]
    if _looks_like_a_measure(series):
        return 0.0, ["continuous numeric column - a measurement, not an identifier"]
    uniques = non_null.nunique()
    ratio = uniques / max(len(non_null), 1)

    if uniques < 2:
        return 0.0, [f"only {uniques} distinct value(s) - cannot identify entities"]
    if ratio > 0.95:
        return 0.0, [f"{ratio:.0%} unique - reads as one row per value, not a repeating entity"]

    # Best when values repeat a few times each: enough entities to compare,
    # enough rows per entity to say anything about one.
    score = 0.0
    if 0.001 <= ratio <= 0.7:
        score = 0.6
        reasons.append(f"{uniques} distinct value(s) across {len(non_null)} row(s) ({ratio:.1%} unique) - repeats")
    else:
        score = 0.35
        reasons.append(f"{ratio:.1%} unique - weak repetition")

    if _ROW_INDEX_NAME.match(column):
        score -= 0.3
        reasons.append("name reads as a row counter rather than a real-world entity")

    bonus = _name_bonus(column, ColumnRole.ENTITY_ID)
    if bonus:
        reasons.append("name suggests a customer-like entity")
    return max(0.0, score + bonus), reasons


def _score_transaction_id(series: pd.Series, column: str, rows: int) -> tuple[float, list[str]]:
    """Two legitimate table shapes, and an early version of this only
    handled one of them:

      WIDE  - one row per order. The id is near-unique.
      LONG  - one row per LINE ITEM. The id repeats, once per item in the
              basket. This is the shape market-basket analysis needs, so
              rejecting it as "not unique enough" would have made basket
              analysis permanently inapplicable on exactly the data it is
              for.

    The long form is distinguished from an entity id by group size: a
    basket holds a handful of lines, whereas a customer accumulates orders
    without any comparable ceiling.
    """
    reasons: list[str] = []
    non_null = series.dropna()
    if non_null.empty:
        return 0.0, ["column is entirely null"]
    if column_kind(series) is ColumnKind.DATETIME:
        # A timestamp is an EVENT_DATE and nothing else. `order_date` has
        # many distinct values and matches the "order" name hint, so without
        # this it outranked `order_id` for the transaction slot on a
        # tie-break - a silent mis-assignment that would have quietly
        # corrupted every analysis grouping by transaction.
        return 0.0, ["datetime column - an event date, not an identifier"]
    if _looks_like_a_measure(series):
        return 0.0, ["continuous numeric column - a measurement, not an identifier"]
    uniques = non_null.nunique()
    ratio = uniques / max(len(non_null), 1)

    if ratio >= 0.9:
        reasons.append(f"{ratio:.1%} unique - one value per row (wide form)")
        score = 0.6
    else:
        mean_group = len(non_null) / max(uniques, 1)
        # A basket of 1.5-50 lines. Below that it is effectively unique;
        # above it the groups are too big to be single transactions.
        if not (1.2 <= mean_group <= 50):
            return 0.0, [
                f"{ratio:.0%} unique, averaging {mean_group:.1f} row(s) per value - "
                "neither one row per transaction nor a plausible basket size"
            ]
        if uniques < 5:
            return 0.0, [f"only {uniques} distinct value(s) - too few to be transactions"]
        reasons.append(
            f"{uniques} distinct value(s) averaging {mean_group:.1f} row(s) each - reads as line items per transaction"
        )
        score = 0.45

    if _ROW_INDEX_NAME.match(column):
        score -= 0.3
        reasons.append("name reads as a row counter")
    bonus = _name_bonus(column, ColumnRole.TRANSACTION_ID)
    if bonus:
        reasons.append("name suggests a transaction/order")
    return max(0.0, score + bonus), reasons


def _score_event_date(series: pd.Series, column: str, rows: int) -> tuple[float, list[str]]:
    """Datetime dtype only. app/datetime_coercion.py already decided, once,
    at fetch time what is a date; re-deciding here is exactly the kind of
    per-module heuristic app/column_kind.py exists to prevent."""
    if column_kind(series) is not ColumnKind.DATETIME:
        return 0.0, ["not a datetime column"]
    non_null = series.dropna()
    if non_null.empty:
        return 0.0, ["column is entirely null"]
    distinct = non_null.nunique()
    if distinct < 2:
        return 0.0, [f"only {distinct} distinct timestamp(s) - no time span to analyse"]
    span = non_null.max() - non_null.min()
    reasons = [f"datetime column spanning {span.days} day(s) across {distinct} distinct value(s)"]
    score = 0.75 if span.days >= 1 else 0.5
    if span.days < 1:
        reasons.append("span under a day - too short for period-based analysis")
    return min(1.0, score + _name_bonus(column, ColumnRole.EVENT_DATE)), reasons


def _score_monetary(series: pd.Series, column: str, rows: int) -> tuple[float, list[str]]:
    if column_kind(series) is not ColumnKind.NUMERIC:
        return 0.0, ["not a numeric column"]
    non_null = series.dropna()
    if non_null.empty:
        return 0.0, ["column is entirely null"]
    if bool((non_null < 0).any()):
        return 0.0, ["contains negative values - not a monetary magnitude"]
    if float(non_null.max()) <= 0:
        return 0.0, ["no positive values"]

    reasons = ["non-negative numeric"]
    score = 0.4
    named_monetary = bool(_NAME_HINTS[ColumnRole.MONETARY].search(column))
    # Integer-valued and small reads as a count, not money - UNLESS the name
    # says otherwise. `revenue` holding 30, 30, 40 is money that happens to
    # be round; demoting it below the usable floor made ABC/Pareto refuse to
    # run on perfectly good data.
    looks_like_count = (
        not named_monetary
        and bool((non_null == non_null.round()).all())
        and float(non_null.max()) <= 100
    )
    if looks_like_count:
        score -= 0.2
        reasons.append("small whole numbers - reads as a count rather than a monetary value")
    else:
        skew = float(non_null.skew()) if len(non_null) > 2 else 0.0
        if skew > 0.2:
            score += 0.15
            reasons.append(f"positively skewed (skew {skew:.2f}) as monetary values usually are")
    # Tiered, not flat. `revenue` and `cost` BOTH match the monetary hint,
    # so a flat bonus left the choice to an alphabetical tie-break - which
    # picked `cost` on the demo table and had ABC/Pareto ranking products by
    # what they consumed rather than by what they returned. Revenue-shaped
    # names are the outcome; cost-shaped ones are an input to it.
    if _REVENUE_NAME.search(column):
        bonus = 0.3
        reasons.append("name suggests a revenue-side measure")
    elif _COST_NAME.search(column):
        bonus = 0.1
        reasons.append("name suggests a cost-side measure")
    elif _NAME_HINTS[ColumnRole.MONETARY].search(column):
        bonus = 0.2
        reasons.append("name suggests a monetary measure")
    else:
        bonus = 0.0
    return max(0.0, score + bonus), reasons


def _score_quantity(series: pd.Series, column: str, rows: int) -> tuple[float, list[str]]:
    if column_kind(series) is not ColumnKind.NUMERIC:
        return 0.0, ["not a numeric column"]
    non_null = series.dropna()
    if non_null.empty:
        return 0.0, ["column is entirely null"]
    if not bool((non_null == non_null.round()).all()):
        return 0.0, ["not whole numbers"]
    if bool((non_null < 0).any()):
        return 0.0, ["contains negative values"]
    peak = float(non_null.max())
    if peak > 1000:
        return 0.0, [f"maximum {peak:.0f} is too large to read as a per-line quantity"]

    # An integer identifier passes every numeric test a quantity does -
    # `customer_id` holding 1..40 is whole, non-negative and small. What
    # separates them is that a quantity takes FEW distinct values and is not
    # named like a key. Without this, an id was being offered as a quantity.
    if _IDENTIFIER_NAME.search(column):
        return 0.0, ["name reads as an identifier, not a measured quantity"]
    if _NAME_HINTS[ColumnRole.MONETARY].search(column):
        return 0.0, ["name reads as a monetary measure, not a per-line quantity"]
    distinct = int(non_null.nunique())
    if distinct > 50:
        return 0.0, [f"{distinct} distinct values - too many to read as a per-line quantity"]

    reasons = [f"whole numbers in [0, {peak:.0f}] across {distinct} distinct value(s)"]
    score = 0.5
    return min(1.0, score + _name_bonus(column, ColumnRole.QUANTITY)), reasons


_SCORERS = {
    ColumnRole.ENTITY_ID: _score_entity_id,
    ColumnRole.TRANSACTION_ID: _score_transaction_id,
    ColumnRole.EVENT_DATE: _score_event_date,
    ColumnRole.MONETARY: _score_monetary,
    ColumnRole.QUANTITY: _score_quantity,
}


class SemanticColumnDetector:
    """Deterministically scores every column for every role.

    Returns ALL candidates, including rejected ones with their reasons - the
    applicability report needs to be able to say precisely why nothing filled
    a slot, and "no column scored above the floor" is a different statement
    from "no column was even considered".
    """

    def __init__(self, minimum_usable: Confidence = MINIMUM_USABLE_CONFIDENCE):
        self.minimum_usable = minimum_usable

    def detect(self, df: pd.DataFrame) -> "RoleDetection":
        rows = len(df)
        candidates: list[RoleCandidate] = []

        for column in df.columns:
            series = df[column]
            for role, scorer in _SCORERS.items():
                score, reasons = scorer(series, str(column), rows)
                # Zero-score candidates are RETAINED. They carry the reason a
                # column was ruled out, and the applicability report needs
                # exactly that to say "closest candidate: `x` - rejected
                # because ...". They can never be used: _best() filters on
                # the confidence band, not on presence in this list.
                candidates.append(RoleCandidate(str(column), role, score, reasons))

        # ITEM_ID is the one role that cannot be judged from a column alone:
        # it is defined by grouping WITHIN a transaction, so it needs the
        # transaction column to already be known.
        best_txn = _best(candidates, ColumnRole.TRANSACTION_ID, self.minimum_usable)
        if best_txn is not None:
            for column in df.columns:
                if str(column) == best_txn.column:
                    continue
                score, reasons = _score_item_id(df, str(column), best_txn.column)
                candidates.append(RoleCandidate(str(column), ColumnRole.ITEM_ID, score, reasons))

        return RoleDetection(candidates=candidates, minimum_usable=self.minimum_usable, row_count=rows)


def _score_item_id(df: pd.DataFrame, column: str, transaction_column: str) -> tuple[float, list[str]]:
    """An item repeats ACROSS transactions and appears alongside other items
    WITHIN one. A column that is unique per transaction is a property of the
    transaction, not a line item in it."""
    series = df[column].dropna()
    if series.empty:
        return 0.0, ["column is entirely null"]
    if column_kind(df[column]) is ColumnKind.DATETIME:
        return 0.0, ["datetime column"]

    uniques = series.nunique()
    if uniques < 2:
        return 0.0, [f"only {uniques} distinct value(s)"]

    per_txn = df.groupby(transaction_column, observed=True)[column].nunique()
    mean_items = float(per_txn.mean()) if len(per_txn) else 0.0
    if mean_items <= 1.0:
        return 0.0, [
            f"exactly {mean_items:.2f} distinct value(s) per {transaction_column} - "
            "a property of the transaction, not a line item within it"
        ]

    reasons = [f"averages {mean_items:.2f} distinct value(s) per {transaction_column} - groups within a transaction"]
    score = 0.6
    return min(1.0, score + _name_bonus(column, ColumnRole.ITEM_ID)), reasons


def _best(candidates: list[RoleCandidate], role: ColumnRole, minimum: Confidence) -> RoleCandidate | None:
    usable = [c for c in candidates if c.role is role and is_usable(c.confidence)]
    if not usable:
        return None
    # Deterministic: highest score, ties broken by column name.
    return sorted(usable, key=lambda c: (-c.score, c.column))[0]


class RoleDetection:
    """The detector's full output: what filled each role, what didn't, why."""

    def __init__(self, candidates: list[RoleCandidate], minimum_usable: Confidence, row_count: int):
        self.candidates = candidates
        self.minimum_usable = minimum_usable
        self.row_count = row_count

    def best(self, role: ColumnRole) -> RoleCandidate | None:
        """The column assigned to a role, or None if nothing cleared the
        floor. Never returns a LOW-confidence guess."""
        return _best(self.candidates, role, self.minimum_usable)

    def rejected(self, role: ColumnRole) -> list[RoleCandidate]:
        """Scored but below the floor - the "did you mean" list."""
        return sorted(
            [c for c in self.candidates if c.role is role and not is_usable(c.confidence)],
            key=lambda c: (-c.score, c.column),
        )

    def assigned(self) -> dict[ColumnRole, RoleCandidate]:
        out: dict[ColumnRole, RoleCandidate] = {}
        for role in ColumnRole:
            found = self.best(role)
            if found is not None:
                out[role] = found
        return out

    def unconfirmed_candidates(self) -> list[RoleCandidate]:
        """Everything a human might want to confirm - scored, but not strong
        enough to use unasked."""
        return sorted(
            [c for c in self.candidates if not is_usable(c.confidence)],
            key=lambda c: (-c.score, c.role.value, c.column),
        )

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "minimum_usable_confidence": self.minimum_usable.value,
            "assigned": {role.value: c.to_dict() for role, c in self.assigned().items()},
            "unconfirmed_candidates": [c.to_dict() for c in self.unconfirmed_candidates()],
        }


def confirmed_detection(row_count: int, **roles: str) -> RoleDetection:
    """Build a RoleDetection from roles a HUMAN has confirmed.

    The brief requires low-confidence detections to be surfaced "as
    candidates for the user to confirm", which only means something if the
    system can then accept that confirmation - this is that path. Confirmed
    roles carry Confidence.CONFIRMED, which detection itself never produces,
    so a downstream reader can always tell a person's decision from a
    machine's inference.

    Also what lets an analysis be unit-tested on a fixture too small for
    detection to fire on: the analysis logic and the detector's tuning stay
    independently testable.

        confirmed_detection(120, entity_id="customer", event_date="ts",
                            monetary="revenue")
    """
    candidates: list[RoleCandidate] = []
    for role_name, column in roles.items():
        role = ColumnRole(role_name)
        candidate = RoleCandidate(column, role, score=1.0, reasons=["confirmed by a human"])
        candidate.confidence = Confidence.CONFIRMED
        candidates.append(candidate)
    return RoleDetection(candidates=candidates, minimum_usable=MINIMUM_USABLE_CONFIDENCE, row_count=row_count)
