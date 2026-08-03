"""ValidationEvent lifecycle, enforced in code so states cannot be skipped.

    detected -> diagnosed -> (auto_fixed | awaiting_approval) -> (resolved | rejected)
    detected -> resolved   [Phase 7.5: acknowledged informational events only]

Part 4 (Diagnostic Agent) drives detected -> diagnosed, unconditionally -
even a failed diagnosis (malformed LLM response, quota exhaustion) still
produces a diagnosis_json (an error record) and transitions to "diagnosed";
the diagnosed -> (auto_fixed | awaiting_approval) decision belongs entirely
to Part 5's gate, not built yet, and a failure-shaped diagnosis is simply a
case the gate will always route to awaiting_approval.

The second edge (detected -> resolved, skipping diagnosed entirely) exists
only for connector-warning events (app/validation/engine.py::
CONNECTOR_WARNING) - informational by construction, never diagnosed, never
gated (there is no fix to apply or matrix to check). Acknowledging one via
app/routers/approvals.py is the only thing that ever takes this edge; every
other DETECTED event still goes through diagnosed as before.
"""

from __future__ import annotations

DETECTED = "detected"
DIAGNOSED = "diagnosed"
AUTO_FIXED = "auto_fixed"
AWAITING_APPROVAL = "awaiting_approval"
RESOLVED = "resolved"
REJECTED = "rejected"

VALID_TRANSITIONS: dict[str, set[str]] = {
    DETECTED: {DIAGNOSED, RESOLVED},
    DIAGNOSED: {AUTO_FIXED, AWAITING_APPROVAL},
    AUTO_FIXED: {RESOLVED, REJECTED},
    AWAITING_APPROVAL: {RESOLVED, REJECTED},
    RESOLVED: set(),
    REJECTED: set(),
}


class IllegalTransitionError(ValueError):
    def __init__(self, current_state: str, new_state: str):
        self.current_state = current_state
        self.new_state = new_state
        super().__init__(f"cannot transition from '{current_state}' to '{new_state}'")


def transition(current_state: str, new_state: str) -> None:
    """Raises IllegalTransitionError unless new_state is a valid next step
    from current_state. Callers apply new_state themselves after this
    doesn't raise - this function only validates, it doesn't mutate."""
    if new_state not in VALID_TRANSITIONS.get(current_state, set()):
        raise IllegalTransitionError(current_state, new_state)
