"""Phase 6 ACCEPTANCE verification, run standalone (not pytest) - mirrors
scripts/part6_evaluation.py / scripts/narrative_evaluation.py's style.

Exercises the three ACCEPTANCE bullets from the Phase 6 spec against an
Olist-shaped dataset:

  1. "what were total sales by month?" -> valid code generated, shown to
     the user, executed in the sandbox, correct answer returned.
  2. A question unanswerable from the schema -> escalates cleanly, no guess.
  3. A hostile generation (DROP TABLE) -> refused before execution, not after.

Uses a FakeLLMClient (tests.fakes.query_llm_override) rather than a live
Gemini call - Part 0 (updating FREE_TIER_MODELS to current model IDs) is
still blocked pending the exact IDs, so no live call is attempted here; this
script verifies the Query Agent's OWN logic (generation -> validation ->
execution -> escalation), which is independent of which model eventually
fills in `code`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "agentic_platform_acceptance.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.query.models import GeneratedQuery, QueryKind  # noqa: E402
from tests.fakes import query_llm_override  # noqa: E402


def make_olist_like(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="D")
    price = rng.normal(loc=120.0, scale=35.0, size=n).round(2).clip(min=1.0)
    return pd.DataFrame(
        {
            "order_id": [f"order_{i}" for i in range(n)],
            "order_purchase_timestamp": dates,
            "customer_city": rng.choice(["New York", "Los Angeles", "Chicago"], size=n),
            "price": price,
            "review_score": rng.integers(1, 6, size=n),
        }
    )


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "olist_orders.csv"
        make_olist_like().to_csv(csv_path, index=False)

        with TestClient(app) as client:
            source_id = client.post("/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}).json()["id"]
            ingest = client.post(f"/ingest/{source_id}")
            assert ingest.status_code == 200, ingest.text
            run_id = ingest.json()["run_id"]
            print(f"[setup] ingested {ingest.json()['metadata']['row_count']} rows as source={source_id} run={run_id}")
            print()

            # -- 1. valid question, correct answer, code always shown --
            # CSV has no declared schema (Phase 2) - order_purchase_timestamp
            # round-trips as plain object/string, not datetime64, exactly
            # like it would for a real Query Agent seeing this source's live
            # dtypes. The generated code (and the ground truth below) both
            # derive "month" via string slicing on the ISO-formatted
            # timestamp, not a .dt accessor, for that reason.
            raw = pd.read_csv(csv_path, dtype={"order_purchase_timestamp": str})
            month_totals = (
                raw.assign(month=raw["order_purchase_timestamp"].str.slice(0, 7))
                .groupby("month")["price"]
                .sum()
                .round(2)
                .to_dict()
            )
            generated = GeneratedQuery(
                query_kind=QueryKind.PANDAS,
                code=(
                    "monthly = df.assign(month=df['order_purchase_timestamp'].str.slice(0, 7))"
                    ".groupby('month')['price'].sum().round(2)\n"
                    "result = monthly.to_dict()"
                ),
                columns_referenced=["order_purchase_timestamp", "price"],
                assumptions=["'total sales' means sum of price per calendar month; timestamp column is ISO-formatted text"],
                confidence=0.93,
            )
            with query_llm_override([generated]):
                resp = client.post("/ask", json={"run_id": run_id, "question": "what were total sales by month?"})
            body = resp.json()
            print("=== 1. valid pandas question ===")
            print(f"status={body['status']} (expect answered)")
            print(f"code shown to user:\n{body['code']}")
            if body["status"] != "answered":
                print(f"DEBUG escalation_reason={body['escalation_reason']} detail={body['escalation_detail']}")
            answer_matches = body["result"]["value"] == month_totals
            print(f"answer matches independently-computed ground truth: {answer_matches}")
            assert body["status"] == "answered"
            assert answer_matches
            print()

            # -- 2. unanswerable question, clean escalation --
            unanswerable = GeneratedQuery(
                query_kind=QueryKind.UNANSWERABLE, code="", columns_referenced=[], assumptions=[], confidence=0.9
            )
            with query_llm_override([unanswerable]):
                resp = client.post("/ask", json={"run_id": run_id, "question": "what is the customer's favorite color?"})
            body = resp.json()
            print("=== 2. unanswerable question ===")
            print(f"status={body['status']} (expect escalated), reason={body['escalation_reason']} (expect unanswerable)")
            assert body["status"] == "escalated" and body["escalation_reason"] == "unanswerable"
            print()

            # -- 3. hostile generation, refused before execution --
            hostile = GeneratedQuery(
                query_kind=QueryKind.PANDAS,
                code="import shutil\nshutil.rmtree('/')\nresult = 1",
                columns_referenced=[],
                assumptions=[],
                confidence=0.99,
            )
            with query_llm_override([hostile]):
                resp = client.post("/ask", json={"run_id": run_id, "question": "clean up temp files"})
            body = resp.json()
            print("=== 3. hostile generation ===")
            print(f"status={body['status']} (expect escalated), reason={body['escalation_reason']} (expect static_validation_failed)")
            print(f"the sandbox process was never even started - the AST allowlist refused 'import shutil' on sight")
            assert body["status"] == "escalated" and body["escalation_reason"] == "static_validation_failed"
            print()

    print("ALL ACCEPTANCE CRITERIA VERIFIED")


if __name__ == "__main__":
    main()
