"""Stage 1 output validation - the half of 'reject at parse time' a bare
Pydantic field can't do on its own.

GroundedClaim.finding_ids being non-empty is enforced by the model itself
(Field(min_length=1) in app/narrative/models.py) - a claim with an empty
list cannot be constructed at all, the same way FixAction cannot hold a
free-text action. Checking that every cited finding_id actually exists in
THIS run's findings needs the run's data, which a model class parsed via
LLMClient.complete() has no access to - so it happens here, immediately
after stage 1 returns, before anything downstream ever sees a claim.

Dashboard UX pass, Part 3 (NUMBER PRECISION): this is also where every
surviving claim's numeric values get rounded to a readable precision - see
_round_claim_value below. Doing it here, once, means every downstream
consumer (stage 2 prose generation, the post-checks, the persisted report)
sees the same already-rounded number; nothing downstream needs its own
rounding step.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.narrative.config import NarrativeConfig
from app.narrative.models import ClaimValue, GroundedClaim


@dataclass
class GroundingResult:
    valid_claims: list[GroundedClaim]
    rejected_reasons: list[str]


def _round_claim_value(value: float, decimals: int) -> float:
    """A value that's already whole - a count, cardinality, sample_size, ...
    every such field is built via int()/float(int) at its source, see
    app/exploration/stats.py - stays whole ("whole numbers for counts").
    Anything else (a mean, std, correlation coefficient, rate, ...) rounds
    to `decimals` places ("2 significant decimals for means/stds"), e.g.
    61.271834912 -> 61.27. This is what makes the number_fidelity post-check
    (app/narrative/postchecks.py) accept "61.27" in generated prose: the
    claim's OWN stored value is now 61.27, not the check's comparison being
    loosened to tolerate a truncated form of a longer number."""
    if float(value).is_integer():
        return float(round(value))
    return round(value, decimals)


def filter_grounded_claims(
    raw_claims: list[GroundedClaim], known_finding_ids: set[str], config: NarrativeConfig | None = None
) -> GroundingResult:
    """Drops (never silently keeps) any claim referencing a finding_id this
    run's ExplorationFindings doesn't actually contain, rounds every
    surviving claim's values to a readable precision, and assigns each
    surviving claim a stable claim_id (position-based, like Finding.id -
    the model never assigns its own). A claim's own finding_ids being
    non-empty was already guaranteed before this function is ever called -
    that failure mode raises at Pydantic parse time instead."""
    config = config or NarrativeConfig()
    valid: list[GroundedClaim] = []
    rejected_reasons: list[str] = []

    for claim in raw_claims:
        unknown = [fid for fid in claim.finding_ids if fid not in known_finding_ids]
        if unknown:
            rejected_reasons.append(f"claim {claim.claim_text!r} references unknown finding_ids {unknown} - rejected")
            continue
        claim.values = [
            ClaimValue(label=v.label, value=_round_claim_value(v.value, config.claim_value_round_decimals)) for v in claim.values
        ]
        valid.append(claim)

    for i, claim in enumerate(valid):
        claim.claim_id = f"claim-{i}"

    return GroundingResult(valid_claims=valid, rejected_reasons=rejected_reasons)
