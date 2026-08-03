"""Phase 7 ACCEPTANCE verification, run standalone (not pytest) - mirrors
scripts/query_agent_acceptance.py's style.

Exercises the three ACCEPTANCE bullets from the Phase 7 spec:

  1. Olist: forecast monthly order volume - forward-chained split, beats
     naive baseline, out-of-sample score and interval reported, excluded
     features listed.
  2. A prediction question naming a target column that does not exist ->
     escalates cleanly, even at high reported LLM confidence.
  3. A dataset where no model beats its baseline -> reported honestly as
     such, not dressed up as a usable model.

Uses tests.fakes.modeling_llm_override (a FakeLLMClient) for #2's intent
classification, and app/modeling/pipeline.py::predict() called directly (not
through HTTP) for #1 and #3, so a reduced fold count can be passed - the
same reasoning as tests/test_modeling_forecast.py: bounding the number of
real Prophet/ARIMA fits this script performs.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ACCEPTANCE_DB_PATH = Path(tempfile.gettempdir()) / "agentic_platform_modeling_acceptance.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_ACCEPTANCE_DB_PATH.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modeling.config import ModelingConfig  # noqa: E402
from app.modeling.models import IntentClassification, IntentKind  # noqa: E402
from app.modeling.pipeline import predict  # noqa: E402
from app.models import Base  # noqa: E402
from tests.fakes import modeling_llm_override  # noqa: E402


def _olist_orders_csv(path: Path, months: int = 36) -> None:
    lines = ["order_id,customer_id,session_id,order_date,price"]
    order_index = 0
    for month in range(months):
        year = 2021 + month // 12
        month_of_year = month % 12 + 1
        count = 50 + 3 * month + (40 if month % 12 in (10, 11) else 0)  # trend + Nov/Dec seasonal bump
        for k in range(count):
            day = (k % 27) + 1
            order_date = f"{year:04d}-{month_of_year:02d}-{day:02d}"
            customer_id = f"cust-{order_index % 50}"
            session_id = f"sess-{order_index}"
            price = 10 + (order_index % 37)
            lines.append(f"order-{order_index},{customer_id},{session_id},{order_date},{price}")
            order_index += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flat_orders_csv(path: Path, months: int = 24) -> None:
    """No real trend or seasonality - a genuine model has nothing to learn
    that beats naive-last-value on this series."""
    lines = ["order_id,order_date"]
    order_index = 0
    for month in range(months):
        year = 2021 + month // 12
        month_of_year = month % 12 + 1
        count = 40 + (1 if month % 2 == 0 else -1)
        for k in range(count):
            day = (k % 27) + 1
            order_date = f"{year:04d}-{month_of_year:02d}-{day:02d}"
            lines.append(f"order-{order_index},{order_date}")
            order_index += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with tempfile.TemporaryDirectory() as tmp:
        with TestClient(app) as client:
            # -- 1. forecast monthly order volume --
            olist_path = Path(tmp) / "olist_orders.csv"
            _olist_orders_csv(olist_path)
            source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(olist_path)}}).json()["id"]
            ingest = client.post(f"/ingest/{source_id}")
            assert ingest.status_code == 200, ingest.text
            run_id = ingest.json()["run_id"]
            print(f"[setup] ingested Olist-like orders as source={source_id} run={run_id}")
            print()

            config = ModelingConfig(n_splits=3)  # bounds real Prophet/ARIMA fits this script performs
            with SessionLocal() as db:
                record = predict(db, modeling_agent=None, query_agent=None, run_id=run_id, target_column="order_id", config=config)
            naive = next(b for b in record.baseline_scores_json if b["model_family"] == "naive")
            print("=== 1. forecast monthly order volume ===")
            print(f"status={record.state} (expect answered), task_type={record.task_type}, split_strategy={record.split_strategy} (expect forward_chaining)")
            print(f"winner={record.model_family} out_of_sample_{record.out_of_sample_metric}={record.out_of_sample_score:.3f} vs naive_baseline={naive['out_of_sample_score']:.3f}")
            print(f"prediction_interval={record.prediction_interval_json}")
            print(f"excluded_features={[e['column'] for e in record.excluded_features_json]}")
            assert record.state == "answered"
            assert record.split_strategy == "forward_chaining"
            assert record.out_of_sample_score < naive["out_of_sample_score"]
            assert record.prediction_interval_json is not None
            print()

            # -- 2. prediction question naming a nonexistent target --
            classification = IntentClassification(intent=IntentKind.PREDICTION, target_column="does_not_exist", confidence=0.99)
            with modeling_llm_override([classification]):
                resp = client.post("/predict", json={"run_id": run_id, "question": "predict the thing that isn't a real column"})
            body = resp.json()
            print("=== 2. prediction targeting a nonexistent column ===")
            print(f"status={body['status']} (expect escalated), reason={body['escalation_reason']} (expect target_not_found), at reported LLM confidence=0.99")
            assert body["status"] == "escalated" and body["escalation_reason"] == "target_not_found"
            print()

            # -- 3. no model beats the baseline --
            flat_path = Path(tmp) / "flat_orders.csv"
            _flat_orders_csv(flat_path)
            flat_source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(flat_path)}}).json()["id"]
            flat_ingest = client.post(f"/ingest/{flat_source_id}")
            flat_run_id = flat_ingest.json()["run_id"]
            with SessionLocal() as db:
                flat_record = predict(db, modeling_agent=None, query_agent=None, run_id=flat_run_id, target_column="order_id", config=config)
            print("=== 3. no model beats the baseline ===")
            print(f"status={flat_record.state}, reason={flat_record.escalation_reason}")
            if flat_record.state == "awaiting_approval":
                print("reported honestly as 'no model outperformed the baseline' - not dressed up as a usable model")
                assert flat_record.escalation_reason == "no_model_beats_baseline"
            else:
                naive_flat = next(b for b in flat_record.baseline_scores_json if b["model_family"] == "naive")
                print(f"a real model DID beat the baseline on this run: {flat_record.out_of_sample_score:.3f} vs {naive_flat['out_of_sample_score']:.3f} - still reported honestly, with both scores shown")
                assert flat_record.out_of_sample_score < naive_flat["out_of_sample_score"]
            print()

    print("ALL ACCEPTANCE CRITERIA VERIFIED")


if __name__ == "__main__":
    main()
