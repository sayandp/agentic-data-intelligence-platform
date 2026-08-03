"""Phase 8 Part 0 / Rule 2: the ONE explicit, standalone place the golden
migration-safety-net file (tests/golden/ingest_capture.json) is ever
written. Never imported by pytest, never invoked automatically, never
given an env-var or flag-triggered path from inside the test suite - a
snapshot test that can rewrite its own expectations certifies whatever the
code currently does, which is worse than no test at all.

Run this deliberately, as a human decision, only when:
  - recording the initial baseline (already done - this is how
    tests/golden/ingest_capture.json was first produced), or
  - a change to the golden battery's SCENARIOS themselves is being made
    (adding a new scenario, changing a fixture) - never to make a failing
    comparison against EXISTING scenarios go away. That failure is either a
    bug to fix or a divergence to document by hand in the "_divergences"
    list (see tests/golden_compare.py's docstring) - regenerating is not a
    third option.

Run: .venv/Scripts/python.exe scripts/record_golden_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DB_PATH = Path(tempfile.gettempdir()) / "agentic_platform_golden_recording.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.diagnosis.agent import DiagnosticAgent  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.modeling.dependency import get_modeling_agent  # noqa: E402
from app.narrative.dependency import get_narrative_agent  # noqa: E402
from app.query.dependency import get_query_agent  # noqa: E402
from app.routers.ingest import get_diagnostic_agent  # noqa: E402
from tests.fakes import FakeLLMClient, InMemoryDiagnosisCache  # noqa: E402
from tests.golden_scenarios import run_all_scenarios  # noqa: E402

GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "ingest_capture.json"


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    app.dependency_overrides[get_diagnostic_agent] = lambda: DiagnosticAgent(
        llm_client=FakeLLMClient(), cache=InMemoryDiagnosisCache(), sleep=lambda _s: None
    )
    app.dependency_overrides[get_narrative_agent] = lambda: None
    app.dependency_overrides[get_query_agent] = lambda: None
    app.dependency_overrides[get_modeling_agent] = lambda: None

    existing = json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.exists() else {}
    divergences = existing.get("_divergences", [])

    with tempfile.TemporaryDirectory() as tmp:
        with TestClient(app) as client:
            golden = run_all_scenarios(client, Path(tmp))

    golden["_scenario_count"] = len(golden)
    if divergences:
        golden["_divergences"] = divergences
        print(f"Carried forward {len(divergences)} existing _divergences entry/entries - review them against this new recording by hand.")

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(golden, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {golden['_scenario_count']} scenarios to {GOLDEN_PATH}")
    print("Review the diff by hand before committing - this script does not know what SHOULD have changed.")


if __name__ == "__main__":
    main()
