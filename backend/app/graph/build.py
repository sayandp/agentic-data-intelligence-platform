"""Assembles the ingest StateGraph: nodes, conditional edges, and the
persistent checkpointer.

RULE B (every graph exit sets a terminal status), made structural: every
node is wrapped once, here, by `safe_node` - never per node. A node
raising for ANY reason is caught in this one place, the run is marked
"failed" with a trace recording why, and routing continues to the
`failed` terminal sink. A node added later automatically gets this
guarantee just by being registered through `_register`; there is no
separate step an author could forget.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, StateGraph

from app.db import SessionLocal
from app.graph.checkpointer import build_checkpointer
from app.graph.nodes import (
    await_human_node,
    explore_node,
    failed_node,
    ingest_node,
    narrate_node,
    summarise_node,
    resolve_node,
    validate_node,
)
from app.graph.state import IngestState
from app.models import AgentTrace, Run

NODE_FUNCS = {
    "ingest": ingest_node,
    "validate": validate_node,
    "resolve": resolve_node,
    "await_human": await_human_node,
    "explore": explore_node,
    "narrate": narrate_node,
    "summarise": summarise_node,
    "failed": failed_node,
}


def safe_node(name: str, fn):
    def wrapped(state, config):
        try:
            return fn(state, config)
        except GraphBubbleUp:
            # LangGraph's OWN control-flow signal (interrupt() raises a
            # GraphInterrupt, a GraphBubbleUp subclass, to unwind the stack
            # up to the graph runtime that actually implements the pause).
            # This is not a node failure - it is the await_human node
            # pausing exactly as designed. Catching Exception broadly below
            # would otherwise swallow it and turn every legitimate pause
            # into a "failed" run; re-raising is what keeps interrupt()
            # working at all through this wrapper.
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad otherwise: any GENUINE node failure degrades, never propagates
            run_id = state.get("run_id")
            if run_id:
                with SessionLocal() as db:
                    run = db.get(Run, run_id)
                    if run is not None and run.status not in ("completed", "failed"):
                        run.status = "failed"
                        run.completed_at = datetime.now(timezone.utc)
                    db.add(
                        AgentTrace(
                            run_id=run_id,
                            agent_name=name,
                            input_summary="node raised an exception",
                            output_summary=json.dumps({"error_type": type(exc).__name__, "error": str(exc)}),
                            edge_taken="failed",
                        )
                    )
                    db.commit()
            return {"route": "failed"}

    wrapped.__name__ = f"safe_{name}"
    return wrapped


def _route(state: IngestState) -> str:
    return state["route"]


def build_graph():
    graph = StateGraph(IngestState)
    for name, fn in NODE_FUNCS.items():
        graph.add_node(name, safe_node(name, fn))

    graph.set_entry_point("ingest")
    graph.add_conditional_edges("ingest", _route, {"validate": "validate", "explore": "explore", "failed": "failed"})
    graph.add_conditional_edges("validate", _route, {"resolve": "resolve", "explore": "explore", "failed": "failed"})
    graph.add_conditional_edges("resolve", _route, {"await_human": "await_human", "explore": "explore", "failed": "failed"})
    graph.add_conditional_edges("await_human", _route, {"resolve": "resolve", "failed": "failed"})
    graph.add_conditional_edges("explore", _route, {"narrate": "narrate", "done": END, "failed": "failed"})
    # The Session Summary Agent runs after narrate, because it summarises
    # the run's persisted artifacts and narrate is the last stage that
    # writes any. It never reads the exported deck.
    graph.add_edge("narrate", "summarise")
    graph.add_edge("summarise", END)
    graph.add_edge("failed", END)

    return graph.compile(checkpointer=build_checkpointer())


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def invoke_in_background(run_id: str, graph_input, config: dict) -> None:
    """Runs the graph OUTSIDE the request/response cycle - shared by
    POST /ingest (fresh start: graph_input is {"run_id", "source_id"}) and
    POST /approvals/{id}/resolve (resume: graph_input is a
    langgraph.types.Command) via FastAPI BackgroundTasks. Both can trigger
    narrate_node's LLM chain (up to 8 calls across two retry-with-backoff
    stages), and neither should hold an HTTP request open for however long
    that takes - see app/routers/ingest.py's module docstring for the
    original bug this pattern fixes, and app/routers/approvals.py's for
    the second instance of it (resolving an escalation resumes this SAME
    graph, so it had the identical problem).

    On an exception the graph itself didn't catch (defense in depth only -
    safe_node above already routes every node-level failure to the
    terminal "failed" sink), marks the run failed directly, since nothing
    else will from out here. Own session: whichever request's `db`
    triggered this is long gone by the time a background task runs."""
    graph = get_graph()
    try:
        graph.invoke(graph_input, config=config)
    except Exception:  # noqa: BLE001 - deliberately broad: this is the last line of defense, see docstring
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            if run is not None and run.status not in ("completed", "failed", "awaiting_approval"):
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()


def reset_graph() -> None:
    """Test-only: drops the cached compiled graph and closes its
    checkpointer's underlying connection, so the next get_graph() call
    rebuilds fresh against whatever GRAPH_CHECKPOINT_PATH currently points
    at - mirrors app/db.py's engine being recreated per test DB. Explicitly
    closing (not just dropping the Python reference) matters on Windows,
    where an open sqlite3 connection holds an OS-level file lock that
    would otherwise block the next test from deleting/replacing the file.
    Never called from production code."""
    global _compiled_graph
    if _compiled_graph is not None:
        checkpointer = getattr(_compiled_graph, "checkpointer", None)
        conn = getattr(checkpointer, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup, never block a test on it
                pass
    _compiled_graph = None
