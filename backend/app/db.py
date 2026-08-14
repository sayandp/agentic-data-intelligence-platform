from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_platform",
)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _add_column_if_missing(conn: Connection, table: str, column: str, ddl_type: str) -> None:
    """Base.metadata.create_all only creates MISSING TABLES - it never
    alters an existing one, so a column added to a model after real rows
    already exist (run_number, ValidationEvent.resolved_at) needs its own
    one-time ALTER TABLE here. `inspect` is dialect-agnostic (works
    identically against the SQLite file this project runs locally and the
    Postgres instance docker-compose.yml points at), and a plain, no-
    default, nullable column is valid ALTER TABLE ... ADD COLUMN syntax on
    both - the one thing that keeps this a single shared code path instead
    of a dialect branch."""
    existing = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _backfill_run_numbers(conn: Connection) -> None:
    """Dashboard UX pass, Part 1: every Run existing before run_number was
    introduced gets one, in creation order, the first time init_db() runs
    against a database whose runs table predates the column. `started_at` is
    a safe creation-order key here specifically because app/routers/
    ingest.py is the ONLY place a Run is ever constructed, and it always
    sets started_at at construction time - never left to a later step."""
    unnumbered = conn.execute(text("SELECT id FROM runs WHERE run_number IS NULL ORDER BY started_at, id")).fetchall()
    if not unnumbered:
        return
    next_number = (conn.execute(text("SELECT COALESCE(MAX(run_number), 0) FROM runs")).scalar() or 0) + 1
    for offset, (run_id,) in enumerate(unnumbered):
        conn.execute(text("UPDATE runs SET run_number = :n WHERE id = :run_id"), {"n": next_number + offset, "run_id": run_id})


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        _add_column_if_missing(conn, "runs", "run_number", "INTEGER")
        _add_column_if_missing(conn, "validation_events", "resolved_at", "TIMESTAMP")
        _add_column_if_missing(conn, "model_runs", "redirected_query_run_id", "TEXT")
        _add_column_if_missing(conn, "model_runs", "forecast_series_json", "JSON")
        _add_column_if_missing(conn, "runs", "contract_metadata", "JSON")
        _add_column_if_missing(conn, "runs", "semantic_roles", "JSON")
        _backfill_run_numbers(conn)
        # IF NOT EXISTS: idempotent across every startup, not just the
        # first one that actually needed to add the column above. This -
        # not the ORM-level unique=True, which SQLite only enforces via the
        # matching index create_all itself would emit for a BRAND NEW table -
        # is the real, always-present uniqueness guarantee for a column that
        # got here via ALTER TABLE on an existing one.
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_runs_run_number ON runs (run_number)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
