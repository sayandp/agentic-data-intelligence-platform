"""Immutable snapshots of the ingested frame, for runs that pause at
awaiting_approval against a mutable source (SQL, API).

The fix_chain design (app/repair.py) replays committed fixes against a base
frame rather than storing a mutated copy - that's correct and cheap when the
base is guaranteed reproducible (a file: same bytes, same frame, every
fetch). It quietly breaks for a mutable source: if a human approves a
pending fix an hour after ingestion, re-fetching a SQL table or an API
endpoint at that moment pulls whatever is there *now*, not what the
Diagnostic Agent actually saw. Verification could then pass for the wrong
reason, fail for an unrelated reason, or revert a fix that was actually
fine - silently, since nothing raises.

So: for any run that reaches awaiting_approval against a non-immutable
source (BaseConnector.source_immutable is False), the RAW ingested frame -
before any fixes - is snapshotted to Parquet here. Later replay
(app/routers/approvals.py) reads the snapshot instead of re-fetching, then
applies run.fix_chain on top exactly as it always did. Auto-apply during the
synchronous ingest request is unaffected - it already works from the
in-memory frame, and nothing has changed underneath it by the time it runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

DEFAULT_RUN_ARTIFACTS_DIR = ".run_artifacts"


def _artifacts_dir() -> Path:
    return Path(os.environ.get("RUN_ARTIFACTS_DIR", DEFAULT_RUN_ARTIFACTS_DIR))


def snapshot_path_for(run_id: str) -> Path:
    return _artifacts_dir() / run_id / "snapshot.parquet"


def save_snapshot(run_id: str, df: pd.DataFrame) -> str:
    path = snapshot_path_for(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return str(path)


def load_snapshot(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)
