"""Run metadata is cached on the row, and must never be served stale.

_serialize_run_response used to rebuild the entire repaired frame just to
describe it - 30.89s on a 94MB source, against the metadata itself taking
0.00s - on an endpoint the UI POLLS and which the run picker also calls for
its column hints. It is read from Run.contract_metadata instead.

A cache of a derived thing is only safe if it dies when the thing changes.
The repaired frame is derived from Run.fix_chain, so the invariant these
tests hold is the direct one: whatever the endpoint reports must match the
frame the run ACTUALLY has right now - including for a run that escalated
and is sitting at awaiting_approval, which is exactly the state a human
sits polling and the one that never reaches explore_node's own refresh.
"""

from __future__ import annotations

from app.connectors.factory import build_connector
from app.db import SessionLocal
from app.models import Baseline, DataSource, Run
from app.repair import repaired_contract_for_run
from tests.corruption import CorruptionSuite
from tests.golden_scenarios import _clean_df, _create_source, _write_csv, ingest_and_wait


def _live_metadata(run_id: str) -> dict:
    """What the run's repaired frame says about itself, rebuilt from source
    - the expensive answer the cache is standing in for."""
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        source = db.get(DataSource, run.source_id)
        baseline = (
            db.query(Baseline).filter(Baseline.source_id == source.id, Baseline.is_active.is_(True)).one_or_none()
        )
        contract = repaired_contract_for_run(run, source, build_connector(source), baseline.profile_json if baseline else None)
        return contract.metadata()


def _cached_metadata(run_id: str) -> dict | None:
    with SessionLocal() as db:
        return db.get(Run, run_id).contract_metadata


def _compare(cached: dict, live: dict) -> None:
    """Everything that describes the FRAME. `ingestion_timestamp` is when
    the contract object was built, not a property of the data, so it
    legitimately differs between the cached copy and a fresh rebuild."""
    assert cached["row_count"] == live["row_count"]
    assert cached["column_types"] == live["column_types"]


def test_a_completed_run_serves_metadata_matching_its_repaired_frame(client, tmp_path):
    path = tmp_path / "clean.csv"
    _write_csv(path, _clean_df())
    source_id = _create_source(client, path)
    run = ingest_and_wait(client, source_id)

    cached = _cached_metadata(run["run_id"])
    assert cached is not None, "a completed run should have cached metadata"
    _compare(cached, _live_metadata(run["run_id"]))


def test_an_escalated_run_never_serves_metadata_from_before_its_fixes(client, tmp_path):
    """The stale window this closes. An escalated run never reaches
    explore_node, so a cache written at ingest time would describe the
    PRE-fix frame for as long as the run sat awaiting a human."""
    base = _clean_df()
    corrupted, _ = CorruptionSuite().apply(base, "inject_nulls", seed=1)
    path = tmp_path / "inject_nulls.csv"
    _write_csv(path, base)
    source_id = _create_source(client, path)
    ingest_and_wait(client, source_id)
    _write_csv(path, corrupted)
    escalated = ingest_and_wait(client, source_id)

    assert escalated["status"] == "awaiting_approval"
    _compare(_cached_metadata(escalated["run_id"]), _live_metadata(escalated["run_id"]))


def test_the_status_endpoint_reports_the_same_metadata_it_has_cached(client, tmp_path):
    """The cache is only worth having if the endpoint actually serves it."""
    path = tmp_path / "clean.csv"
    _write_csv(path, _clean_df())
    source_id = _create_source(client, path)
    run = ingest_and_wait(client, source_id)

    served = client.get(f"/ingest/{run['run_id']}/status").json()["metadata"]
    _compare(served, _live_metadata(run["run_id"]))


def test_a_run_with_no_cached_value_falls_back_to_the_live_rebuild(client, tmp_path):
    """Runs that completed before the column existed have no cache. They
    must keep working rather than losing the fields entirely."""
    path = tmp_path / "clean.csv"
    _write_csv(path, _clean_df())
    source_id = _create_source(client, path)
    run = ingest_and_wait(client, source_id)

    with SessionLocal() as db:
        row = db.get(Run, run["run_id"])
        row.contract_metadata = None
        db.commit()

    served = client.get(f"/ingest/{run['run_id']}/status").json()["metadata"]

    assert served["row_count"] == _live_metadata(run["run_id"])["row_count"]
    assert served["column_types"], "the fallback lost the column types"
