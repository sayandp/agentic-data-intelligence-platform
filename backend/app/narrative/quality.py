"""Part 5: quality context is not a footnote.

Deterministically rendered - NEVER by the LLM, in either generation mode -
because the one thing this system cannot afford is a fluent report that
reads as authoritative over repaired or unvalidated data. Both
app/narrative/pipeline.py's llm path and its template path call this same
function and place its output in the same field
(NarrativeReport.quality_context_summary), rendered first
(NarrativeReport.rendered_text) - the caveat cannot be dropped, reordered,
or rephrased away by anything downstream.
"""

from __future__ import annotations

from app.exploration.findings import DataQualityContext, ResolutionKind

_NOTABLE_KINDS = {
    ResolutionKind.AUTO_FIXED: "issue(s) were detected and automatically fixed, then verified against the baseline",
    ResolutionKind.AUTO_FIX_REVERTED: "automatic fix attempt(s) FAILED verification and were reverted, then escalated for human review",
    ResolutionKind.APPROVED_AND_APPLIED: "issue(s) were resolved by an explicit human approval",
    ResolutionKind.APPROVED_FIX_FAILED_VERIFICATION: "human-approved fix attempt(s) failed verification and were rejected",
    ResolutionKind.REJECTED_FIX_DATA_ACCEPTABLE: "flagged issue(s) were reviewed and judged acceptable as-is, with no fix applied",
    ResolutionKind.ACCEPTED_AS_NEW_BASELINE: "flagged change(s) were accepted as the new normal and superseded the prior baseline",
}


def render_quality_context_summary(dqc: DataQualityContext) -> str:
    """Never empty - always states plainly whether this run's data can be
    read as-is or carries caveats, even when the answer is 'nothing to
    report'."""
    lines: list[str] = []

    if dqc.active_baseline_provisional:
        lines.append(
            "- The active baseline for this source is still PROVISIONAL (established automatically on first "
            "ingest, not yet confirmed by a human). The findings below describe how this run compares to that "
            "unconfirmed baseline."
        )

    for kind, description in _NOTABLE_KINDS.items():
        count = dqc.resolution_counts.get(kind, 0)
        if count:
            lines.append(f"- {count} {description}.")

    if not lines:
        if dqc.total_events:
            lines.append(
                f"- {dqc.total_events} validation event(s) were recorded for this run; none required a fix or "
                "carried forward as an open concern."
            )
        else:
            lines.append("- No data-quality issues were detected against the active baseline for this run.")

    return "\n".join(lines)
