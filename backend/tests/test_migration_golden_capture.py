"""Phase 8 Part 0: the migration safety net.

RULE 2: this test NEVER writes tests/golden/ingest_capture.json. It runs
the shared scenario battery (tests/golden_scenarios.py) against whichever
router implementation is currently wired into the app, and compares the
result against the COMMITTED golden file (tests/golden_compare.py) -
nothing here can make a failing comparison pass by rewriting the file it's
supposed to be checked against.

Before the graph migration, this matched the golden file EXACTLY (domain
fields) with an identical trace sequence (no insertions yet either) - that
green, deterministic run was reported and approved before any router
change landed (Part 0's stop gate). app/routers/ingest.py and
app/routers/approvals.py are graph-based now, and this SAME file, run
again with no changes of its own, is what proves the migration: the
domain-field assertions still hold exactly, and the trace-sequence check
still holds as a subsequence match against every documented, reasoned
insertion/reorder in the golden file's _divergences list (Rule 1) - never
a silent update (Rule 2, enforced structurally: tests/golden_compare.py
has no write path at all). There is no separate post-migration file; the
proof is this test's behavior not changing while the code underneath it
did.
"""

from __future__ import annotations

from pathlib import Path

from tests.golden_compare import assert_matches_golden, load_golden
from tests.golden_scenarios import run_all_scenarios

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "ingest_capture.json"


def test_current_router_behavior_matches_golden_capture(client, tmp_path):
    golden = load_golden(GOLDEN_PATH)
    actual = run_all_scenarios(client, tmp_path)

    scenario_names = [k for k in actual if not k.startswith("_")]
    assert set(scenario_names) == {k for k in golden if not k.startswith("_")}, "scenario set changed - update via scripts/record_golden_baseline.py and get it reviewed, not silently"

    for scenario in scenario_names:
        assert_matches_golden(golden, scenario, actual[scenario])
