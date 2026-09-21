"""Google Gemini via the AI Studio developer API (google-genai SDK).

Deliberately NOT Vertex AI - Vertex requires a GCP billing account, and the
whole point of this provider choice is staying on Gemini's free tier.
"""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMClient, LLMRateLimitError, LLMResponseError, LLMUnavailableError

# Only free-tier models are ever allowed, regardless of what GEMINI_MODEL is
# set to - a stray env change (or a typo pointing at a paid model) must fail
# loudly at construction time, not silently start billing.
#
# gemini-2.5-flash/-lite (the previous allowlist) are still reachable via
# this key's ListModels response, but gemini-2.0-flash/-lite - one
# generation further back - are documented as shut down (2026-06-01),
# which is the concrete failure mode this allowlist exists to catch early:
# a model that quietly stops being free-tier (or stops existing) should
# fail loudly at client construction, not surface as a mystery 404 mid-run.
# Verified directly against the API (not guessed):
#   curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY"
# cross-checked against https://ai.google.dev/gemini-api/docs/pricing for a
# "Free of charge" row. gemini-3.5-flash / gemini-3.5-flash-lite is the
# newest matched (non-preview) pair confirmed on both.
FREE_TIER_MODELS = {"gemini-3.5-flash", "gemini-3.5-flash-lite"}
DEFAULT_MODEL = "gemini-3.5-flash"

# gemini-3.5-flash spends "thinking" tokens out of this SAME budget by
# default (confirmed live: a 500-token budget left only ~56 tokens for the
# actual answer, truncating every structured response mid-string with
# finish_reason=MAX_TOKENS). gemini-3.5-flash-lite does the opposite - it
# does not think by default, and explicitly forcing thinking_budget=0
# raises a 400 INVALID_ARGUMENT on it. Since both allowlisted models have
# to share one code path, the fix is a budget generous enough to absorb
# flash's hidden reasoning AND the actual answer, rather than trying to
# force thinking off (which doesn't work uniformly across the allowlist).
MAX_OUTPUT_TOKENS = 4096
RATE_LIMIT_HTTP_CODE = 429


# How much of a failed response body to keep. Enough to see WHERE a
# truncated JSON string stopped, bounded so a log line stays readable and
# no oversized blob is retained.
RAW_TEXT_SNIPPET_CHARS = 600


def _finish_reason_of(response) -> str | None:
    """The candidate's finish_reason as a plain uppercase name.

    The SDK reports this as an enum whose repr has changed between
    versions, so this normalises to `.name` when present and falls back to
    str(). Returns None when the response carries no candidate at all
    (which is itself worth seeing as "unknown" downstream).
    """
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        reason = getattr(candidates[0], "finish_reason", None)
        if reason is None:
            return None
        return str(getattr(reason, "name", reason)).upper()
    except Exception:  # noqa: BLE001 - diagnostics must never mask the original failure
        return None


def _raw_text_of(response) -> str | None:
    """The response body, truncated for logging. `.text` itself can raise
    or be None when the model returned no usable part."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - see _finish_reason_of
        return None
    if text is None:
        return None
    if len(text) > RAW_TEXT_SNIPPET_CHARS:
        return f"{text[:RAW_TEXT_SNIPPET_CHARS]}... [{len(text)} chars total]"
    return text


def _usage_of(response) -> dict:
    """Token accounting, including THINKING tokens - gemini-3.5-flash
    spends those from the same max_output_tokens budget as the answer (see
    MAX_OUTPUT_TOKENS above), so a truncation is only explicable with this
    number in hand."""
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return {}
        return {
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "thoughts_tokens": getattr(usage, "thoughts_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
        }
    except Exception:  # noqa: BLE001 - see _finish_reason_of
        return {}


def _load_api_keys_from_env() -> list[str]:
    """GEMINI_API_KEYS (comma-separated) is for multi-key rotation - each
    free-tier key gets its own separate 20-requests/day quota bucket
    (per-project, per-model), so a second key is real extra headroom, not
    a workaround. Falls back to the single GEMINI_API_KEY for a one-key
    .env, unchanged from before this existed."""
    multi = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in multi.split(",") if k.strip()]
    if keys:
        return keys
    single = os.environ.get("GEMINI_API_KEY")
    return [single] if single else []


class GeminiClient(LLMClient):
    temperature = 0.0

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.model_name = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        if self.model_name not in FREE_TIER_MODELS:
            raise ValueError(
                f"'{self.model_name}' is not on the free-tier allowlist {sorted(FREE_TIER_MODELS)}; "
                "refusing to construct a client that could incur cost"
            )

        self._api_keys = [api_key] if api_key else _load_api_keys_from_env()
        if not self._api_keys:
            raise ValueError(
                "GEMINI_API_KEY is not set (checked constructor arg, GEMINI_API_KEYS, and "
                "GEMINI_API_KEY in the environment)"
            )

        self._key_index = 0
        self._client = None  # lazy: real SDK client is only built on first use, rebuilt on key rotation

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_keys[self._key_index])
        return self._client

    def _advance_key(self) -> bool:
        """Moves to the next configured key after the current one hits a
        429, so the NEXT call starts from a key that's actually still
        got quota instead of re-discovering the same exhausted one every
        time. Returns False once every key has been tried - the caller's
        job at that point is to raise, same as the single-key case always
        did."""
        if self._key_index + 1 >= len(self._api_keys):
            return False
        exhausted_key_num = self._key_index + 1
        self._key_index += 1
        self._client = None
        print(
            f"[gemini] key {exhausted_key_num} of {len(self._api_keys)} hit a 429 - "
            f"switching to key {self._key_index + 1} of {len(self._api_keys)}"
        )
        return True

    def complete(self, system: str, user: str, response_schema: type[BaseModel]) -> BaseModel:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=response_schema,
        )

        # A 429 here rotates to the next configured key and retries the
        # SAME request, rather than surfacing immediately - only once every
        # key is exhausted does this raise LLMRateLimitError, exactly like
        # the single-key case always did. Callers above this boundary
        # (DiagnosticAgent's own backoff/retry loop, etc.) see no
        # difference between "one key, exhausted" and "N keys, all
        # exhausted" - key rotation is entirely internal to this client.
        while True:
            client = self._get_client()
            try:
                response = client.models.generate_content(model=self.model_name, contents=user, config=config)
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code == RATE_LIMIT_HTTP_CODE:
                    if self._advance_key():
                        continue
                    raise LLMRateLimitError(str(exc)) from exc
                # A 5xx is the SERVER failing, not the model answering
                # badly - rotating keys can't help (every key reaches the
                # same overloaded model) and neither can asking it to
                # repair its JSON, because there is no JSON. Surfaced as
                # its own transient error so callers back off and retry,
                # and so the recorded reason says "unavailable" instead of
                # blaming a parse that never happened.
                if isinstance(code, int) and code >= 500:
                    raise LLMUnavailableError(str(exc)) from exc
                raise LLMResponseError(str(exc)) from exc
            except httpx.TransportError as exc:
                # The request never got an HTTP answer at all: the connection
                # was reset, refused, or timed out. That is not an APIError,
                # so it used to escape this client as a raw httpx exception -
                # an error no agent recognises. The Narrative and Summary
                # agents fall back to their templates on LLMUnavailableError,
                # but a raw ConnectError went straight past that, out of the
                # node, and left the run "completed" with no report, forever.
                # Measured: 3 node failures in the local database, all
                # `ConnectError: [WinError 10054] An existing connection was
                # forcibly closed by the remote host` - 2 in narrate, 1 in
                # summarise. It is the provider being unreachable, which is
                # exactly what LLMUnavailableError means; callers already
                # retry it with backoff and then degrade with the reason
                # stated.
                raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc
            break

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_schema):
            return parsed

        # The SDK couldn't parse `response.text` into response_schema itself;
        # try once more explicitly so the failure mode is a clean, catchable
        # LLMResponseError rather than an AttributeError on `None`.
        try:
            return response_schema.model_validate_json(response.text)
        except (ValidationError, ValueError, TypeError) as exc:
            # Attach the evidence that distinguishes "cut off mid-JSON" from
            # "complete but schema-invalid". Without finish_reason these are
            # indistinguishable downstream, and both used to surface as a
            # bare "escalated_parse_failure" that could not be diagnosed
            # after the fact. Reading these off the response must never
            # itself raise - a failure to describe a failure would hide it.
            error = LLMResponseError(
                f"response did not match {response_schema.__name__}: {exc}",
                finish_reason=_finish_reason_of(response),
                raw_text=_raw_text_of(response),
                model_name=self.model_name,
                schema_name=response_schema.__name__,
                usage=_usage_of(response),
            )
            print(f"[gemini] parse failure: {error.diagnostic_detail()}")
            raise error from exc
