"""A run against a mutable source (SQL) that pauses at awaiting_approval
must be resolvable later against what was actually diagnosed, not whatever
the live table currently holds. app/run_snapshots.py is what makes that true;
this is the test that would catch a regression back to re-fetching.
"""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from app.diagnosis.agent import DiagnosticAgent
from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.main import app
from app.routers.ingest import get_diagnostic_agent
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache
from tests.golden_scenarios import ingest_and_wait, resolve_and_wait

# Confidence 0.5 is below the default gate threshold (0.8) - the rename is
# correctly diagnosed but the gate escalates rather than auto-applying, which
# is what puts the run into awaiting_approval for a human to resolve later.
LOW_CONFIDENCE_RENAME = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="looks like a rename but not confident enough to auto-apply",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.5,
)


@contextmanager
def _low_confidence_diagnosis():
    def _override():
        return DiagnosticAgent(
            llm_client=FakeLLMClient(default=LOW_CONFIDENCE_RENAME),
            cache=InMemoryDiagnosisCache(),
            sleep=lambda _s: None,
        )

    app.dependency_overrides[get_diagnostic_agent] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_diagnostic_agent, None)


def _clean_orders(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago"]
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "city": rng.choice(cities, size=n),
            "amount": rng.normal(100.0, 20.0, size=n).round(2).clip(min=1.0),
        }
    )


def _run_to_awaiting_approval(client, monkeypatch, tmp_path, table_label: str, clean_df, corrupted_df) -> tuple[str, str, str]:
    """Sets up a SQL source with its own SQLite table, ingests clean (baseline)
    then corrupted (-> awaiting_approval). Returns (source_id, run_id, sql_env_var)."""
    env_var = f"SNAPSHOT_TEST_DB_{table_label.upper()}"
    db_path = tmp_path / f"{table_label}.db"
    monkeypatch.setenv(env_var, f"sqlite:///{db_path.as_posix()}")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    clean_df.to_sql("orders", engine, if_exists="replace", index=False)
    engine.dispose()

    source_id = client.post(
        "/sources",
        json={"type": "sql", "connection_config": {"connection_string_env": env_var, "query": "select * from orders"}},
    ).json()["id"]
    ingest_and_wait(client, source_id)  # establishes baseline

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    corrupted_df.to_sql("orders", engine, if_exists="replace", index=False)
    engine.dispose()
    result = ingest_and_wait(client, source_id)
    assert result["status"] == "awaiting_approval"

    return source_id, result["run_id"], env_var


def test_approval_after_table_mutation_verifies_against_snapshot_not_live_data(client, monkeypatch, tmp_path):
    from app.db import SessionLocal
    from app.models import Run, ValidationEvent

    clean_df = _clean_orders()
    corrupted_df = clean_df.rename(columns={"city": "town"})

    with _low_confidence_diagnosis():
        control_source_id, control_run_id, _ = _run_to_awaiting_approval(
            client, monkeypatch, tmp_path, "control", clean_df, corrupted_df
        )
        mutated_source_id, mutated_run_id, mutated_env_var = _run_to_awaiting_approval(
            client, monkeypatch, tmp_path, "mutated", clean_df, corrupted_df
        )

        with SessionLocal() as db:
            run = db.get(Run, mutated_run_id)
            assert run.snapshot_path is not None  # SQL is source_immutable=False - must have snapshotted

        # Mutate the underlying table AFTER ingest, BEFORE approval: rename
        # "town" back to "city" directly in SQL, as if some other process
        # independently touched the live table. If approval re-fetched the
        # live table instead of the snapshot, the rename fix would find no
        # "town" column left to act on and FAIL verification - a completely
        # different, wrong outcome from approving before the mutation.
        import os

        engine = create_engine(os.environ[mutated_env_var])
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE orders RENAME COLUMN town TO city"))
            conn.commit()
        engine.dispose()

        with SessionLocal() as db:
            control_event_id = (
                db.query(ValidationEvent).filter(ValidationEvent.run_id == control_run_id).first().id
            )
            mutated_event_id = (
                db.query(ValidationEvent).filter(ValidationEvent.run_id == mutated_run_id).first().id
            )

        control_result = resolve_and_wait(client, control_event_id, "approve", "alice")
        mutated_result = resolve_and_wait(client, mutated_event_id, "approve", "alice")

    assert control_result["applied"] is True
    assert mutated_result["applied"] is True  # verified against the snapshot, unaffected by the mutation

    with SessionLocal() as db:
        control_events = db.query(ValidationEvent).filter(ValidationEvent.run_id == control_run_id).all()
        mutated_events = db.query(ValidationEvent).filter(ValidationEvent.run_id == mutated_run_id).all()

        control_summary = sorted((e.rule_failed, e.state, e.action_taken) for e in control_events)
        mutated_summary = sorted((e.rule_failed, e.state, e.action_taken) for e in mutated_events)
        assert control_summary == mutated_summary
        assert all(e.state == "resolved" and e.action_taken == "approved_and_applied" for e in mutated_events)

        control_run = db.get(Run, control_run_id)
        mutated_run = db.get(Run, mutated_run_id)
        assert control_run.fix_chain == mutated_run.fix_chain
        assert mutated_run.status == "completed"


def test_snapshot_not_taken_for_file_sources(client, tmp_path):
    """source_immutable=True for FileConnector - re-fetching is already
    reproducible, so there's nothing a snapshot would protect against."""
    from app.db import SessionLocal
    from app.models import Run

    csv_path = tmp_path / "orders.csv"
    _clean_orders().to_csv(csv_path, index=False)
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
    ingest_and_wait(client, source_id)

    corrupted = _clean_orders().rename(columns={"city": "town"})
    csv_path.write_text(corrupted.to_csv(index=False), encoding="utf-8")

    with _low_confidence_diagnosis():
        result = ingest_and_wait(client, source_id)

    assert result["status"] == "awaiting_approval"
    with SessionLocal() as db:
        run = db.get(Run, result["run_id"])
        assert run.snapshot_path is None
