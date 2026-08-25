"""Deterministic post-checks on the generated summary.

Three of the four are the Narrative Agent's, imported rather than
reimplemented: number fidelity, causal language, claim coverage. Writing a
second copy would create two definitions of "a fabricated number" that must
agree forever, which is the failure this codebase keeps finding.

The fourth is specific to this agent: LENGTH. The brief is 4-8 short
sentences, and both ends matter. A one-sentence summary has dropped most of
what the run found while still reading as complete; a twenty-sentence one is
the detailed report again under a different heading, for the reader who was
promised they would not have to read it.
"""

from __future__ import annotations

import re

from app.narrative.models import (
    GroundedClaim,
    NarrativeProse,
    PostCheckAttempt,
    PostCheckIssue,
    PostCheckKind,
    PostCheckOutcome,
)
from app.narrative.postchecks import run_post_checks as run_narrative_post_checks
from app.summary.config import SummaryConfig

#: A sentence ends at . ! or ? followed by whitespace or end of text. Kept
#: simple deliberately: an abbreviation-aware splitter would be another
#: heuristic to maintain, and the check only needs to distinguish "about
#: five" from "one" or "twenty".
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def count_sentences(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return len(_SENTENCE_END.findall(stripped)) or 1


def check_length(text: str, config: SummaryConfig) -> PostCheckOutcome:
    count = count_sentences(text)
    issues: list[PostCheckIssue] = []
    if count < config.min_sentences:
        issues.append(
            PostCheckIssue(
                kind=PostCheckKind.CLAIM_COVERAGE,
                detail=(
                    f"the summary is {count} sentence(s), below the minimum of {config.min_sentences} - "
                    "a summary this short has dropped most of what the run found while still reading as complete"
                ),
            )
        )
    elif count > config.max_sentences:
        issues.append(
            PostCheckIssue(
                kind=PostCheckKind.CLAIM_COVERAGE,
                detail=(
                    f"the summary is {count} sentence(s), above the maximum of {config.max_sentences} - "
                    "this is the detailed report again, for a reader who was promised they would not have to read it"
                ),
            )
        )
    return PostCheckOutcome(kind=PostCheckKind.CLAIM_COVERAGE, passed=not issues, issues=issues)


def run_summary_post_checks(
    claims: list[GroundedClaim], summary_text: str, attempt: int, config: SummaryConfig | None = None
) -> PostCheckAttempt:
    """Every check, every attempt. Never skipped, never judged by a model."""
    config = config or SummaryConfig()
    # The narrative checks operate on prose; the summary is prose with no
    # recommendations, so it is adapted rather than duplicated.
    prose = NarrativeProse(report_text=summary_text, recommendations=[])
    attempt_result = run_narrative_post_checks(claims, prose, attempt, config.narrative)
    attempt_result.outcomes.append(check_length(summary_text, config))
    return attempt_result
