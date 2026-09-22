"""Put the held-open session back; confirm the tests notice. Then the rest.

The defect: a session that had queried stayed open across model calls,
holding a pooled connection, and overlapping runs drained the pool (5 + 10).
The first case re-creates exactly that in narrate_node and must fail the
load test. The others break each remaining guarantee one at a time.

Each mutation is asserted to have LANDED before its result is trusted - a
mutation that silently fails to apply reports NOT CAUGHT, which reads
exactly like a missing test.

    .venv/Scripts/python.exe scripts/falsify_connection_release.py
"""

import pathlib
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())
T = "tests/test_no_connection_across_model_calls.py::"

NARRATE_CALL = (
    "    report, egress_records = generate_for_inputs(inputs, contract.data, narrative_agent)\n"
)
NARRATE_HELD = (
    "    with SessionLocal() as _held:\n"
    "        _held.get(Run, state[\"run_id\"])  # the old pattern: queried, then kept open\n"
    "        report, egress_records = generate_for_inputs(inputs, contract.data, narrative_agent)\n"
)

CASES = [
    (
        "THE ORIGINAL DEFECT: narrate_node holds a queried session across its model calls",
        "app/graph/nodes.py",
        (NARRATE_CALL, NARRATE_HELD),
        T + "test_more_simultaneous_ingests_than_the_pool_holds_and_none_time_out",
    ),
    (
        "summarise_node holds a queried session across its model calls",
        "app/graph/nodes.py",
        (
            "    result = generate_summary(inputs, summary_agent)\n",
            "    with SessionLocal() as _held:\n"
            "        _held.get(Run, state[\"run_id\"])\n"
            "        result = generate_summary(inputs, summary_agent)\n",
        ),
        T + "test_more_simultaneous_ingests_than_the_pool_holds_and_none_time_out",
    ),
    (
        "resolve_node diagnoses with a queried session held open",
        "app/graph/nodes.py",
        (
            "        groups_and_events = _group_detected_events(detected_events)\n        queue_outcome = process_queue(\n",
            "        groups_and_events = _group_detected_events(detected_events)\n"
            "        _held = SessionLocal(); _held.get(Run, run_id)\n"
            "        queue_outcome = process_queue(\n",
        ),
        T + "test_diagnosis_in_resolve_node_holds_no_connection_and_still_writes_everything",
    ),
    (
        "the query path no longer releases the request's connection before its call",
        "app/graph/query_graph.py",
        ("    release_connection(db)\n    outcome = query_agent.generate(", "    outcome = query_agent.generate("),
        T + "test_narrative_summary_query_and_modeling_calls_hold_no_connection",
    ),
    (
        "POST /ingest's request session is no longer function-scoped",
        "app/routers/ingest.py",
        ('    db: Session = Depends(get_db, scope="function"),\n', "    db: Session = Depends(get_db),\n"),
        T + "test_every_endpoint_that_schedules_background_work_closes_its_session_first",
    ),
    (
        "...and the same regression, seen by the precision test through the real HTTP path",
        "app/routers/ingest.py",
        ('    db: Session = Depends(get_db, scope="function"),\n', "    db: Session = Depends(get_db),\n"),
        T + "test_narrative_summary_query_and_modeling_calls_hold_no_connection",
    ),
    (
        "save_narrative_report overwrites a report written meanwhile",
        "app/narrative/pipeline.py",
        ("    elif existing is not None:\n        skip_reason = \"a report was written", "    elif False:\n        skip_reason = \"a report was written"),
        T + "test_a_report_written_meanwhile_is_kept_and_this_one_dropped",
    ),
    (
        "save_summary writes against a run that is no longer completed",
        "app/summary/pipeline.py",
        ("    elif run.status != \"completed\":\n        skip_reason = f\"the run is now", "    elif False:\n        skip_reason = f\"the run is now"),
        T + "test_no_summary_is_written_against_a_run_that_stopped_being_completed",
    ),
    (
        "resolve_node writes its diagnosis over a run rejected meanwhile",
        "app/graph/nodes.py",
        ("        if current.status != status_as_read:\n            conflict = f\"the run became", "        if False:\n            conflict = f\"the run became"),
        T + "test_a_run_rejected_during_diagnosis_is_not_overwritten",
    ),
    (
        "resolve_node overwrites events another resolution processed meanwhile",
        "app/graph/nodes.py",
        ("        elif changed_events:\n", "        elif False:\n"),
        T + "test_events_resolved_elsewhere_during_diagnosis_are_not_overwritten",
    ),
    (
        "reveal_depth_reached is left on the detached run and lost",
        "app/graph/nodes.py",
        (
            "        current.reveal_depth_reached = max(current.reveal_depth_reached or 0, run.reveal_depth_reached or 0)\n",
            "",
        ),
        T + "test_diagnosis_in_resolve_node_holds_no_connection_and_still_writes_everything",
    ),
]


def run(test: str) -> bool:
    result = subprocess.run([PY, "-m", "pytest", test, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True)
    return result.returncode == 0


caught = 0
for name, path, (old, new), test in CASES:
    file = pathlib.Path(path)
    raw = file.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if text.count(old) != 1:
        print(f"[SKIP] {name}\n       anchor found {text.count(old)} times in {path}")
        continue
    mutated = text.replace(old, new, 1)
    file.write_bytes((mutated.replace("\n", "\r\n") if crlf else mutated).encode("utf-8"))
    if file.read_bytes().decode("utf-8").replace("\r\n", "\n") != mutated:
        file.write_bytes(raw)
        print(f"[SKIP] {name}\n       write did not land")
        continue
    try:
        passed = run(test)
    finally:
        file.write_bytes(raw)
    verdict = "NOT CAUGHT" if passed else "CAUGHT"
    caught += not passed
    print(f"[{verdict}] {name}\n          by: {test.split('::')[-1]}")

print(f"\n{caught}/{len(CASES)} guards caught.")
