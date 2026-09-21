"""Provider-agnostic LLM interface.

Nothing above this layer (DiagnosticAgent, ingest.py) knows which concrete
provider it's talking to - only that it implements LLMClient. Gemini is the
only provider this deployment runs (app/llm/factory.py), but the interface
stays provider-agnostic on purpose: adding a future provider is one new
LLMClient subclass, not a rewrite of everything that calls complete().
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMError(Exception):
    """Base for all LLM-layer failures the DiagnosticAgent knows how to handle."""


class LLMRateLimitError(LLMError):
    """The provider returned a rate-limit response (e.g. HTTP 429)."""


class LLMUnavailableError(LLMError):
    """The provider was transiently unreachable/overloaded: an HTTP 5xx, or a
    request that got no HTTP answer at all (connection reset, refused or timed
    out).

    Distinct from LLMResponseError on purpose. A 503 "model is currently
    experiencing high demand" used to be classified as a malformed
    response, which was wrong twice over: it told the caller the model had
    produced something invalid when it had produced nothing at all, and it
    sent the caller down the repair path - re-asking the model to "respond
    again with valid JSON" while the server was down, burning a second
    call to reach the same failure. A run then reported
    `escalated_parse_failure`, so the stored reason blamed parsing for a
    provider outage. Transient by definition, so callers retry it with
    backoff exactly as they do a rate limit.
    """


#: finish_reason values that mean "the model was cut off", not "the model
#: produced something invalid". Kept as a set of UPPERCASE names because
#: providers report this as an enum whose repr differs between SDK
#: versions - compared by name, never by identity.
TRUNCATION_FINISH_REASONS = {"MAX_TOKENS", "LENGTH"}


class LLMResponseError(LLMError):
    """The provider's response was not valid JSON, or didn't match response_schema.

    Carries the evidence needed to tell those two apart AFTER THE FACT.
    Previously this was a bare message, so a parse failure recorded that it
    happened but never why: a response truncated at max_output_tokens and a
    response that was complete but schema-invalid produced the same opaque
    "escalated_parse_failure", with no way to distinguish them from logs or
    from the stored report.
    """

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None = None,
        raw_text: str | None = None,
        model_name: str | None = None,
        schema_name: str | None = None,
        usage: dict | None = None,
    ):
        super().__init__(message)
        self.finish_reason = finish_reason
        self.raw_text = raw_text
        self.model_name = model_name
        self.schema_name = schema_name
        #: Provider token accounting (prompt/output/thinking) when exposed -
        #: the only way to see reasoning tokens eating the output budget.
        self.usage = usage or {}

    @property
    def truncated(self) -> bool:
        return (self.finish_reason or "").upper() in TRUNCATION_FINISH_REASONS

    def cause_summary(self) -> str:
        """A short phrase safe to show a user - names the CAUSE, never an
        internal enum. The technical detail stays in diagnostic_detail()."""
        if self.truncated:
            return "the model's response was cut off before it finished"
        if self.finish_reason and self.finish_reason.upper() not in {"STOP", "FINISH_REASON_UNSPECIFIED"}:
            return f"the model stopped early ({self.finish_reason})"
        return "the model's response did not match the required format"

    def diagnostic_detail(self) -> str:
        """Everything worth having in a log line or an audit trace."""
        parts = [f"finish_reason={self.finish_reason or 'unknown'}"]
        if self.model_name:
            parts.append(f"model={self.model_name}")
        if self.schema_name:
            parts.append(f"schema={self.schema_name}")
        for key in ("prompt_tokens", "output_tokens", "thoughts_tokens", "total_tokens"):
            if self.usage.get(key) is not None:
                parts.append(f"{key}={self.usage[key]}")
        parts.append(f"error={super().__str__()}")
        if self.raw_text is not None:
            parts.append(f"raw_response={self.raw_text!r}")
        return " ".join(parts)


class LLMClient(ABC):
    #: Fixed at 0 by every implementation - determinism is a hard requirement
    #: (Part 6 figures must be reproducible), not a per-call choice.
    temperature: float = 0.0
    model_name: str

    @abstractmethod
    def complete(self, system: str, user: str, response_schema: type[BaseModel]) -> BaseModel:
        """Runs one completion and returns an instance of `response_schema`.

        Must raise LLMRateLimitError on a rate-limit response and
        LLMResponseError on malformed JSON / schema-validation failure -
        never let a provider-specific exception leak past this boundary.
        """
