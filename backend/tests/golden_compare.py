"""Shared comparison logic between the pre-migration and post-migration
golden capture tests (Phase 8 Part 0).

RULE 2 (never regenerate the golden file to make tests pass): this module
contains NO write path to the golden JSON file at all - only
scripts/record_golden_baseline.py does that, and it is a standalone script,
never imported or invoked by pytest. If a golden assertion fails, the two
valid responses are fixing the code or documenting a divergence (Rule 1)
directly in tests/golden/ingest_capture.json's "_divergences" list - never
re-running a recorder to make the failure go away.

RULE 1 (preserve by default; divergences are documented, not silent):
DOMAIN_FIELDS are compared for exact equality - run status, fix_chain,
final ValidationEvent states, baseline state, downstream-artifact
presence. These are NOT expected to change under the graph migration, and
never gained a divergence entry.

agent_trace_sequence is compared differently, deliberately: the recorded
sequence must remain an order-preserving SUBSEQUENCE of whatever the
current code produces - new entries may be INSERTED (Part 2 adds
edge-tracking traces, and a new failure-path trace on connector-fetch
exceptions), but nothing recorded may be removed or reordered. A pure
insertion never needs a per-scenario divergence entry (see the single,
general policy note in ingest_capture.json's "_divergences" list, which
explains this class of change once rather than duplicating the same reason
per scenario) - but removal or reordering of a recorded step is exactly
addition (A)'s concern and is never accepted silently.
"""

from __future__ import annotations

import json
from pathlib import Path

DOMAIN_FIELDS = [
    "run_status",
    "fix_chain",
    "reveal_depth_reached",
    "events",
    "baseline_active",
    "exploration_present",
    "report_present",
    "report_generation_mode",
]


def load_golden(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assert_matches_golden(golden: dict, scenario: str, actual: dict) -> None:
    if scenario not in golden:
        raise AssertionError(f"no golden entry recorded for scenario {scenario!r} - this is a NEW scenario, not a divergence; add it via scripts/record_golden_baseline.py and get it reviewed, not asserted into existence here")
    expected = golden[scenario]

    for field in DOMAIN_FIELDS:
        if field not in expected:
            continue
        actual_value = actual.get(field)
        expected_value = expected[field]
        assert actual_value == expected_value, (
            f"{scenario}.{field}: expected {expected_value!r}, got {actual_value!r} - "
            "if this is an INTENTIONAL change, it must be recorded in tests/golden/ingest_capture.json's "
            "_divergences list with a reason (Rule 1), never silently updated here"
        )

    _assert_trace_sequence_is_compatible(scenario, expected.get("agent_trace_sequence", []), actual.get("agent_trace_sequence", []))


def _assert_trace_sequence_is_compatible(scenario: str, old_sequence: list[str], new_sequence: list[str]) -> None:
    remaining = iter(new_sequence)
    for expected_name in old_sequence:
        for actual_name in remaining:
            if actual_name == expected_name:
                break
        else:
            raise AssertionError(
                f"{scenario}: recorded trace step {expected_name!r} is missing or reordered in the new "
                f"sequence {new_sequence!r} (recorded sequence was {old_sequence!r}) - a reordering or "
                "removal is exactly what this check exists to catch; a pure insertion of new steps would "
                "have passed"
            )
