"""Data the v2 cut renders as tables: the real rows of the seeded orders file
in the three states the runs saw, and what the app's OWN redactor emits for
them (app.privacy - imported read-only, nothing changed).

  ..\\backend\\.venv\\Scripts\\python.exe scripts\\v2_data.py
Writes capture/public/v2-data.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "backend"))

from seed_demo_runs import make_orders  # noqa: E402
from app.privacy.classification import classify_frame  # noqa: E402
from app.privacy.redaction import RedactionPolicy, redact_records  # noqa: E402

df = make_orders()
cols = ["order_id", "city", "category", "price", "quantity", "customer_email"]
head = df[cols].head(6)

clean = head.to_dict(orient="records")
renamed = head.rename(columns={"city": "town"}).to_dict(orient="records")
dollar = [{**r, "price": f"${r['price']:.2f}"} for r in clean]

classification = classify_frame(df)
sample = redact_records(clean, classification, RedactionPolicy.STRICT)
redacted = sample.rows

out = {
    "source": "video/scripts/seed_demo_runs.py make_orders() (random.Random(7)), first 6 rows",
    "columns": cols,
    "clean": clean,
    "renamed": renamed,
    "dollar": dollar,
    "redacted": redacted,
    "redacted_columns": getattr(sample, "redacted_columns", {}),
}
path = HERE.parent / "capture" / "public" / "v2-data.json"
path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
print(path)
for r in redacted[:3]:
    print({k: r[k] for k in ("city", "customer_email")})
print("redacted columns:", out["redacted_columns"])
