"""Part 2: deterministic post-checks on the generated prose. Run after
EVERY generation attempt (never skipped, never LLM-judged) - the report
reading well is not the acceptance criterion, these are.

Each check reports precisely what failed and where (PostCheckIssue.detail),
never just a pass/fail bit - the results are persisted (Part 6), not merely
used as an internal switch.
"""

from __future__ import annotations

import re

from app.narrative.config import NarrativeConfig
from app.narrative.models import (
    GroundedClaim,
    NarrativeProse,
    PostCheckAttempt,
    PostCheckIssue,
    PostCheckKind,
    PostCheckOutcome,
)
from app.narrative.numerics import extract_numerals, extract_raw_numerals, normalized_candidates, value_forms

_STOPWORDS = frozenset(
    {
        "this", "that", "with", "from", "have", "were", "been", "which", "there", "their", "about",
        "above", "below", "between", "shows", "shown", "value", "values", "found", "across", "these",
        "those", "based", "than", "then", "each", "when", "while", "into", "over", "under", "such",
    }
)


# ---------------------------------------------------------------------------
# 2.1 number fidelity
# ---------------------------------------------------------------------------


def _acceptable_values(claims: list[GroundedClaim], decimals: int) -> set[float]:
    """The ground-truth numeral set: every value a claim explicitly carries,
    PLUS every numeral already present in the claim's own text (a claim that
    validly survived stage 1 is itself grounded, so any number it already
    states is fair game for stage 2 to repeat)."""
    values: set[float] = set()
    for claim in claims:
        for claim_value in claim.values:
            values |= value_forms(claim_value.value, decimals)
        values |= extract_numerals(claim.claim_text, decimals)
    return values


def check_number_fidelity(claims: list[GroundedClaim], text: str, config: NarrativeConfig) -> PostCheckOutcome:
    ground_truth = _acceptable_values(claims, config.numeric_match_decimals)
    issues: list[PostCheckIssue] = []
    for token in extract_raw_numerals(text):
        candidates = normalized_candidates(token, config.numeric_match_decimals)
        if candidates and not (candidates & ground_truth):
            issues.append(
                PostCheckIssue(
                    kind=PostCheckKind.NUMBER_FIDELITY,
                    detail=f"numeral {token!r} has no matching value in any grounded claim - likely hallucinated",
                )
            )
    return PostCheckOutcome(kind=PostCheckKind.NUMBER_FIDELITY, passed=not issues, issues=issues)


# ---------------------------------------------------------------------------
# 2.2 causal language
# ---------------------------------------------------------------------------


def check_causal_language(text: str, config: NarrativeConfig) -> PostCheckOutcome:
    issues: list[PostCheckIssue] = []
    for pattern in config.causal_lexicon:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = max(0, match.start() - 30), min(len(text), match.end() + 30)
            snippet = text[start:end].strip().replace("\n", " ")
            issues.append(
                PostCheckIssue(
                    kind=PostCheckKind.CAUSAL_LANGUAGE,
                    detail=f"banned causal term {match.group()!r} found in: '...{snippet}...'",
                )
            )
    return PostCheckOutcome(kind=PostCheckKind.CAUSAL_LANGUAGE, passed=not issues, issues=issues)


# ---------------------------------------------------------------------------
# 2.3 claim coverage
# ---------------------------------------------------------------------------


def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z_]{3,}", text)
    return [w.lower() for w in words if w.lower() not in _STOPWORDS]


def _claim_is_covered(claim: GroundedClaim, prose_numerals: set[float], prose_lower: str, decimals: int) -> bool:
    claim_values: set[float] = set()
    for claim_value in claim.values:
        claim_values |= value_forms(claim_value.value, decimals)
    claim_values |= extract_numerals(claim.claim_text, decimals)
    if claim_values & prose_numerals:
        return True
    # Numeric overlap is the strong signal; a claim with no distinctive
    # number (or one stage 2 fully paraphrased away) falls back to a
    # keyword check against its own claim_text.
    return any(keyword in prose_lower for keyword in _significant_words(claim.claim_text))


def check_claim_coverage(claims: list[GroundedClaim], report_text: str, config: NarrativeConfig) -> PostCheckOutcome:
    prose_numerals: set[float] = set()
    for token in extract_raw_numerals(report_text):
        prose_numerals |= normalized_candidates(token, config.numeric_match_decimals)
    prose_lower = report_text.lower()

    issues: list[PostCheckIssue] = []
    for claim in claims:
        if not _claim_is_covered(claim, prose_numerals, prose_lower, config.numeric_match_decimals):
            issues.append(
                PostCheckIssue(
                    kind=PostCheckKind.CLAIM_COVERAGE,
                    detail=f"claim {claim.claim_id!r} ({claim.claim_text!r}) is not represented anywhere in the generated prose",
                )
            )
    return PostCheckOutcome(kind=PostCheckKind.CLAIM_COVERAGE, passed=not issues, issues=issues)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def run_post_checks(
    claims: list[GroundedClaim], prose: NarrativeProse, attempt: int, config: NarrativeConfig | None = None
) -> PostCheckAttempt:
    config = config or NarrativeConfig()
    # Fidelity and causal-language scan every word the LLM wrote, including
    # recommendations - a fabricated number or a causal claim tucked into a
    # "suggestion" is exactly as ungrounded as one in the main narrative.
    full_generated_text = "\n".join([prose.report_text, *(rec.text for rec in prose.recommendations)])
    outcomes = [
        check_number_fidelity(claims, full_generated_text, config),
        check_causal_language(full_generated_text, config),
        # Coverage is scoped to report_text only - a claim not restated in a
        # recommendation is not "dropped", it simply had no suggestion
        # attached to it.
        check_claim_coverage(claims, prose.report_text, config),
    ]
    return PostCheckAttempt(attempt=attempt, outcomes=outcomes)
