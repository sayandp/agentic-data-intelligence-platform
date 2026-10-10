"""Seeds the runs the video films that the database did not already have.

Talks to the running stack through its public HTTP API only - no app code is
imported or changed. Every run it creates is an ordinary user-initiated
ingest; nothing here schedules anything.

Data is synthetic and seeded (random.Random(7)): orders with a fake
`customer_email` on the reserved example.com domain, so the privacy layer
has a real email column to classify and the egress trail has something to
mask - without a single real person in it.

Runs it produces, in order (all against ONE file source so they share a
baseline, plus three more sources for the connector shots):
  clean     - first ingest, establishes the provisional baseline
  renamed   - `city` arrives as `town`  -> expected: rename_column auto-fixed
  dollar    - `price` arrives as "$12.50" text -> expected: safe_type_cast
              attempted, post-condition fails, reverted, escalated
  api       - the clean rows served over a loopback HTTP endpoint
  sql       - the clean rows in a SQLite table (needs ADIP_DEMO_SQL_URL set
              in the BACKEND's environment)
  workbook  - a two-sheet .xlsx -> a connector warning to acknowledge

Writes capture/state.json with every run id/number. Outcomes are whatever the
system actually decided; the script reports them, it does not assume them.

Run with the backend venv (needs pandas + openpyxl):
  ..\\backend\\.venv\\Scripts\\python.exe scripts\\seed_demo_runs.py
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

API = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "capture" / "data"
STATE = HERE / "capture" / "state.json"
API_PORT = 8765


def make_orders(n: int = 240) -> pd.DataFrame:
    rng = random.Random(7)
    cities = {"north": ["Leeds", "York"], "south": ["Brighton", "Exeter"], "east": ["Norwich", "Ipswich"], "west": ["Bristol", "Bath"]}
    cats = ["widgets", "gadgets", "tools", "accessories"]
    rows = []
    start = date(2026, 1, 5)
    for i in range(n):
        region = rng.choice(list(cities))
        rows.append(
            {
                "order_id": f"ORD-{i + 1:05d}",
                "order_date": (start + timedelta(days=i // 3)).isoformat(),
                "region": region,
                "city": rng.choice(cities[region]),
                "category": rng.choice(cats),
                "price": round(rng.uniform(8, 120), 2),
                "quantity": rng.randint(1, 6),
                "customer_email": f"customer{rng.randint(1, 90):03d}@example.com",
            }
        )
    return pd.DataFrame(rows)


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def register(kind: str, config: dict) -> str:
    return call("POST", "/sources", {"type": kind, "connection_config": config})["id"]


def ingest(source_id: str, label: str) -> dict:
    run_id = call("POST", f"/ingest/{source_id}", {})["run_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        s = call("GET", f"/ingest/{run_id}/status")
        if s["status"] in ("completed", "awaiting_approval", "failed"):
            print(f"  {label:9s} run #{s['run_number']} -> {s['status']}")
            return {"run_id": run_id, "run_number": s["run_number"], "status": s["status"], "source_id": source_id}
        time.sleep(2)
    raise SystemExit(f"{label}: ingest did not settle in 10 minutes")


def wait_for_report(run_id: str, label: str) -> None:
    # The report and summary are written after the run is marked completed;
    # filming before they exist would film a waiting page.
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            r = call("GET", f"/reports/{run_id}")
            if r.get("narrative_text"):
                return
        except Exception:
            pass
        time.sleep(3)
    print(f"  {label}: no report after 5 minutes (filming will show whatever exists)")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    clean = make_orders()
    state: dict = {}

    csv_path = DATA / "orders.csv"
    print("file source (shared baseline)")
    clean.to_csv(csv_path, index=False)
    src = register("file", {"path": str(csv_path), "original_filename": "orders.csv"})
    state["clean"] = ingest(src, "clean")

    clean.rename(columns={"city": "town"}).to_csv(csv_path, index=False)
    state["renamed"] = ingest(src, "renamed")

    dollar = clean.copy()
    dollar["price"] = dollar["price"].map(lambda p: f"${p:.2f}")
    dollar.to_csv(csv_path, index=False)
    state["dollar"] = ingest(src, "dollar")

    # The file is LEFT as last ingested. A file source is re-read whenever a
    # run's repaired data is rebuilt (Ask, Predict, resuming an approval), so
    # rewriting it here would make the dollar run resume against data it
    # never received (docs/EVIDENCE.md §5.4: reproduction depends on the
    # source being unchanged). The on-camera upload uses its own clean copy.
    clean.to_csv(DATA / "orders_upload.csv", index=False)

    print("api source (loopback)")
    api_dir = DATA / "api"
    api_dir.mkdir(exist_ok=True)
    (api_dir / "orders.json").write_text(clean.to_json(orient="records"), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", API_PORT), partial(SimpleHTTPRequestHandler, directory=str(api_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api_src = register("api", {"url": f"http://127.0.0.1:{API_PORT}/orders.json"})
        state["api"] = ingest(api_src, "api")
    finally:
        server.shutdown()

    print("sql source (SQLite)")
    db_path = DATA / "orders.db"
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as con:
        clean.to_sql("orders", con, index=False)
    sql_src = register("sql", {"connection_string_env": "ADIP_DEMO_SQL_URL", "query": "select * from orders"})
    state["sql"] = ingest(sql_src, "sql")

    print("two-sheet workbook (connector warning)")
    xlsx_path = DATA / "orders_q1.xlsx"
    with pd.ExcelWriter(xlsx_path) as xw:
        clean.to_excel(xw, sheet_name="orders", index=False)
        clean.head(20).to_excel(xw, sheet_name="notes", index=False)
    wb_src = register("file", {"path": str(xlsx_path), "original_filename": "orders_q1.xlsx"})
    state["workbook"] = ingest(wb_src, "workbook")

    for key in ("clean", "renamed", "api", "sql", "workbook"):
        if state[key]["status"] == "completed":
            wait_for_report(state[key]["run_id"], key)

    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"wrote {STATE}")


if __name__ == "__main__":
    sys.exit(main())
