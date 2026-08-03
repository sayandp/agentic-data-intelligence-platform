"""Constructs the graph's persistent checkpointer.

Deliberately a SEPARATE connection/store from the app's own SQLAlchemy
DATABASE_URL (app/db.py) - LangGraph's checkpointer manages its own schema
(checkpoints/writes tables) via its own driver (raw sqlite3 or psycopg, not
SQLAlchemy), and keeping it physically separate makes explicit what Rule C
already establishes logically: the checkpoint store and the domain
database are two different things that happen to both persist to disk,
not one shared source of truth.

GRAPH_CHECKPOINT_PATH (default: a sqlite file under .cache/, gitignored,
mirroring app/diagnosis/cache.py's DIAGNOSIS_CACHE_PATH convention) is used
unless GRAPH_CHECKPOINT_POSTGRES_URL is set, in which case Postgres is used
instead (docker-compose sets this in production; tests and local dev use
the sqlite default).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_CHECKPOINT_PATH = ".cache/graph_checkpoints.db"


def _sqlite_checkpointer(path: str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def _postgres_checkpointer(conn_string: str):
    from langgraph.checkpoint.postgres import PostgresSaver

    saver_cm = PostgresSaver.from_conn_string(conn_string)
    saver = saver_cm.__enter__()  # kept open for the process lifetime, mirroring app/db.py's module-level engine
    saver.setup()
    return saver


def build_checkpointer():
    postgres_url = os.environ.get("GRAPH_CHECKPOINT_POSTGRES_URL")
    if postgres_url:
        return _postgres_checkpointer(postgres_url)
    path = os.environ.get("GRAPH_CHECKPOINT_PATH", DEFAULT_CHECKPOINT_PATH)
    return _sqlite_checkpointer(path)
