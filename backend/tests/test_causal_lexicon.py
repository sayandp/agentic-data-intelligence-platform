"""The shared causal lexicon, and the gap that existed in it from Phase 5.

ONE lexicon, used by the Narrative Agent and the Session Summary Agent. Two
definitions of "causal language" that must agree forever is the failure this
codebase keeps removing, so this file tests the shared one and both consumers
read it.

The gap: `\\bbecause of\\b` was present and bare `\\bbecause\\b` was not, so
"sales fell because the feed broke" - the most ordinary causal sentence in
English - cleared the check for the entire life of the guarantee.
"""

import pytest

from app.narrative.config import DEFAULT_CAUSAL_LEXICON, NarrativeConfig
from app.narrative.postchecks import check_causal_language
from app.summary.config import SummaryConfig


def flagged(text: str, config: NarrativeConfig | None = None) -> bool:
    return not check_causal_language(text, config or NarrativeConfig()).passed


# ---- the gap that shipped ----


@pytest.mark.parametrize(
    "text",
    [
        "Sales fell because the feed broke.",
        "Because this is the first completed run, there is nothing to compare.",
        "The drop happened because of the outage.",
    ],
)
def test_because_is_caught_in_every_form(text):
    """The regression that motivated this file. `because of` alone left the
    single most common causal connective in English uncaught."""
    assert flagged(text)


# ---- the audit: each pattern as broad as the concept it names ----


@pytest.mark.parametrize(
    "text",
    [
        # asserting a cause
        "The outage caused the gap.",
        "This is causing the drop.",
        "There is a causal link between them.",
        # one thing producing another
        "Spend drives conversions.",
        "Spend drove conversions.",
        "These are the drivers of revenue.",
        "Higher spend is driving the increase.",
        "More spend leads to more clicks.",
        "More spend leading to more clicks was observed.",
        "The change led to a drop.",
        "This results in a lower rate.",
        "This resulted in a lower rate.",
        "The drop stems from the outage.",
        "The gap stemmed from a feed failure.",
        "The pattern arises from the sampling.",
        "The pattern arose from the sampling.",
        # attributing an outcome
        "The drop is due to the feed.",
        "The drop is owing to the feed.",
        "Thanks to the change, CPA improved.",
        "The feed is responsible for the gap.",
        "The gap is attributable to the feed.",
        "The gap is attributed to the feed.",
        # inferential connectives
        "As a result, the total is lower.",
        "Consequently, the total is lower.",
        "Therefore, the total is lower.",
        "Hence the total is lower.",
        "Thus the total is lower.",
        # "the reason ..." with its causal continuation
        "The reason for the drop is the outage.",
        "The reason why the rate fell is unclear.",
        # effect and influence
        "This impacted revenue.",
        "The effect was a lower rate.",
        "Spend influences conversions.",
        "This explains the drop.",
    ],
)
def test_every_audited_concept_is_caught(text):
    assert flagged(text), f"not flagged: {text!r}"


# ---- what must NOT be caught ----


def test_a_column_literally_named_reason_is_not_a_causal_claim():
    """`the reason` requires its causal continuation precisely so a dataset
    with a `reason` column does not trip the check the moment prose mentions
    it. A bare pattern would have made the check unusable on such a source."""
    assert not flagged("The reason column has 3 nulls.")
    assert not flagged("The reason field is categorical with 4 distinct values.")


@pytest.mark.parametrize(
    "text",
    [
        "Band A is associated with a value share of 0.68.",
        "Revenue correlates with quantity.",
        "The two series move together with a coefficient of 0.8.",
    ],
)
def test_the_permitted_phrasing_survives(text):
    """The brief explicitly permits these. Banning the vocabulary this system
    uses to describe a correlation would ban the finding itself."""
    assert not flagged(text)


def test_affect_is_deliberately_not_banned():
    """"the affected column" and "affected rows" are this system's own
    descriptive vocabulary for what a validation event touched. No word
    -boundary pattern separates that from "X affects Y", so it is left out
    with the reason recorded rather than guessed at."""
    from app.narrative.config import NOT_BANNED_WITH_REASON

    assert "affected" in NOT_BANNED_WITH_REASON
    assert not flagged("The affected column had 12 null rows.")


# ---- one lexicon, both agents ----


def test_the_summary_agent_reads_the_shared_lexicon():
    """Not its own copy. Two definitions that must agree forever is the
    failure this codebase keeps removing."""
    assert SummaryConfig().narrative.causal_lexicon is DEFAULT_CAUSAL_LEXICON


def test_the_summary_post_checks_catch_a_bare_because():
    from app.narrative.models import GroundedClaim
    from app.summary.postchecks import run_summary_post_checks

    claims = [GroundedClaim(claim_id="claim-0", claim_text="The run completed.", finding_ids=["fact-quality-0"], values=[])]

    attempt = run_summary_post_checks(
        claims, "The run completed. Nothing needed a decision. Sales fell because the feed broke. Nothing changed.", 1
    )

    assert not all(o.passed for o in attempt.outcomes)
    details = " ".join(i.detail for o in attempt.outcomes for i in o.issues)
    assert "because" in details.lower()
