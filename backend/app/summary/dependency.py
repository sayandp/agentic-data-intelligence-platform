"""FastAPI dependency for the Session Summary Agent.

Construction failure degrades to None rather than propagating, exactly like
app/narrative/dependency.py: a run must never fail for lack of an LLM, and
app/summary/pipeline.py treats agent=None as the template path with the reason
recorded.
"""

from __future__ import annotations

from app.llm.factory import get_llm_client
from app.summary.agent import SessionSummaryAgent


def get_summary_agent() -> SessionSummaryAgent | None:
    try:
        return SessionSummaryAgent(llm_client=get_llm_client())
    except Exception:  # noqa: BLE001 - no LLM is a supported mode, not an error
        return None
