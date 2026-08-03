"""Part 2 SQL path, closing note: 'Execute on a READ-ONLY database
connection - a separate role or connection flag, not just a parse-time
promise. Structure over check: the connection must be incapable of
writing.'

sqlglot's validator (app/query/sql_validation.py) already refuses anything
that isn't a bare SELECT - this module is the second, independent layer:
even a validation bug, or a dialect construct sqlglot doesn't recognize as
a write, hits a connection that the database engine itself will not let
write, regardless of what SQL text reaches it.

Dialect-specific, deliberately: there is no single ANSI-standard "make this
connection read-only" statement that works identically on SQLite, Postgres,
and MySQL. An UNRECOGNIZED dialect is refused outright rather than executed
without this guarantee - the same "structure over check" call as
BaseConnector.source_immutable (Phase 3): no default to silently inherit
the wrong (unprotected) behavior.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

_SQLITE = "sqlite"
_POSTGRESQL = "postgresql"
_MYSQL = "mysql"

SUPPORTED_READONLY_DIALECTS = frozenset({_SQLITE, _POSTGRESQL, _MYSQL})


class UnsupportedReadOnlyDialectError(ValueError):
    def __init__(self, dialect_name: str):
        self.dialect_name = dialect_name
        super().__init__(
            f"'{dialect_name}' has no known read-only enforcement in this codebase "
            f"(supported: {sorted(SUPPORTED_READONLY_DIALECTS)}) - refusing to execute generated SQL "
            "against it rather than run without a genuinely read-only connection"
        )


def _enforce_read_only(conn: Connection, dialect_name: str) -> None:
    if dialect_name == _SQLITE:
        conn.execute(text("PRAGMA query_only = ON"))
    elif dialect_name == _POSTGRESQL:
        conn.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        conn.execute(text("SET default_transaction_read_only = on"))
    elif dialect_name == _MYSQL:
        conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
    else:
        raise UnsupportedReadOnlyDialectError(dialect_name)


@contextmanager
def read_only_connection(engine: Engine):
    """Yields a Connection that the database engine itself will refuse to
    write through - not a promise kept by this codebase's own discipline,
    a property of the connection/session state enforced by the database.
    Raises UnsupportedReadOnlyDialectError before opening anything if the
    dialect has no known enforcement mechanism here.
    """
    dialect_name = engine.dialect.name
    if dialect_name not in SUPPORTED_READONLY_DIALECTS:
        raise UnsupportedReadOnlyDialectError(dialect_name)

    with engine.connect() as conn:
        _enforce_read_only(conn, dialect_name)
        yield conn
