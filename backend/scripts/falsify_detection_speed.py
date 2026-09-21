"""Break each guarantee behind the run-1 fix; report which test notices.

Run 1 - a 1,067,371-row retail export - sat "completed" for 16 minutes before
its report existed, 9 of them with no exploration. Two defects: role
detection re-parsed every column once per scorer, and GET /reports told the
reader "Exploration has finished"
on the strength of a status that is set before exploration starts.

Each mutation is asserted to have LANDED before its result is trusted: a
mutation that silently fails to apply reports NOT CAUGHT, which reads exactly
like a missing test.

    .venv/Scripts/python.exe scripts/falsify_detection_speed.py
"""

import pathlib
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())

CASES = [
    (
        "the per-pass cache is bypassed (every scorer re-parses)",
        "app/analytics/roles.py",
        ("    cache = _PASS_CACHE.get()\n    if cache is None:", "    cache = None\n    if cache is None:"),
        "tests/test_detection_parse_once.py::test_each_column_is_parsed_at_most_once_per_detection_pass",
    ),
    (
        "the cache is never reset, so it outlives its pass",
        "app/analytics/roles.py",
        ("        finally:\n            _PASS_CACHE.reset(token)", "        finally:\n            pass"),
        "tests/test_detection_parse_once.py::test_the_cache_does_not_outlive_its_detection_pass",
    ),
    (
        # The exit that mattered for run 1: free text is rejected early.
        "looks_numeric loses its REJECT exit (free text parsed end to end again)",
        "app/numeric_text.py",
        ("        elif (parsed + (total - seen)) / total < minimum_parse_rate:\n            return False",
         "        elif False:\n            return False"),
        "tests/test_detection_parse_once.py::test_looks_numeric_stops_parsing_once_the_answer_is_decided",
    ),
    (
        "looks_numeric loses its ACCEPT exit",
        "app/numeric_text.py",
        ("            if parsed / total >= minimum_parse_rate:\n                return True",
         "            if False:\n                return True"),
        "tests/test_detection_parse_once.py::test_looks_numeric_stops_parsing_once_the_answer_is_decided",
    ),
    (
        "the early exit decides too soon (off by one in the remaining count)",
        "app/numeric_text.py",
        ("        elif (parsed + (total - seen)) / total < minimum_parse_rate:",
         "        elif (parsed + (total - seen) - 1) / total < minimum_parse_rate:"),
        "tests/test_detection_parse_once.py::test_looks_numeric_early_exit_gives_the_full_scan_answer",
    ),
    # EXPECTED NOT CAUGHT - an equivalent mutant. A search of 10 rates across
    # every size up to 4,000 found no input where `parsed >= rate * total`
    # and `parsed / total >= rate` disagree, and a too-strict early exit is
    # rescued by the final full comparison anyway. Kept so the result is
    # visible rather than assumed: a CAUGHT here would mean a real rounding
    # case exists, and the comment in numeric_text.py should then say so.
    (
        "the early exit uses a rearranged comparison (rate * total)",
        "app/numeric_text.py",
        ("            if parsed / total >= minimum_parse_rate:\n                return True",
         "            if parsed >= minimum_parse_rate * total:\n                return True"),
        "tests/test_detection_parse_once.py::test_looks_numeric_early_exit_gives_the_full_scan_answer",
    ),
    (
        # Found by the E2E run for this fix: a second route to "completed,
        # no report, forever" - a dropped connection escaping the client.
        "a dropped connection escapes the LLM client as a raw httpx error",
        "app/llm/gemini_client.py",
        ("            except httpx.TransportError as exc:",
         "            except httpx.UnsupportedProtocol as exc:"),
        "tests/test_narrative_fallback_diagnostics.py::test_a_dropped_connection_through_the_real_client_still_produces_a_report",
    ),
    (
        "GET /reports claims exploration finished again, from the status alone",
        "app/routers/reports.py",
        ("    if exploration is None:\n        return (",
         "    if False:\n        return ("),
        "tests/test_narrative_api.py::test_a_missing_report_on_a_COMPLETED_run_does_not_blame_the_run_state",
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
    original = raw.decode("utf-8")
    text = original.replace("\r\n", "\n")
    if text.count(old) != 1:
        print(f"[SKIP] {name}\n       anchor found {text.count(old)} times in {path}")
        continue
    mutated = text.replace(old, new, 1)
    file.write_bytes((mutated.replace("\n", "\r\n") if crlf else mutated).encode("utf-8"))
    if old in file.read_bytes().decode("utf-8").replace("\r\n", "\n"):
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
