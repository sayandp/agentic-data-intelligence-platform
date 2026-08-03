"""Intent classification cache. Reuses app/diagnosis/cache.py::DiagnosisCache
directly (a distinct class only so the default file path doesn't collide
with the diagnosis/query caches), keyed on (provider, model, source schema
hash, normalized question) - same reasoning as app/query/cache.py: a
fake-LLM run and a real Gemini run over the same question must never
collide, and a schema change is a cache miss, not a stale hit.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.diagnosis.cache import DiagnosisCache
from app.query.cache import cache_key_for_query, normalize_question, schema_hash  # noqa: F401 - re-exported for callers

DEFAULT_MODELING_CACHE_PATH = ".cache/modeling.json"


class ModelingCache(DiagnosisCache):
    def __init__(self, path: str | Path | None = None):
        super().__init__(path=path or os.environ.get("MODELING_CACHE_PATH", DEFAULT_MODELING_CACHE_PATH))
