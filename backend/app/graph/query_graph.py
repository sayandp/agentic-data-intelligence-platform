"""Phase 8 Part 0's approved, deliberately narrow scope for a query
subgraph: "do this only because they have real conditional branching
(valid -> execute, invalid -> escalate, unanswerable -> respond). Do not
invest beyond that." Three nodes, matching the phase boundaries
app/query/pipeline.py::ask_question already had - generate, then validate
(every Part 4 escalation check, in the SAME fixed order, unchanged), then
execute - each still calling the exact same generation/validation/sandbox
functions Phase 6 built and tested. This reorganizes WHERE the branching
lives, never what it decides; not one individual check became its own
node, since a node per one-line "if X: escalate" would be decoration, not
orchestration.

No checkpointer, unlike app/graph/build.py's ingest graph: Part 0 already
settled that approving an escalated query is a fresh re-validate-and-rerun
(app/query/pipeline.py::resolve_escalated_query_approval), never a resumed
graph invocation - there is no suspended position that would ever need to
survive a process restart, so state here carries live Python objects
(the DataContract, the DB session) freely rather than only IDs. Rule C
(checkpoint stores position only) was about the checkpointed ingest graph
specifically; a subgraph with nothing checkpointed has nothing to keep
that rule about.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.query.models import QueryAnswerStatus, QueryKind
from app.privacy.classification import PrivacyClassification
from app.privacy.redaction import POLICY_BY_PATH, redact_records
from app.semantic_roles import prompt_context
from app.query.pandas_validation import validate_pandas_code
from app.query.sandbox import run_pandas_sandbox
from app.query.sql_validation import validate_sql


class QueryState(TypedDict, total=False):
    db: Any
    query_agent: Any
    run: Any
    source: Any
    question: str
    confidence_threshold: float
    row_limit: int
    sandbox_timeout_seconds: float
    sandbox_memory_limit_bytes: int
    sandbox_row_cap: int
    quality_summary: str
    schema: dict
    contract: Any
    query_kind: QueryKind
    table_name: str | None
    allowed_tables: set
    sql_dialect: str
    findings_summary: list
    sample_rows: list
    generated: Any
    result: Any
    route: str


def generate_node(state: QueryState, config) -> dict:
    from app.query.pipeline import _persist, _sql_validation_inputs, MAX_SAMPLE_ROWS, _findings_summary_for_run

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    contract, schema, query_kind = state["contract"], state["schema"], state["query_kind"]
    query_agent = state["query_agent"]

    if query_agent is None:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED,
            escalation_reason="llm_unavailable",
            escalation_detail="no LLM is configured/reachable for query generation - ask again once one is available",
        )
        return {"result": result, "route": "done"}

    table_name: str | None = None
    allowed_tables: set = set()
    sql_dialect = "postgres"
    if query_kind == QueryKind.SQL:
        table_name, allowed_tables, sql_dialect = _sql_validation_inputs(source)

    findings_summary = _findings_summary_for_run(db, run)
    # THE EGRESS BOUNDARY for the query path.
    sample_rows = redact_records(
        contract.data.head(MAX_SAMPLE_ROWS).to_dict(orient="records"),
        PrivacyClassification.from_dict(state["run"].privacy_classification),
        POLICY_BY_PATH["query"],
    )

    # Roles are CONTEXT for the model, from the run's one detection pass.
    outcome = query_agent.generate(
        query_kind,
        question,
        schema,
        sample_rows,
        findings_summary,
        table_name=table_name,
        column_roles=prompt_context(state["run"].semantic_roles),
    )
    if outcome.query is None:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED,
            escalation_reason="llm_unavailable",
            escalation_detail=f"query generation failed: {outcome.source}",
        )
        return {"result": result, "route": "done"}

    return {
        "generated": outcome.query,
        "table_name": table_name,
        "allowed_tables": allowed_tables,
        "sql_dialect": sql_dialect,
        "route": "validate",
    }


def validate_node(state: QueryState, config) -> dict:
    from app.query.pipeline import _persist

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    schema, query_kind = state["schema"], state["query_kind"]
    generated = state["generated"]

    if generated.query_kind == QueryKind.UNANSWERABLE:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason="unanswerable",
            escalation_detail="the model reported this question cannot be answered from the given schema and findings",
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    if generated.query_kind != query_kind:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind, code=generated.code,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason="kind_mismatch",
            escalation_detail=f"expected query_kind={query_kind.value!r} (assigned deterministically by source type), got {generated.query_kind.value!r}",
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    unknown_columns = sorted(set(generated.columns_referenced) - set(schema))
    if unknown_columns:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind, code=generated.code,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason="unknown_column",
            escalation_detail=f"references column(s) absent from the live schema: {unknown_columns}",
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    if query_kind == QueryKind.SQL:
        validation = validate_sql(
            generated.code, state["allowed_tables"], set(schema), row_limit=state["row_limit"], dialect=state["sql_dialect"]
        )
    else:
        validation = validate_pandas_code(generated.code)

    if not validation.valid:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind, code=generated.code,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason="static_validation_failed",
            escalation_detail="; ".join(validation.errors),
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    if generated.confidence < state["confidence_threshold"]:
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind, code=generated.code,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason="low_confidence",
            escalation_detail=f"confidence {generated.confidence:.2f} is below the threshold {state['confidence_threshold']:.2f}",
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    return {"validated_code": validation.limited_sql if query_kind == QueryKind.SQL else None, "route": "execute"}


def execute_node(state: QueryState, config) -> dict:
    from app.query.pipeline import _ExecOutcome, _execute_sql, _persist

    db, run, source = state["db"], state["run"], state["source"]
    question, quality_summary = state["question"], state["quality_summary"]
    query_kind, generated = state["query_kind"], state["generated"]
    contract = state["contract"]

    if query_kind == QueryKind.SQL:
        limited_sql = state.get("validated_code") or generated.code
        exec_result = _execute_sql(source, limited_sql, state["sandbox_timeout_seconds"])
    else:
        sandbox_result = run_pandas_sandbox(
            generated.code, contract.data,
            timeout_seconds=state["sandbox_timeout_seconds"],
            memory_limit_bytes=state["sandbox_memory_limit_bytes"],
            row_cap=state["sandbox_row_cap"],
        )
        exec_result = _ExecOutcome(
            success=sandbox_result.success, value=sandbox_result.value, truncated=sandbox_result.truncated,
            row_count=sandbox_result.row_count, error=sandbox_result.error,
            timed_out=sandbox_result.timed_out, killed=sandbox_result.memory_killed,
        )

    if not exec_result.success:
        reason = "execution_timeout" if exec_result.timed_out else ("execution_killed" if exec_result.killed else "execution_failed")
        result = _persist(
            db, run, source, question, quality_summary,
            status=QueryAnswerStatus.ESCALATED, query_kind=generated.query_kind, code=generated.code,
            columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
            escalation_reason=reason,
            escalation_detail=exec_result.error or reason,
            awaiting_approval=True,
        )
        return {"result": result, "route": "done"}

    result = _persist(
        db, run, source, question, quality_summary,
        status=QueryAnswerStatus.ANSWERED, query_kind=generated.query_kind, code=generated.code,
        columns_referenced=generated.columns_referenced, assumptions=generated.assumptions, confidence=generated.confidence,
        result=exec_result.value, truncated=exec_result.truncated, row_count=exec_result.row_count,
    )
    return {"result": result, "route": "done"}


def _route(state: QueryState) -> str:
    return state["route"]


def build_query_graph():
    graph = StateGraph(QueryState)
    graph.add_node("generate", generate_node)
    graph.add_node("validate", validate_node)
    graph.add_node("execute", execute_node)
    graph.set_entry_point("generate")
    graph.add_conditional_edges("generate", _route, {"validate": "validate", "done": END})
    graph.add_conditional_edges("validate", _route, {"execute": "execute", "done": END})
    graph.add_edge("execute", END)
    return graph.compile()


_compiled_query_graph = None


def get_query_graph():
    global _compiled_query_graph
    if _compiled_query_graph is None:
        _compiled_query_graph = build_query_graph()
    return _compiled_query_graph
