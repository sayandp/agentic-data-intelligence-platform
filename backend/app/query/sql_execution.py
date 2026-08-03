"""Part 3, SQL path: executes already-validated SQL against a genuinely
read-only connection (app/query/readonly_db.py). Only ever called with SQL
that has already passed app/query/sql_validation.py - this module does not
re-validate, it executes what it's given, on a connection incapable of
writing regardless of what the text says.

Timeout is enforced two ways: a driver-level statement timeout where the
dialect supports one (Postgres/MySQL genuinely abort the remote query, not
just stop waiting for it - the far stronger guarantee), and a wall-clock
wrapper around the whole call on every dialect (including SQLite, which has
no native statement timeout) so the API-level guarantee is uniform even
where the stronger one isn't available.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

from app.query.readonly_db import read_only_connection

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass
class SQLExecutionResult:
    success: bool
    dataframe: pd.DataFrame | None = None
    error: str | None = None
    timed_out: bool = False


def _apply_statement_timeout(conn, dialect_name: str, timeout_seconds: float) -> None:
    timeout_ms = int(timeout_seconds * 1000)
    if dialect_name == "postgresql":
        conn.execute(text(f"SET statement_timeout = {timeout_ms}"))
    elif dialect_name == "mysql":
        conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}"))
    # sqlite has no native statement timeout - the wall-clock wrapper below
    # is the only guard for that dialect.


def run_sql_readonly(engine: sa.Engine, limited_sql: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> SQLExecutionResult:
    result_box: dict = {}

    def _run() -> None:
        try:
            with read_only_connection(engine) as conn:
                _apply_statement_timeout(conn, engine.dialect.name, timeout_seconds)
                result_box["dataframe"] = pd.read_sql_query(text(limited_sql), conn)
        except Exception as exc:  # noqa: BLE001 - any driver/DB exception becomes a clean escalation, never a crash
            result_box["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        # A Python thread can't be forcibly killed, but the API-level
        # contract is honored either way: the caller gets a clean timeout
        # signal now rather than blocking the request on a connection that
        # (for a dialect with no statement timeout) may still be running.
        return SQLExecutionResult(success=False, error="query exceeded the wall-clock timeout", timed_out=True)

    if "error" in result_box:
        return SQLExecutionResult(success=False, error=result_box["error"])
    return SQLExecutionResult(success=True, dataframe=result_box["dataframe"])
