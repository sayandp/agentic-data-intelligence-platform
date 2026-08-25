"""Response schemas and results for the Session Summary Agent.

Stage 1 returns claims citing fact ids; stage 2 returns prose. Both are closed
Pydantic schemas for the same reason every other agent's are: a free-text or
out-of-schema response cannot reach the caller.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.narrative.models import GroundedClaim


class SummaryClaimsResponse(BaseModel):
    """Stage 1's response schema. Reuses GroundedClaim so the grounding
    filter, the rounding and the post-checks are literally the same code the
    Narrative Agent uses - a second claim type would be a second set of rules
    to keep in agreement."""

    claims: list[GroundedClaim] = Field(default_factory=list)


class SessionSummaryProse(BaseModel):
    """Stage 2's response schema.

    One field. The summary is prose for someone who will not read the
    statistics, and giving stage 2 anywhere else to put text is how a
    "recommendations" section grows into a second, unchecked narrative.
    """

    summary_text: str


class ClaimsOutcome(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    claims: list[GroundedClaim] | None
    source: Literal["llm", "escalated_parse_failure", "escalated_quota_exhausted", "escalated_unavailable"]
    rejected_reasons: list[str] = Field(default_factory=list)
    model_name: str | None = None
    #: The EgressRecord for this call, persisted by the caller that holds the
    #: session (app/summary/pipeline.py).
    egress: object | None = None


class ProseOutcome(BaseModel):
    prose: SessionSummaryProse | None
    source: Literal["llm", "escalated_parse_failure", "escalated_quota_exhausted", "escalated_unavailable"]
    model_name: str | None = None
    egress: object | None = None
