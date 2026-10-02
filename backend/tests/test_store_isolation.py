"""Nothing a test run writes may land in a live store.

Three stores exist outside the app database: the app DB itself, the graph
checkpoint store, and run snapshots. The first two were given per-process
temp paths after a collision between two pytest processes was misreported as
a product regression three times. Run snapshots were missed: their default
path is the RELATIVE `.run_artifacts`, so a test run resolved it against the
working directory and wrote parquet files into the real
`backend/.run_artifacts`, next to snapshots belonging to live runs. A cleanup
found 12 of them there, none belonging to any run in the development
database.

These tests assert the isolation rather than the cleanup: a stray file that
gets deleted afterwards is still a file that was written into live data.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from app.run_snapshots import load_snapshot, save_snapshot, snapshot_path_for

REPO_BACKEND = Path(__file__).resolve().parent.parent


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_BACKEND.parent)
        return True
    except ValueError:
        return False


def test_every_store_a_test_writes_to_is_outside_the_repo():
    for name in ("DATABASE_URL", "GRAPH_CHECKPOINT_PATH", "RUN_ARTIFACTS_DIR"):
        value = os.environ.get(name)
        assert value, f"{name} is not set for tests - the default would be a live store"
        location = Path(value.replace("sqlite:///", ""))
        assert not _inside_repo(location), f"{name} points inside the repo: {location}"


def test_a_snapshot_written_by_a_test_lands_in_the_temp_directory(tmp_path):
    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    stored = Path(save_snapshot("run-under-test", frame))

    assert not _inside_repo(stored), f"a test wrote a snapshot into the repo: {stored}"
    assert stored == snapshot_path_for("run-under-test")
    # Still a working snapshot, not just a file somewhere harmless.
    pd.testing.assert_frame_equal(load_snapshot(str(stored)), frame)


def test_the_live_artifacts_directory_is_untouched_by_this_run():
    """The specific thing that went wrong: `.run_artifacts` resolved against
    the working directory, so `backend/.run_artifacts` collected test files."""
    save_snapshot("another-run-under-test", pd.DataFrame({"a": [1]}))

    live = REPO_BACKEND / ".run_artifacts"
    if live.exists():
        stray = [p.name for p in live.iterdir() if p.name.endswith("-under-test")]
        assert not stray, f"test snapshots were written into the live directory: {stray}"
