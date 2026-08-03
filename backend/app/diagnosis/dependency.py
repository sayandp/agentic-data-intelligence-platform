"""FastAPI dependency for the Diagnostic Agent, shared by every router that
needs to diagnose a validation_event group (ingest's synchronous auto-apply
path, approvals' human-approve path, and either path's do-no-harm reveal
follow-up - app/resolution.py). One function object so a single
`app.dependency_overrides[get_diagnostic_agent] = ...` in tests covers every
route that depends on it, regardless of which router declared the route.

Construction failure degrades to None here - the same pattern
app/narrative/dependency.py, app/query/dependency.py, and
app/modeling/dependency.py already use (missing API key, unknown provider).
A run must be fully usable with no LLM configured at all: every group that
would have been diagnosed instead escalates straight to awaiting_approval
(app/resolution.py::process_group treats diagnostic_agent=None as an
already-failed diagnosis, the same shape a 429 or a malformed response
produces) rather than the request 500ing before the route body ever runs."""

from __future__ import annotations

from app.diagnosis.agent import DiagnosticAgent
from app.llm.factory import get_llm_client


def get_diagnostic_agent() -> DiagnosticAgent | None:
    """Overridden in tests with a DiagnosticAgent wrapping a fake LLMClient -
    nothing in the test suite ever reaches a real provider."""
    try:
        return DiagnosticAgent(llm_client=get_llm_client())
    except Exception:
        return None
