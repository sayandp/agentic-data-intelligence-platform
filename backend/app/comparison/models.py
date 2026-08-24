"""The vocabulary of a comparison.

Three ideas, kept as separate types on purpose:

  Delta          two comparable numbers, and the change between them
  Membership     a thing present in one run and not the other
  Incomparable   a section that CANNOT be compared, and the reason why

The third is why this module exists as types rather than as dicts. A
comparison that quietly renders 0 where one side has no value, or shows a
delta between numbers computed on different bases, is worse than showing
nothing: it invents a change that did not happen, in a view whose entire
purpose is to report change. `Incomparable` makes that state representable and
therefore reportable, instead of leaving it to be encoded as a missing key
somebody downstream forgets to check.

NO CAUSAL LANGUAGE. A Delta reports that a number moved and by how much. It
never says why, and nothing in this package produces a word like "because",
"caused", "due to", "drove" or "led to". A comparison of two runs cannot
support a causal claim - the runs differ in every uncontrolled way at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Comparability(str, Enum):
    """Whether a section can be compared at all."""

    COMPARABLE = "comparable"
    #: Present in one run only - reported as membership, never as a delta.
    ONE_SIDED = "one_sided"
    #: Present in both, but the two are not measuring the same thing.
    NOT_COMPARABLE = "not_comparable"
    #: Neither run has it. Said explicitly rather than omitted.
    ABSENT = "absent"


@dataclass(frozen=True)
class Delta:
    """A number in run A and the same number in run B.

    `relative` is None when `before` is zero. A relative change from zero is
    undefined, and rendering it as 0%, as infinity, or as "new" would each be
    a different wrong answer - the honest output is the absolute change and no
    ratio.
    """

    label: str
    before: float | None
    after: float | None
    unit: str | None = None
    #: What this number is computed from, when that matters for reading it.
    basis: str | None = None

    @property
    def absolute(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    @property
    def relative(self) -> float | None:
        if self.before is None or self.after is None or self.before == 0:
            return None
        return (self.after - self.before) / abs(self.before)

    @property
    def changed(self) -> bool:
        return self.absolute not in (None, 0)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "before": self.before,
            "after": self.after,
            "absolute": self.absolute,
            "relative": self.relative,
            "unit": self.unit,
            "basis": self.basis,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class Membership:
    """Something one run has and the other does not.

    Deliberately NOT a Delta with a None side. A correlation that appeared is
    a different fact from a correlation whose strength moved, and flattening
    the two into one shape is how "appeared" ends up rendered as a change from
    zero.
    """

    label: str
    side: str  # "a_only" | "b_only"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"label": self.label, "side": self.side, "detail": self.detail}


@dataclass
class SectionComparison:
    """One comparable area: schema, data quality, exploration, analytics,
    marketing, model."""

    name: str
    comparability: Comparability
    #: Why, in words, whenever comparability is not COMPARABLE. Never blank in
    #: that case - "not comparable" without a reason is an error message
    #: pretending to be an answer.
    reason: str = ""
    deltas: list[Delta] = field(default_factory=list)
    memberships: list[Membership] = field(default_factory=list)
    #: Free-form facts that are neither a delta nor a membership - each run's
    #: value basis, the grain an analysis used, and so on.
    notes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.comparability is not Comparability.COMPARABLE and not self.reason:
            raise ValueError(f"section {self.name!r} is {self.comparability.value} but gives no reason")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "comparability": self.comparability.value,
            "reason": self.reason,
            "deltas": [d.to_dict() for d in self.deltas],
            "memberships": [m.to_dict() for m in self.memberships],
            "notes": self.notes,
        }


@dataclass
class RunComparison:
    """Two runs of one source, side by side."""

    run_a: dict
    run_b: dict
    sections: list[SectionComparison] = field(default_factory=list)
    #: Set when the two runs cannot be compared at all - different sources,
    #: or a run that never completed. Sections are empty in that case rather
    #: than half-populated.
    blocked_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "comparable": self.blocked_reason is None,
            "blocked_reason": self.blocked_reason,
            "sections": [s.to_dict() for s in self.sections],
        }
