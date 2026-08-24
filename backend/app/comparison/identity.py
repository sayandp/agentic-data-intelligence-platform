"""How a finding in run A is recognised as "the same finding" in run B.

FINDING IDS ARE POSITIONAL AND MUST NOT BE USED HERE. An exploration finding's
id is `f"{finding_type}-{position}"`, assigned from its offset in the run's
final list (app/exploration/findings.py). `correlation-3` in one run and
`correlation-3` in the next are the third correlation each time, which is not
the same fact - adding one earlier correlation renumbers every later one.
Matching on id would report every finding after an insertion as both
disappeared and appeared.

So identity is built from WHAT the finding is about: its type and the columns
it concerns, plus whichever payload field distinguishes two findings of the
same type on the same columns (a Pareto band letter, a segment name).
"""

from __future__ import annotations

from typing import Any


def _columns_key(columns: Any) -> tuple:
    """Column identity, order-insensitive.

    A correlation between (price, quantity) and one between (quantity, price)
    are the same fact; the engine's column order is an implementation detail
    of how the pair was enumerated, not part of what was found.
    """
    if not isinstance(columns, list):
        return ()
    return tuple(sorted(str(c) for c in columns))


def exploration_key(finding: dict) -> tuple:
    """Identity of an exploration finding across runs."""
    payload = finding.get("payload") or {}
    finding_type = str(finding.get("finding_type", ""))
    base = (finding_type, _columns_key(finding.get("columns")))

    # A distribution-shape finding names the shape it found; two runs whose
    # shape differs are the same finding with a changed value, not two
    # different findings.
    if finding_type in {"summary_stat", "distribution_shape", "trend", "correlation", "missing_pattern"}:
        return base
    # Outlier clusters and cardinality notes are also per-column facts.
    return base


def analytics_key(analysis: str, finding: dict) -> tuple:
    """Identity of a business-analytics finding across runs.

    Includes the analysis name because two analyses can emit the same
    finding_type about the same columns and mean different things.
    """
    payload = finding.get("payload") or {}
    finding_type = str(finding.get("finding_type", ""))
    # The field that distinguishes findings of one type within one analysis.
    discriminator = (
        payload.get("band")
        or payload.get("segment")
        or payload.get("cohort")
        or payload.get("rule_id")
        or payload.get("metric")
        or ""
    )
    return (analysis, finding_type, _columns_key(finding.get("columns")), str(discriminator))


def marketing_key(finding: dict) -> tuple:
    """Identity of a marketing finding across runs.

    Scope (which ad set) is part of identity: the same rule firing on two
    different ad sets is two findings, and the same rule firing on the same ad
    set in two runs is one finding whose value moved.
    """
    payload = finding.get("payload") or {}
    return (
        str(finding.get("finding_type", "")),
        str(payload.get("rule") or payload.get("rule_id") or ""),
        str(payload.get("scope") or ""),
        str(payload.get("metric") or ""),
    )


def describe_exploration(finding: dict) -> str:
    """A human label for a finding, for the appeared/disappeared lists."""
    columns = ", ".join(str(c) for c in (finding.get("columns") or []))
    return f"{finding.get('finding_type', 'finding')} on {columns}" if columns else str(finding.get("finding_type", "finding"))
