"""Phase 7.5 - THE DELIVERABLE.

Every defect this phase fixes existed because nothing exercised the
pipeline on data shaped like real data: a transactional table with a
primary key, and a genuine date column arriving as ingested text (CSV/SQL
ingestion never coerces date-like text to datetime64 on its own). This test
ingests exactly that shape end to end and asserts the whole class of
defect stays fixed:

  - a baseline IS established (not rejected over the primary key)
  - the identifier column is excluded and recorded, not silently dropped
  - the date column is profiled and explored as DATETIME, not categorical
  - at least one trend finding IS emitted
  - Exploration and Modeling agree about the date column's kind in the
    same run
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tests.golden_scenarios import ingest_and_wait


def _make_olist_like_csv(tmp_path, n: int = 400, seed: int = 7) -> str:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="h")  # unique per row
    trend = np.linspace(50, 200, n)
    noise = rng.normal(0, 5, n)
    price = (trend + noise).round(2).clip(min=1.0)
    df = pd.DataFrame(
        {
            "order_id": [f"order_{i}" for i in range(n)],  # unique per row
            "order_purchase_timestamp": dates.astype(str),  # plain ISO text, exactly as a real CSV export arrives
            "customer_city": rng.choice(["New York", "Los Angeles", "Chicago"], size=n),
            "price": price,
            "review_score": rng.integers(1, 6, size=n),
        }
    )
    path = tmp_path / "olist_orders.csv"
    df.to_csv(path, index=False)
    return str(path)


def test_realistic_olist_shaped_ingest_end_to_end(client, tmp_path):
    from app.db import SessionLocal
    from app.exploration.columns import column_kind as exploration_column_kind
    from app.models import Baseline, ExplorationFinding

    csv_path = _make_olist_like_csv(tmp_path)
    source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": csv_path}}).json()["id"]

    body = ingest_and_wait(client, source_id)
    assert body["status"] == "completed"

    # -- a baseline IS established --
    assert body["baseline"] is not None
    assert body["baseline_rejected_reasons"] is None
    run_id = body["run_id"]

    with SessionLocal() as db:
        baseline = db.query(Baseline).filter(Baseline.source_id == source_id, Baseline.is_active.is_(True)).one()

        # -- the identifier column is excluded and recorded, not silently dropped --
        excluded = {e["column"]: e["reason"] for e in baseline.profile_json["excluded_columns"]}
        assert "order_id" in excluded
        assert "identifier" in excluded["order_id"]
        assert "order_id" not in baseline.profile_json["columns"]

        # -- the date column is profiled as DATETIME, not categorical --
        assert baseline.profile_json["columns"]["order_purchase_timestamp"]["kind"] == "datetime"
        assert "top_values" not in baseline.profile_json["columns"]["order_purchase_timestamp"]

        # -- at least one trend finding IS emitted --
        ef = db.query(ExplorationFinding).filter(ExplorationFinding.run_id == run_id).one()
        findings = ef.findings_json["findings"]
        trend_findings = [f for f in findings if f["finding_type"] == "trend"]
        assert len(trend_findings) >= 1
        assert trend_findings[0]["payload"]["datetime_column"] == "order_purchase_timestamp"

        # -- the datetime summary_stat payload is actually constructed --
        summary_findings = [f for f in findings if f["finding_type"] == "summary_stat"]
        ts_summary = next(f for f in summary_findings if f["columns"] == ["order_purchase_timestamp"])
        assert ts_summary["payload"]["kind"] == "datetime"
        assert ts_summary["payload"]["min"] is not None
        assert ts_summary["payload"]["max"] is not None
        assert ts_summary["payload"]["span_days"] is not None

    # -- exploration and modeling agree about the same column's kind --
    from app.connectors.factory import build_connector
    from app.modeling.config import ModelingConfig
    from app.modeling.models import TaskType
    from app.modeling.task_selection import select_task_type
    from app.models import DataSource, Run
    from app.repair import repaired_contract_for_run

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        source = db.get(DataSource, source_id)
        baseline = db.query(Baseline).filter(Baseline.source_id == source_id, Baseline.is_active.is_(True)).one()
        connector = build_connector(source)
        contract = repaired_contract_for_run(run, source, connector, baseline.profile_json)
        df = contract.data

        assert pd.api.types.is_datetime64_any_dtype(df["order_purchase_timestamp"])
        assert exploration_column_kind(df["order_purchase_timestamp"]).value == "datetime"

        task_type, dt_col = select_task_type(df, "price", ModelingConfig())
        assert task_type == TaskType.FORECAST
        assert dt_col == "order_purchase_timestamp"
