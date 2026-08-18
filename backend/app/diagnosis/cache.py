"""Disk-persisted diagnosis cache, keyed by (provider, model, rule_failed,
column, detail) across ALL member events of a correlated group - not a
single (rule, column) pair. A rename pair's cache key covers both the
missing_column and unexpected_column events together, since they're
diagnosed as one unit; two different rename pairs (different columns, or
the same columns but a different underlying corruption) must never
collide.

provider and model are part of the key, not just the failure signature -
without them, a FakeLLMClient run and a real Gemini run over the same
corruption produce the SAME key, and whichever ran first silently serves
its (possibly fake, possibly wrong-model) result to the other forever.
Swapping LLM_PROVIDER, or moving from gemini-2.5-flash-lite to
gemini-2.5-flash, must be a cache miss, not a stale hit.

Exists so repeated development/evaluation runs over the same corruption
don't re-spend free-tier quota.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.validation.engine import ValidationFailure

DEFAULT_CACHE_PATH = ".cache/diagnoses.json"


def _stable_detail(detail: dict | None) -> str:
    return json.dumps(detail or {}, sort_keys=True, default=str)


#: Bump this whenever the diagnosis PROMPT changes in a way that could change
#: the answer - new context, different framing, a different sample.
#:
#: The key below is derived from the failure GROUP, not the prompt text, which
#: is what makes it stable across runs. The cost of that is real: when egress
#: redaction was added, the prompt changed (values became tokens) while the key
#: did not, so pre-redaction answers were replayed for redacted prompts and a
#: measurement of "did redaction change the diagnosis" silently compared a
#: cached answer against itself. Found in exactly that way.
#:
#: Generic on purpose: any future prompt change would have the same problem.
#: Bumping this invalidates every entry, which is the correct and cheap
#: response to "the question we asked is no longer the same question".
#:
#:   1 - original prompt
#:   2 - egress redaction: sample values arrive as stable tokens
PROMPT_VERSION = 2


def cache_key_for_group(
    members: list[ValidationFailure],
    provider: str,
    model: str,
    prompt_version: int = PROMPT_VERSION,
) -> str:
    """One key per (provider, model, prompt version, correlated group).

    Order-independent over the group's members (sorted), so the same group
    produces the same key regardless of member ordering - but never
    order-independent of provider, model or PROMPT VERSION, because a
    different model, or the same model asked a different question, is a
    different diagnosis rather than a cache hit.
    """
    parts = sorted(f"{m.rule_failed}|{m.column}|{_stable_detail(m.detail)}" for m in members)
    combined = f"v{prompt_version}|{provider}|{model}||" + "||".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


class DiagnosisCache:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path or os.environ.get("DIAGNOSIS_CACHE_PATH", DEFAULT_CACHE_PATH))
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, diagnosis: dict) -> None:
        self._data[key] = diagnosis
        self._save()
