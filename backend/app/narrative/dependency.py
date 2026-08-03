"""FastAPI dependency for the Narrative Agent.

Unlike app/diagnosis/dependency.py, construction failure here is caught and
degraded to None rather than left to propagate as a 500 - Part 4's mandate
("the run must never fail for lack of an LLM") has to hold even when the
LLM client can't be constructed at all (missing API key, unknown
provider), not just when a call to it fails at runtime. app/narrative/pipeline.py
treats narrative_agent=None identically to any other total-LLM-failure
path: straight to the deterministic template.
"""

from __future__ import annotations

from app.llm.factory import get_llm_client
from app.narrative.agent import NarrativeAgent


def get_narrative_agent() -> NarrativeAgent | None:
    try:
        return NarrativeAgent(llm_client=get_llm_client())
    except Exception:
        return None
