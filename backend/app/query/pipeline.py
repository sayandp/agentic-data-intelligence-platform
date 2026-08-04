"""Part 4/5: orchestrates the Query Agent end to end - resolve the run,
deterministically assign query_kind by source type, generate, run every
Part 4 escalation check IN A FIXED ORDER, execute in the sandbox/read-only
connection, and persist a QueryRun. Every path through ask_question ends in
a QueryRun row, never an exception and never a retry-with-the-error-message
(a validation or execution failure is ALWAYS an escalation, never a second
generation attempt against the same question).

Check order matters: static validation always runs before the confidence
check, even for a low-confidence answer, because LOW_CONFIDENCE is the only
escalation reason a human can later "approve and run" (app/routers/
approvals.py) - that can only ever be safe if the code it would run has
already passed validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.connectors.credentials import resolve_env_var
from app.connectors.factory import build_connector
from app.connectors.sql_connector import SQLConnectorConfig, _extract_single_table
from app.contract import SourceType
from app.exploration.findings import DataQualityContext, ExplorationFindings
from app.id_lookup import find_run
from app.models import Baseline, DataSource, ExplorationFinding, QueryRun, Run
from app.narrative.quality import render_quality_context_summary
from app.query.agent import QueryAgent
from app.query.models import QueryAnswerStatus, QueryKind
from app.query.pandas_validation import validate_pandas_code
from app.query.sandbox import (
    DEFAULT_MEMORY_LIMIT_BYTES,
    DEFAULT_ROW_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    run_pandas_sandbox,
    serialize_dataframe,
)
from app.query.sql_execution import run_sql_readonly
from app.query.sql_validation import DEFAULT_ROW_LIMIT, validate_sql
from app.repair import repaired_contract_for_run
from app.state_machine import AWAITING_APPROVAL, REJECTED, RESOLVED, transition

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
MAX_SAMPLE_ROWS = 5

# sqlglot's dialect names diverge from SQLAlchemy's for two of the three
# read-only-supported dialects; everything else (sqlite included) matches.
_SQLALCHEMY_TO_SQLGLOT_DIALECT = {"postgresql": "postgres"}


def resolve_run(db: Session, source_id: str | None, run_id: str | None) -> Run:
    """`run_id` accepts a full UUID OR a run_number ("48"/"#48") - the exact
    same forms Reports/Audit/ingest-status accept, via the one shared
    matcher in app/id_lookup.py::find_run. This previously did a bare
    db.get(Run, run_id) (primary key only), so a run number typed on the
    Ask/Predict screens fell through to "run '48' not found" even when run
    #48 existed and its report rendered fine - the run_number lookup added
    for the paste-an-id boxes was never wired into the two pages that take a
    run as part of a larger payload."""
    if run_id:
        run = find_run(db, run_id)
        if run is None:
            raise ValueError(f"run '{run_id}' not found")
        return run
    if source_id:
        run = (
            db.query(Run)
            .filter(Run.source_id == source_id, Run.status == "completed")
            .order_by(Run.completed_at.desc())
            .first()
        )
        if run is None:
            raise ValueError(f"no completed run available for source '{source_id}'")
        return run
    raise ValueError("either source_id or run_id is required")


def _sqlglot_dialect_for_source(source: DataSource, sql_config: SQLConnectorConfig) -> str:
    """create_engine is lazy - reading .dialect.name never opens a
    connection, so this is cheap to call before generation even starts."""
    engine = sa.create_engine(resolve_env_var(sql_config.connection_string_env))
    try:
        name = engine.dialect.name
    finally:
        engine.dispose()
    return _SQLALCHEMY_TO_SQLGLOT_DIALECT.get(name, name)


def _sql_validation_inputs(source: DataSource) -> tuple[str | None, set[str], str]:
    """(table_name, allowed_tables, sqlglot dialect) for a SQL source -
    shared by ask_question (generation-time validation) and
    resolve_escalated_query_approval (re-validation at approval time), so
    the two never derive this differently."""
    sql_config = SQLConnectorConfig.model_validate(source.connection_config)
    table_name = sql_config.table or _extract_single_table(sql_config.query)
    allowed_tables = {table_name} if table_name else set()
    dialect = _sqlglot_dialect_for_source(source, sql_config)
    return table_name, allowed_tables, dialect


def _quality_context_for_run(db: Session, run: Run) -> DataQualityContext:
    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    if record is None:
        return DataQualityContext(total_events=0)
    return ExplorationFindings.model_validate(record.findings_json).data_quality_context


def _findings_summary_for_run(db: Session, run: Run) -> list[str]:
    record = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run.id).one_or_none()
    if record is None:
        return []
    findings = ExplorationFindings.model_validate(record.findings_json)
    return [f"{f.finding_type.value} on column(s) {f.columns}: {f.payload!r}" for f in findings.findings]


def _persist(
    db: Session,
    run: Run,
    source: DataSource,
    question: str,
    quality_summary: str,
    *,
    status: QueryAnswerStatus,
    query_kind: QueryKind | None = None,
    code: str | None = None,
    columns_referenced: list[str] | None = None,
    assumptions: list[str] | None = None,
    confidence: float | None = None,
    escalation_reason: str | None = None,
    escalation_detail: str | None = None,
    result: dict | None = None,
    truncated: bool = False,
    row_count: int | None = None,
    awaiting_approval: bool = False,
) -> QueryRun:
    record = QueryRun(
        source_id=source.id,
        run_id=run.id,
        question=question,
        query_kind=query_kind.value if query_kind is not None else None,
        generated_code=code,
        columns_referenced_json=columns_referenced,
        assumptions_json=assumptions,
        confidence=confidence,
        state=AWAITING_APPROVAL if awaiting_approval else ("answered" if status == QueryAnswerStatus.ANSWERED else REJECTED),
        escalation_reason=escalation_reason,
        escalation_detail=escalation_detail,
        result_json=result,
        truncated=truncated,
        row_count=row_count,
        quality_context_summary=quality_summary,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def ask_question(
    db: Session,
    query_agent: QueryAgent | None,
    source_id: str | None = None,
    run_id: str | None = None,
    question: str = "",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    row_limit: int = DEFAULT_ROW_LIMIT,
    sandbox_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    sandbox_memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    sandbox_row_cap: int = DEFAULT_ROW_CAP,
) -> QueryRun:
    """The single entry point app/routers/query.py calls. Raises ValueError
    only for a bad source_id/run_id (the router turns that into a 404) -
    every LLM/validation/execution failure past that point is captured as an
    escalated QueryRun, never an exception.

    resolve_run/contract-building below is unconditional setup, not a
    decision point, so it stays here rather than becoming a graph node -
    everything that actually BRANCHES (generate -> validate -> execute, or
    an early escalation at any of those points) lives in
    app/graph/query_graph.py (Phase 8 Part 1)."""
    from app.graph.query_graph import get_query_graph

    run = resolve_run(db, source_id, run_id)
    source = db.get(DataSource, run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    connector = build_connector(source)
    contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)

    quality_summary = render_quality_context_summary(_quality_context_for_run(db, run))
    schema = contract.column_types
    query_kind = QueryKind.SQL if contract.source_type == SourceType.SQL else QueryKind.PANDAS

    final_state = get_query_graph().invoke(
        {
            "db": db,
            "query_agent": query_agent,
            "run": run,
            "source": source,
            "question": question,
            "confidence_threshold": confidence_threshold,
            "row_limit": row_limit,
            "sandbox_timeout_seconds": sandbox_timeout_seconds,
            "sandbox_memory_limit_bytes": sandbox_memory_limit_bytes,
            "sandbox_row_cap": sandbox_row_cap,
            "quality_summary": quality_summary,
            "schema": schema,
            "contract": contract,
            "query_kind": query_kind,
        }
    )
    return final_state["result"]


@dataclass
class _ExecOutcome:
    """Shared execution-result shape for the SQL and pandas paths, so
    ask_question's post-execution handling doesn't branch on query_kind."""

    success: bool
    value: dict | None = None
    truncated: bool = False
    row_count: int | None = None
    error: str | None = None
    timed_out: bool = False
    killed: bool = False


def _execute_sql(source: DataSource, limited_sql: str, timeout_seconds: float) -> _ExecOutcome:
    """run_sql_readonly runs the connection/query inside its own worker
    thread and already turns every exception raised there - including
    read_only_connection's UnsupportedReadOnlyDialectError - into a clean
    SQLExecutionResult(success=False, error=...); nothing escapes as a raw
    exception here."""
    sql_config = SQLConnectorConfig.model_validate(source.connection_config)
    connection_string = resolve_env_var(sql_config.connection_string_env)
    engine = sa.create_engine(connection_string)
    try:
        result = run_sql_readonly(engine, limited_sql, timeout_seconds=timeout_seconds)
    finally:
        engine.dispose()

    if not result.success:
        return _ExecOutcome(success=False, error=result.error, timed_out=result.timed_out)

    serialized = serialize_dataframe(result.dataframe, DEFAULT_ROW_CAP)
    return _ExecOutcome(success=True, value=serialized["value"], truncated=serialized["truncated"], row_count=serialized["row_count"])


def resolve_escalated_query_approval(db: Session, query_run: QueryRun, resolved_by: str) -> QueryRun:
    """The `approve` decision for an escalated QueryRun (app/routers/
    approvals.py) - the caller restricts this to
    EscalationReason.LOW_CONFIDENCE (app/query/models.py::
    APPROVABLE_ESCALATION_REASONS), the only reason that leaves behind code
    which is already validated-safe and simply under-confident.

    Re-validates and re-derives the schema from scratch before running
    anything - the stored code is untrusted text the moment it's read back
    from the database, not a promise that still holds (the source's schema,
    or its live data, may have changed since the escalation was recorded).
    A human approval that no longer validates or fails to execute lands on
    rejected, exactly like a human-approved fix that fails post-condition
    verification (app/routers/approvals.py::_approve) - awaiting_approval
    can only legally end at resolved or rejected, never loop back."""
    run = db.get(Run, query_run.run_id)
    source = db.get(DataSource, query_run.source_id)
    baseline = db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
    connector = build_connector(source)
    contract = repaired_contract_for_run(run, source, connector, baseline.profile_json if baseline else None)
    query_kind = QueryKind(query_run.query_kind)
    code = query_run.generated_code

    if query_kind == QueryKind.SQL:
        _table_name, allowed_tables, dialect = _sql_validation_inputs(source)
        validation = validate_sql(code, allowed_tables, set(contract.column_types), dialect=dialect)
    else:
        validation = validate_pandas_code(code)

    if not validation.valid:
        return _reject_query_run(db, query_run, resolved_by, f"re-validation at approval time failed: {'; '.join(validation.errors)}")

    if query_kind == QueryKind.SQL:
        exec_result = _execute_sql(source, validation.limited_sql, DEFAULT_TIMEOUT_SECONDS)
    else:
        sandbox_result = run_pandas_sandbox(code, contract.data)
        exec_result = _ExecOutcome(
            success=sandbox_result.success, value=sandbox_result.value, truncated=sandbox_result.truncated,
            row_count=sandbox_result.row_count, error=sandbox_result.error,
            timed_out=sandbox_result.timed_out, killed=sandbox_result.memory_killed,
        )

    if not exec_result.success:
        return _reject_query_run(db, query_run, resolved_by, f"re-execution at approval time failed: {exec_result.error}")

    transition(query_run.state, RESOLVED)
    query_run.state = RESOLVED
    query_run.result_json = exec_result.value
    query_run.truncated = exec_result.truncated
    query_run.row_count = exec_result.row_count
    query_run.resolved_by = resolved_by
    query_run.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(query_run)
    return query_run


def _reject_query_run(db: Session, query_run: QueryRun, resolved_by: str, detail: str) -> QueryRun:
    transition(query_run.state, REJECTED)
    query_run.state = REJECTED
    query_run.escalation_detail = detail
    query_run.resolved_by = resolved_by
    query_run.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(query_run)
    return query_run


def reject_escalated_query(db: Session, query_run: QueryRun, resolved_by: str) -> QueryRun:
    """The `reject_fix` decision - acknowledges the escalation without
    running anything. Valid for every escalation reason, not just
    low_confidence (there is nothing TO run for the other reasons anyway)."""
    return _reject_query_run(db, query_run, resolved_by, query_run.escalation_detail or "escalation acknowledged, not run")
