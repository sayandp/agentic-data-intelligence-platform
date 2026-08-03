"""Part 1: the Query Agent's structural contracts.

`query_kind` is chosen DETERMINISTICALLY by source type BEFORE generation
(app/query/pipeline.py) - never by the model. The model fills in `code` for
the kind it was given, or reports "unanswerable" - a first-class valid
output, not a failure. Every field here is a closed enum or a structurally
bounded type; nothing about what eventually runs is inferred from free
text the model produced about itself.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class QueryKind(str, Enum):
    SQL = "sql"
    PANDAS = "pandas"
    UNANSWERABLE = "unanswerable"


class GeneratedQuery(BaseModel):
    """The Query Agent's LLMClient.complete() response_schema."""

    query_kind: QueryKind
    code: str = ""
    columns_referenced: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class GenerationOutcome(BaseModel):
    """Mirrors app/narrative/models.py::ClaimsOutcome's shape - `query` is
    None only when the LLM stage failed entirely (quota exhausted, or
    malformed output survived the one repair attempt), which the pipeline
    treats identically to 'LLM unavailable'."""

    model_config = {"arbitrary_types_allowed": True}

    query: GeneratedQuery | None
    source: Literal["llm", "cache", "escalated_parse_failure", "escalated_quota_exhausted"]
    model_name: str | None = None
    temperature: float | None = None


class EscalationReason(str, Enum):
    """Closed vocabulary for why an answer was NOT produced - Part 4's
    "route to escalation rather than answering" conditions, one member
    each, so the reason recorded is always exactly one of the documented
    triggers, never a free-text explanation standing in for one."""

    LLM_UNAVAILABLE = "llm_unavailable"
    UNANSWERABLE = "unanswerable"
    KIND_MISMATCH = "kind_mismatch"  # model's query_kind didn't match the deterministically-assigned one
    UNKNOWN_COLUMN = "unknown_column"
    LOW_CONFIDENCE = "low_confidence"
    STATIC_VALIDATION_FAILED = "static_validation_failed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    EXECUTION_KILLED = "execution_killed"


# Reasons where the generated code itself is not known-safe-and-just-
# unconfident - there is nothing a human can respond "approve, run it" to,
# because either there IS no code (unanswerable), the code never passed
# validation (static_validation_failed, kind_mismatch), or it already ran
# and failed/timed out/was killed. Only LOW_CONFIDENCE leaves behind code
# that is validated-safe and simply under-confident - see
# app/routers/approvals.py's query-resolution restriction.
APPROVABLE_ESCALATION_REASONS = frozenset({EscalationReason.LOW_CONFIDENCE})


class QueryAnswerStatus(str, Enum):
    ANSWERED = "answered"
    ESCALATED = "escalated"


class QueryAnswer(BaseModel):
    """The full assembled response - what POST /ask returns, and what
    app/models.py::QueryRun persists. quality_context_summary is always
    populated (Part 4/5's "attached to the answer, rendered before the
    result" rule) and code is always shown when one was generated, even on
    escalation - the user must be able to see what would have run."""

    status: QueryAnswerStatus
    quality_context_summary: str
    question: str
    query_kind: QueryKind | None = None
    code: str | None = None
    result: dict | None = None
    truncated: bool = False
    row_count: int | None = None
    columns_referenced: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float | None = None
    escalation_reason: EscalationReason | None = None
    escalation_detail: str | None = None
