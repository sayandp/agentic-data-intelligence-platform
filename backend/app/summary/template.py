"""The template summary: what a run gets when no LLM is available, or when
generation failed its post-checks.

Built from the same facts the LLM path reads, so the two modes describe the
same run and cannot disagree. It is plainer, not less true - and it always
states WHY it is the template, because a reader who cannot tell which mode
produced a summary cannot judge how much to trust its phrasing.

No causal language, by construction: every sentence here is a fixed form
filled with a number.
"""

from __future__ import annotations

from app.summary.facts import FactGroup, SummaryFact


def render_template_summary(facts: list[SummaryFact], reason: str) -> str:
    """A deterministic summary. `reason` is stated first, never omitted."""
    by_group: dict[FactGroup, list[SummaryFact]] = {}
    for fact in facts:
        by_group.setdefault(fact.group, []).append(fact)

    lines = [f"This summary was written without a language model ({reason})."]

    quality = by_group.get(FactGroup.QUALITY, [])
    if quality:
        lines.extend(fact.text for fact in quality[:3])
    else:
        lines.append("No data-quality information was recorded for this run.")

    attention = by_group.get(FactGroup.ATTENTION, [])
    if attention:
        lines.extend(fact.text for fact in attention[:2])
    else:
        lines.append("Nothing from this run is waiting for a decision.")

    findings = by_group.get(FactGroup.FINDING, [])
    if findings:
        lines.extend(fact.text for fact in findings[:2])
    else:
        lines.append("No analysis produced a reportable finding for this run.")

    change = by_group.get(FactGroup.CHANGE, [])
    if change:
        lines.append(change[0].text)

    return " ".join(lines)
