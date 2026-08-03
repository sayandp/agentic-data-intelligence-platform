"""Stage 1 (grounding) validation: 'reject at parse time any claim with an
empty finding_ids, or referencing a finding ID absent from the input.' Two
different mechanisms enforce the two halves - a bare Pydantic field for the
empty case (no external context needed), a post-parse filter for the
unknown-id case (needs the actual run's finding ids, which no Pydantic
field validator alone can see)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.narrative.grounding import filter_grounded_claims
from app.narrative.models import ClaimValue, GroundedClaim, GroundedClaimsResponse


def test_empty_finding_ids_cannot_even_be_constructed():
    """Structural, not a check that has to catch it - Field(min_length=1)
    makes the invalid value unrepresentable."""
    with pytest.raises(ValidationError):
        GroundedClaim(claim_text="amount correlates with price.", finding_ids=[], values=[])


def test_stage1_response_with_empty_finding_ids_fails_to_parse():
    """The same guarantee, exercised the way it actually happens: an LLM's
    raw JSON response failing schema validation because one claim's
    finding_ids is empty - this is what a malformed-output retry in
    NarrativeAgent would see as a validation failure."""
    raw = {"claims": [{"claim_text": "amount correlates with price.", "finding_ids": [], "values": []}]}
    with pytest.raises(ValidationError):
        GroundedClaimsResponse.model_validate(raw)


def test_claim_referencing_unknown_finding_id_is_rejected():
    known_ids = {"summary_stat-0", "correlation-0"}
    valid_claim = GroundedClaim(claim_text="amount has a mean of 55.", finding_ids=["summary_stat-0"], values=[])
    bogus_claim = GroundedClaim(claim_text="amount correlates with a column that doesn't exist.", finding_ids=["correlation-99"], values=[])

    result = filter_grounded_claims([valid_claim, bogus_claim], known_ids)

    assert len(result.valid_claims) == 1
    assert result.valid_claims[0].claim_text == valid_claim.claim_text
    assert result.rejected_reasons
    assert "correlation-99" in result.rejected_reasons[0]


def test_claim_referencing_a_mix_of_known_and_unknown_ids_is_rejected():
    """A claim only survives if EVERY finding_id it cites is real - partial
    credit isn't grounding."""
    known_ids = {"summary_stat-0"}
    claim = GroundedClaim(claim_text="two things.", finding_ids=["summary_stat-0", "correlation-99"], values=[])

    result = filter_grounded_claims([claim], known_ids)

    assert result.valid_claims == []
    assert result.rejected_reasons


def test_claim_ids_assigned_deterministically_after_filtering():
    known_ids = {"summary_stat-0", "summary_stat-1"}
    claims = [
        GroundedClaim(claim_text="first.", finding_ids=["summary_stat-0"], values=[]),
        GroundedClaim(claim_text="bogus.", finding_ids=["does-not-exist"], values=[]),
        GroundedClaim(claim_text="second.", finding_ids=["summary_stat-1"], values=[]),
    ]

    result = filter_grounded_claims(claims, known_ids)

    assert [c.claim_id for c in result.valid_claims] == ["claim-0", "claim-1"]
    assert [c.claim_text for c in result.valid_claims] == ["first.", "second."]


def test_all_claims_rejected_yields_empty_valid_list_not_an_exception():
    known_ids = {"summary_stat-0"}
    claims = [GroundedClaim(claim_text="bogus.", finding_ids=["does-not-exist"], values=[])]

    result = filter_grounded_claims(claims, known_ids)

    assert result.valid_claims == []
    assert len(result.rejected_reasons) == 1


# ---- dashboard UX pass, Part 3: NUMBER PRECISION ----


def test_non_whole_values_are_rounded_to_two_decimal_places():
    known_ids = {"summary_stat-0"}
    claim = GroundedClaim(
        claim_text="amount has a mean of 61.271834912.",
        finding_ids=["summary_stat-0"],
        values=[ClaimValue(label="mean", value=61.271834912), ClaimValue(label="std", value=3.14159265)],
    )

    result = filter_grounded_claims([claim], known_ids)

    values = result.valid_claims[0].values
    assert values[0].value == 61.27
    assert values[1].value == 3.14


def test_whole_values_stay_whole_not_forced_into_decimals():
    known_ids = {"summary_stat-0"}
    claim = GroundedClaim(
        claim_text="x.",
        finding_ids=["summary_stat-0"],
        values=[ClaimValue(label="count", value=157.0), ClaimValue(label="std", value=5.0)],
    )

    result = filter_grounded_claims([claim], known_ids)

    values = result.valid_claims[0].values
    assert values[0].value == 157
    assert values[1].value == 5
