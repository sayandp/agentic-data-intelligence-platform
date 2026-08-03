"""The Diagnostic Agent's output contract.

Every field is a closed vocabulary, enforced by both this Pydantic model AND
the LLM provider's response schema (Gemini's response_schema /
response_mime_type="application/json") - the model is structurally
incapable of returning a free-text action or an out-of-enum cause. That's
what makes cause_category safe to exact-match score in Part 6, and what
makes suggested_fix.action safe for the Part 5 gate to key off of.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CauseCategory(str, Enum):
    RENAME = "rename"
    DTYPE_CHANGE = "dtype_change"
    NULL_FLOOD = "null_flood"
    COLUMN_DROPPED = "column_dropped"
    DISTRIBUTION_SHIFT = "distribution_shift"
    WHITESPACE_CASE = "whitespace_case"
    ROW_LOSS = "row_loss"
    UNKNOWN = "unknown"  # a valid, PREFERRED answer over a guess


class FixAction(str, Enum):
    RENAME_COLUMN = "rename_column"
    SAFE_TYPE_CAST = "safe_type_cast"
    STRIP_WHITESPACE = "strip_whitespace"
    NORMALIZE_CASE = "normalize_case"
    ESCALATE = "escalate"


class RiskLevel(str, Enum):
    LOW = "low"
    HIGH = "high"


class SuggestedFix(BaseModel):
    # No `parameters` field, deliberately - app/gate.py::build_fix_spec already
    # never read one (fix parameters come only from validation-event data,
    # never from the diagnosis - see the README's "make the bad state
    # unrepresentable" section), so a free-form dict here was dead weight
    # with no consumer, and a bare `dict` also breaks real Gemini Developer
    # API structured output (JSON schema `additionalProperties` isn't
    # supported outside Enterprise mode). Removing it is strictly stronger
    # than the prior "unread" guarantee: there is now no field for a bad or
    # injected parameter to occupy at all.
    action: FixAction


class Diagnosis(BaseModel):
    cause_category: CauseCategory
    likely_cause: str
    suggested_fix: SuggestedFix
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
