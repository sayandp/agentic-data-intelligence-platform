"""FastAPI dependency for the Modeling Agent - mirrors
app/query/dependency.py exactly: construction failure is caught and
degraded to None rather than left to propagate as a 500. Losing the LLM
must not lose the modelling capability (Part 7's template/degrade rule): a
request that explicitly names a target_column still trains with
modeling_agent=None; only LLM-driven intent classification is unavailable.
"""

from __future__ import annotations

from app.llm.factory import get_llm_client
from app.modeling.agent import ModelingAgent


def get_modeling_agent() -> ModelingAgent | None:
    try:
        return ModelingAgent(llm_client=get_llm_client())
    except Exception:
        return None
