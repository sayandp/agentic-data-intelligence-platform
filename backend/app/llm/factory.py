from __future__ import annotations

import os

from app.llm.base import LLMClient

DEFAULT_PROVIDER = "gemini"


def get_llm_client() -> LLMClient:
    """Reads LLM_PROVIDER from the environment (default: gemini) and returns
    the matching client. This is the only place provider selection happens -
    everything downstream depends on LLMClient, never a concrete provider.

    Gemini is the only provider this deployment runs. A caller with no key
    configured at all never reaches the "unknown provider" branch below -
    GeminiClient itself raises ValueError("GEMINI_API_KEY is not set...") at
    construction time, which is exactly what every caller of get_llm_client()
    already catches to degrade to a no-LLM state (see
    app/diagnosis/dependency.py, app/narrative/dependency.py, etc). Removing
    a provider is not the same change as removing that degrade path - this
    function still raises/returns normally either way; only LLM_PROVIDER
    values other than "gemini" are newly rejected here."""
    provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).lower()

    if provider == "gemini":
        from app.llm.gemini_client import GeminiClient

        return GeminiClient()

    raise ValueError(f"unknown LLM_PROVIDER '{provider}'; expected 'gemini'")
