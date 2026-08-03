"""The ingest graph's typed state - deliberately minimal.

RULE C (single source of truth): this state carries IDENTIFIERS only -
run_id, source_id - never domain state (Run.status, ValidationEvent.state,
fix_chain) and never a DataFrame. Every node re-reads domain state fresh
from the database at the start of its own execution (Run/ValidationEvent/
Baseline rows), and reconstructs the working frame on demand via
app/repair.py's repaired_contract_for_run/base_contract_for_run - the same
helpers app/routers/approvals.py already used before this migration for
exactly this reason (_run_exploration_and_narrative_now,
_accept_as_baseline). The checkpointer LangGraph persists therefore only
ever stores GRAPH POSITION (which node is next, what this thread's state
dict contains) - never a second copy of anything Run/ValidationEvent
already owns. If a domain fact needs to survive a resume, it is written to
the database before the node returns, not carried in this dict.

`route` is the one working field nodes write and conditional-edge
functions read within a single step - it's re-derived by whichever node
just ran, never trusted across a resume (every node re-derives it fresh,
per Rule D - reconcile on resume, domain state wins over whatever the
checkpoint remembered).
"""

from __future__ import annotations

from typing import TypedDict


class IngestState(TypedDict, total=False):
    run_id: str
    source_id: str
    # Set by whichever node just ran; read by the conditional-edge function
    # that follows it in app/graph/build.py. Never read by a LATER node -
    # each node re-derives everything it needs from the database itself.
    route: str
