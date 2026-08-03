"""Part 2: deterministic post-checks on generated prose. The report reading
well is not the acceptance criterion - these are, and each has to report
precisely what failed and where, not just pass/fail."""

from __future__ import annotations

import re

from app.narrative.config import NarrativeConfig
from app.narrative.models import ClaimValue, GroundedClaim, NarrativeProse, PostCheckKind, Recommendation
from app.narrative.postchecks import check_causal_language, check_claim_coverage, check_number_fidelity, run_post_checks


def _claim(claim_id: str, text: str, finding_id: str, **values: float) -> GroundedClaim:
    return GroundedClaim(
        claim_id=claim_id,
        claim_text=text,
        finding_ids=[finding_id],
        values=[ClaimValue(label=k, value=v) for k, v in values.items()],
    )


# ---- 2.1 number fidelity ----


def test_hallucinated_numeral_is_caught_by_number_fidelity():
    claims = [_claim("claim-0", "amount and price correlate at 0.82.", "correlation-0", coefficient=0.82, sample_size=120.0)]
    prose = NarrativeProse(report_text="Amount and price correlate at 0.95 across 500 rows.", recommendations=[])

    outcome = check_number_fidelity(claims, prose.report_text, NarrativeConfig())

    assert not outcome.passed
    details = " ".join(issue.detail for issue in outcome.issues)
    assert "0.95" in details
    assert "500" in details


def test_formatting_variants_of_the_same_value_are_accepted():
    """0.31 / 31% / 0.310 must all read as 'the same number'."""
    claims = [_claim("claim-0", "the null rate is 0.31.", "summary_stat-0", null_rate=0.31)]
    config = NarrativeConfig()

    for phrasing in ("0.31", "31%", "0.310", "31.0%"):
        prose_text = f"The null rate is {phrasing}."
        outcome = check_number_fidelity(claims, prose_text, config)
        assert outcome.passed, f"{phrasing!r} should have matched claim value 0.31"


def test_number_stated_only_in_claim_text_is_accepted_in_prose():
    """A claim's OWN claim_text carries numbers too (not just `values`) -
    stage 2 restating something the (already-grounded) claim already said
    must not count as hallucination."""
    claims = [_claim("claim-0", "the trend spans from 2020 to 2023.", "trend-0")]
    outcome = check_number_fidelity(claims, "The trend spans from 2020 to 2023.", NarrativeConfig())
    assert outcome.passed


def test_clean_prose_with_no_extra_numbers_passes_fidelity():
    claims = [_claim("claim-0", "amount averages 55.5 over 80 rows.", "summary_stat-0", mean=55.5, count=80.0)]
    outcome = check_number_fidelity(claims, "Amount averages 55.5 over 80 rows.", NarrativeConfig())
    assert outcome.passed


# ---- 2.2 causal language: every banned term from the brief, individually ----

BANNED_TERM_EXAMPLES = [
    "caused", "causes", "drove", "driver", "led to", "results in", "resulted in",
    "due to", "because of", "impact", "effect", "influences", "explains",
]


def test_each_banned_causal_term_is_individually_caught():
    config = NarrativeConfig()
    for term in BANNED_TERM_EXAMPLES:
        text = f"Delivery time {term} the review score to change."
        outcome = check_causal_language(text, config)
        assert not outcome.passed, f"{term!r} should have been flagged as causal language"
        assert outcome.issues[0].kind == PostCheckKind.CAUSAL_LANGUAGE
        assert re.search(re.escape(term), outcome.issues[0].detail, re.IGNORECASE)


def test_permitted_phrasing_is_never_flagged():
    config = NarrativeConfig()
    for phrase in ("is associated with", "correlates with", "moves together with"):
        text = f"Delivery time {phrase} the review score."
        outcome = check_causal_language(text, config)
        assert outcome.passed, f"{phrase!r} must never be flagged"


def test_causal_language_is_case_insensitive_and_reports_location():
    outcome = check_causal_language("This pattern DROVE the outcome entirely.", NarrativeConfig())
    assert not outcome.passed
    assert "drove" in outcome.issues[0].detail.lower()


# ---- 2.3 claim coverage ----


def test_dropped_claim_is_caught_by_coverage():
    claims = [
        _claim("claim-0", "amount and price correlate at 0.82 (n=120).", "correlation-0", coefficient=0.82, sample_size=120.0),
        _claim("claim-1", "quantity averages 3.2 over 90 rows.", "summary_stat-1", mean=3.2, count=90.0),
    ]
    # Only claim-0 is represented; claim-1 is silently dropped.
    prose_text = "Amount and price correlate at 0.82 across 120 orders."

    outcome = check_claim_coverage(claims, prose_text, NarrativeConfig())

    assert not outcome.passed
    assert len(outcome.issues) == 1
    assert "claim-1" in outcome.issues[0].detail


def test_all_claims_represented_passes_coverage_even_when_merged():
    claims = [
        _claim("claim-0", "amount and price correlate at 0.82 (n=120).", "correlation-0", coefficient=0.82, sample_size=120.0),
        _claim("claim-1", "quantity averages 3.2 over 90 rows.", "summary_stat-1", mean=3.2, count=90.0),
    ]
    prose_text = "Amount and price correlate at 0.82 across 120 orders, and separately, quantity averages 3.2 over 90 rows."

    outcome = check_claim_coverage(claims, prose_text, NarrativeConfig())

    assert outcome.passed


def test_coverage_falls_back_to_keyword_match_for_claims_without_distinctive_numbers():
    claims = [_claim("claim-0", "the region column looks like an identifier, not a category.", "cardinality_note-0")]
    prose_text = "The region column appears to behave like an identifier rather than a normal category."

    outcome = check_claim_coverage(claims, prose_text, NarrativeConfig())

    assert outcome.passed


# ---- orchestration: recommendations are scanned for fidelity/causal, not coverage ----


def test_run_post_checks_scans_recommendations_for_fidelity_and_causal_language():
    claims = [_claim("claim-0", "amount and price correlate at 0.82 (n=120).", "correlation-0", coefficient=0.82, sample_size=120.0)]
    prose = NarrativeProse(
        report_text="Amount and price correlate at 0.82 across 120 orders.",
        recommendations=[Recommendation(text="Since this caused the shift, consider adjusting pricing.", claim_id="claim-0")],
    )

    attempt = run_post_checks(claims, prose, attempt=1)

    causal_outcome = next(o for o in attempt.outcomes if o.kind == PostCheckKind.CAUSAL_LANGUAGE)
    assert not causal_outcome.passed
    assert not attempt.passed


def test_run_post_checks_coverage_ignores_recommendations():
    """A claim not restated in a recommendation is not 'dropped' -
    recommendations are optional extras, coverage only concerns the
    narrative body."""
    claims = [
        _claim("claim-0", "amount and price correlate at 0.82 (n=120).", "correlation-0", coefficient=0.82, sample_size=120.0),
        _claim("claim-1", "quantity averages 3.2 over 90 rows.", "summary_stat-1", mean=3.2, count=90.0),
    ]
    prose = NarrativeProse(
        report_text="Amount and price correlate at 0.82 across 120 orders, and quantity averages 3.2 over 90 rows.",
        recommendations=[Recommendation(text="Consider reviewing pricing.", claim_id="claim-0")],
    )

    attempt = run_post_checks(claims, prose, attempt=1)

    assert attempt.passed
