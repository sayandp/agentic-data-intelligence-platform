"""FastAPI dependency for the Query Agent - mirrors
app/narrative/dependency.py exactly: construction failure is caught and
degraded to None rather than left to propagate as a 500. Part 6's mandate
("query generation unavailable... does NOT fail the service") has to hold
even when the LLM client can't be constructed at all (missing API key,
unknown provider), not just when a call to it fails at runtime.
app/query/pipeline.py treats query_agent=None identically to any other
total-LLM-failure path: a clear "query generation unavailable" response,
never a service failure.
"""

from __future__ import annotations

from app.llm.factory import get_llm_client
from app.query.agent import QueryAgent


def get_query_agent() -> QueryAgent | None:
    try:
        return QueryAgent(llm_client=get_llm_client())
    except Exception:
        return None
