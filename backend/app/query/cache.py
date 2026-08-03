"""Query generation cache. Reuses app/diagnosis/cache.py::DiagnosisCache's
generic get/set/disk-persistence behavior directly (a distinct class only
so the default file path doesn't collide with the diagnosis cache's), keyed
on (provider, model, source schema hash, normalized question) per Part 6.

provider/model are part of the key for the exact reason the diagnosis
cache was corrected earlier this project: a fake-LLM run and a real Gemini
run over the same question must never collide, or whichever ran first
silently serves its result to the other forever. The schema hash means a
schema change (a column renamed, added, dropped since the cached answer
was generated) is a cache miss, not a stale hit against a shape that no
longer exists.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.diagnosis.cache import DiagnosisCache

DEFAULT_QUERY_CACHE_PATH = ".cache/queries.json"


def normalize_question(question: str) -> str:
    """Whitespace/case-insensitive - 'What were total sales?' and '  what
    were   total sales?  ' are the same cache entry."""
    return " ".join(question.strip().lower().split())


def schema_hash(schema: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode("utf-8")).hexdigest()


def cache_key_for_query(provider: str, model: str, schema: dict[str, str], question: str) -> str:
    combined = f"{provider}|{model}|{schema_hash(schema)}|{normalize_question(question)}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


class QueryCache(DiagnosisCache):
    def __init__(self, path: str | Path | None = None):
        super().__init__(path=path or os.environ.get("QUERY_CACHE_PATH", DEFAULT_QUERY_CACHE_PATH))
