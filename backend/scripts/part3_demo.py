"""Part 3 stop-gate demonstration.

Ingests a clean synthetic dataset (establishing a provisional baseline), then
applies every CorruptionSuite injector independently to that same clean
dataset PLUS one composed multi-fault case, re-ingesting each corrupted
version through the real FastAPI /ingest pipeline and comparing the
resulting validation_events against each corruption's ground truth. Prints
the raw validation_events rows plus a detected/missed summary.

All seeds are fixed constants below, so this whole run is reproducible.

Run: .venv/Scripts/python.exe scripts/part3_demo.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DB_PATH = Path(tempfile.gettempdir()) / "agentic_platform_part3_demo.db"
_DB_PATH.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, ValidationEvent  # noqa: E402
from tests.corruption import CorruptionSuite  # noqa: E402

Base.metadata.create_all(bind=engine)

CLEAN_DATA_SEED = 0
CORRUPTION_BASE_SEED = 100
COMPOSE_SEED = 200


def make_clean_orders(n: int = 400, seed: int = CLEAN_DATA_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cities = ["New York", "Los Angeles", "San Francisco", "Chicago", "Houston"]
    return pd.DataFrame(
        {
            "order_id": range(1, n + 1),
            "customer_city": rng.choice(cities, size=n),
            "price": rng.normal(loc=120.0, scale=35.0, size=n).round(2).clip(min=1.0),
            "quantity": rng.integers(1, 6, size=n),
        }
    )


def _print_events(run_id: str) -> list[ValidationEvent]:
    with SessionLocal() as db:
        events = db.query(ValidationEvent).filter(ValidationEvent.run_id == run_id).all()
    if events:
        for e in events:
            print(
                f"  validation_event: rule_failed={e.rule_failed!r} column_name={e.column_name!r} "
                f"detail={e.detail_json} against_provisional_baseline={e.against_provisional_baseline} "
                f"correlation_group_id={e.correlation_group_id} correlation_rule={e.correlation_rule} "
                f"diagnosis_json={e.diagnosis_json} risk_level={e.risk_level} action_taken={e.action_taken}"
            )
        group_ids = {e.correlation_group_id for e in events if e.correlation_group_id}
        if group_ids:
            print(f"  -> {len(events)} events correlated into {len(group_ids)} diagnosis call(s) "
                  f"(would be {len(events)} without correlation)")
    else:
        print("  validation_event: (none)")
    return events


def main() -> None:
    client = TestClient(app)
    scratch_dir = Path(tempfile.mkdtemp(prefix="part3_demo_"))
    csv_path = scratch_dir / "orders.csv"

    print(f"Seeds: clean_data_seed={CLEAN_DATA_SEED} corruption_base_seed={CORRUPTION_BASE_SEED} "
          f"compose_seed={COMPOSE_SEED}\n")

    clean_df = make_clean_orders()
    clean_df.to_csv(csv_path, index=False)

    source_id = client.post(
        "/sources", json={"type": "file", "connection_config": {"path": str(csv_path)}}
    ).json()["id"]

    baseline_run = client.post(f"/ingest/{source_id}").json()
    print("=== Baseline-establishing ingest ===")
    print(f"run_id={baseline_run['run_id']} status={baseline_run['status']} "
          f"row_count={baseline_run['metadata']['row_count']} "
          f"validation_failure_count={baseline_run['validation_failure_count']} "
          f"baseline={baseline_run['baseline']}")
    print("(baseline is_provisional=True: it hasn't been confirmed via POST /approvals/{id}/resolve yet, "
          "so every event below is flagged against_provisional_baseline=True)")
    print()

    suite = CorruptionSuite()
    results = suite.apply_all(clean_df, base_seed=CORRUPTION_BASE_SEED)

    rows = []
    print("=== Per-corruption ingests (single-fault, isolated) ===")
    for name, (corrupted_df, truth) in results.items():
        corrupted_df.to_csv(csv_path, index=False)
        run = client.post(f"/ingest/{source_id}").json()

        events = _print_events_header(name, truth, run)
        fired_families = sorted({e.rule_failed.split(":")[0] for e in events})
        detected = truth.expected_detection in fired_families
        print(f"  => {'DETECTED' if detected else 'MISSED'}")

        rows.append(
            {
                "corruption_type": truth.corruption_type,
                "expected_detection": truth.expected_detection,
                "expected_risk_level": truth.expected_risk_level,
                "seed": truth.seed,
                "rule_families_fired": ", ".join(fired_families) or "(none)",
                "event_count": len(events),
                "detected": "yes" if detected else "no",
            }
        )

    # --- composed multi-fault case: two corruptions on two columns in one run ---
    print("\n=== Composed multi-fault ingest ===")
    print("customer_city renamed to 'city' (schema_conformance/low) AND price scaled 4x "
          "(distribution_drift/high) in the SAME corrupted file, as a real bad export would.")
    specs = [
        ("rename_column", {"column": "customer_city", "new_name": "city"}),
        ("shift_distribution", {"column": "price", "mode": "scale", "factor": 4.0}),
    ]
    composed_df, composed_truths = suite.compose(clean_df, specs, seed=COMPOSE_SEED)
    composed_df.to_csv(csv_path, index=False)
    composed_run = client.post(f"/ingest/{source_id}").json()

    print(f"run_id={composed_run['run_id']} status={composed_run['status']} "
          f"row_count={composed_run['metadata']['row_count']}")
    composed_events = _print_events(composed_run["run_id"])
    fired_families = sorted({e.rule_failed.split(":")[0] for e in composed_events})

    print("\nGround truths composed into this one run (matching is many-to-many here, not 1:1 - "
          "Part 6 handles this properly; this is just a presence check per expected family):")
    for truth in composed_truths:
        detected = truth.expected_detection in fired_families
        print(f"  - {truth.corruption_type} (column={truth.target_columns}, seed={truth.seed}, "
              f"expected_detection={truth.expected_detection}, expected_risk={truth.expected_risk_level}) "
              f"=> {'DETECTED' if detected else 'MISSED'}")
        rows.append(
            {
                "corruption_type": f"{truth.corruption_type} (composed)",
                "expected_detection": truth.expected_detection,
                "expected_risk_level": truth.expected_risk_level,
                "seed": truth.seed,
                "rule_families_fired": ", ".join(fired_families) or "(none)",
                "event_count": len(composed_events),
                "detected": "yes" if detected else "no",
            }
        )

    print("\n\n=== Summary: detected vs missed against ground truth ===\n")
    header = ["corruption_type", "expected_detection", "expected_risk_level", "seed",
              "rule_families_fired", "event_count", "detected"]
    col_widths = [max(len(str(r[h])) for r in rows + [dict(zip(header, header))]) for h in header]

    def fmt_row(values: list[str]) -> str:
        return " | ".join(str(v).ljust(w) for v, w in zip(values, col_widths))

    print(fmt_row(header))
    print("-+-".join("-" * w for w in col_widths))
    for r in rows:
        print(fmt_row([r[h] for h in header]))

    n_detected = sum(1 for r in rows if r["detected"] == "yes")
    print(f"\n{n_detected}/{len(rows)} ground-truth corruptions detected by Part 3's rule families.")


def _print_events_header(name: str, truth, run: dict) -> list[ValidationEvent]:
    print(f"\n--- {name} (ground truth: {truth.corruption_type}, seed={truth.seed}, "
          f"expected_detection={truth.expected_detection}, expected_risk={truth.expected_risk_level}) ---")
    print(f"run_id={run['run_id']} status={run['status']} row_count={run['metadata']['row_count']}")
    return _print_events(run["run_id"])


if __name__ == "__main__":
    main()
