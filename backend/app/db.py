from __future__ import annotations

import os
import threading
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
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


# ---------------------------------------------------------------------------
# No pool connection may be held across a network call.
#
# A session that has queried holds a pooled connection until its transaction
# ends. narrate_node used to query, then make its model calls - with
# rate-limit backoff - and only release the connection at the final commit.
# Enough overlapping runs drained SQLAlchemy's default pool (5 + 10): a plain
# status request waited 30s and returned 500, and an ingest FAILED with
# `QueuePool limit ... reached`. Raising the pool size would only move that
# number; the fix is that nothing waits on the network while holding one.
#
# This records which thread checked out each connection, so a test can assert
# the count is ZERO at the moment of a model call - a precise check at the
# call itself, rather than an inference from whether a pool happened to run
# dry under some particular load.
# ---------------------------------------------------------------------------

_checked_out: dict[int, int] = {}
_checked_out_lock = threading.Lock()


def track_connections(target_engine) -> None:
    """Attach the checkout tracker to an engine. Idempotent per engine."""
    if getattr(target_engine, "_tracks_connections", False):
        return

    @event.listens_for(target_engine, "checkout")
    def _on_checkout(dbapi_connection, connection_record, connection_proxy):
        with _checked_out_lock:
            _checked_out[id(connection_record)] = threading.get_ident()

    @event.listens_for(target_engine, "checkin")
    def _on_checkin(dbapi_connection, connection_record):
        # Removed by record, not by the current thread: a check-in can
        # happen on a different thread from the checkout.
        with _checked_out_lock:
            _checked_out.pop(id(connection_record), None)

    target_engine._tracks_connections = True


def connections_held_by_current_thread() -> int:
    me = threading.get_ident()
    with _checked_out_lock:
        return sum(1 for owner in _checked_out.values() if owner == me)


def release_connection(db: Session | None) -> None:
    """End `db`'s transaction so its pooled connection goes back BEFORE a
    network call, for a session the caller does not own and so cannot close
    (a request's session, carried through a query or prediction graph).

    Refuses if anything is pending. Committing here would write that pending
    state early - before the model has answered, and outside the transaction
    its author meant it to be in. That is a bug in the caller, and it is
    raised rather than committed quietly.
    """
    if db is None:
        # No session, nothing held - a graph node unit-tested without a
        # database passes None through.
        return
    pending = [*db.new, *db.dirty, *db.deleted]
    if pending:
        kinds = sorted({type(obj).__name__ for obj in pending})
        raise RuntimeError(
            f"release_connection: {len(pending)} pending change(s) ({', '.join(kinds)}) would be committed early, "
            "before a network call"
        )
    db.commit()


track_connections(engine)


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
        _add_column_if_missing(conn, "runs", "privacy_classification", "JSON")
        _add_column_if_missing(conn, "confirmed_column_roles", "decision", "VARCHAR")
        _add_column_if_missing(conn, "confirmed_column_roles", "confirmed_by", "VARCHAR")
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
