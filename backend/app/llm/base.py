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


class LLMResponseError(LLMError):
    """The provider's response was not valid JSON, or didn't match response_schema."""


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
