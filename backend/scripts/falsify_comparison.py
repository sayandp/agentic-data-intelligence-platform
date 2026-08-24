"""Break each comparison guarantee; report which test notices.

The guards in app/comparison/ all have the same shape: they REFUSE to show a
delta. A refusal nobody has seen fail is a refusal nobody has shown to work -
and a broken one fails silently, by rendering a plausible number.
"""

import pathlib
import shutil
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())
TEST = "tests/test_run_comparison.py"

CASES = [
    (
        "value-basis guard removed",
        "app/comparison/engine.py",
        ("    if basis_a != basis_b:", "    if False:"),
    ),
    (
        "baseline-supersession guard removed",
        "app/comparison/engine.py",
        ("    if superseded is not None:", "    if False:"),
    ),
    (
        "one-sided analysis differenced instead of reported",
        "app/comparison/engine.py",
        ("        if ran_a != ran_b:", "        if False:"),
    ),
    (
        "different-source check removed",
        "app/comparison/engine.py",
        ("    if run_a.source_id != run_b.source_id:", "    if False:"),
    ),
    (
        "incomplete-run check removed",
        "app/comparison/engine.py",
        ("    if incomplete:", "    if False:"),
    ),
    (
        "relative change divides by zero silently",
        "app/comparison/models.py",
        (
            "        if self.before is None or self.after is None or self.before == 0:\n            return None",
            "        if self.before is None or self.after is None:\n            return None\n        if self.before == 0:\n            return 0.0",
        ),
    ),
    (
        "finding identity falls back to the positional id",
        "app/comparison/identity.py",
        ("    base = (finding_type, _columns_key(finding.get(\"columns\")))", "    base = (str(finding.get(\"id\", \"\")),)"),
    ),
    (
        "a section may claim not-comparable with no reason",
        "app/comparison/models.py",
        (
            "        if self.comparability is not Comparability.COMPARABLE and not self.reason:\n            raise ValueError(f\"section {self.name!r} is {self.comparability.value} but gives no reason\")",
            "        return",
        ),
    ),
]


def run() -> tuple[str, list[str]]:
    result = subprocess.run(
        [PY, "-m", "pytest", TEST, "-q", "--no-header", "--tb=no"],
        capture_output=True,
        text=True,
    )
    summary = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line]
    failed = [line.replace("FAILED tests/test_run_comparison.py::", "") for line in result.stdout.splitlines() if line.startswith("FAILED")]
    return (summary[-1] if summary else "no summary"), failed


for label, rel, (old, new) in CASES:
    path = pathlib.Path(rel)
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy(path, backup)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"[{label}] SKIPPED - anchor not found")
        backup.unlink()
        continue
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    summary, failed = run()

    shutil.copy(backup, path)
    backup.unlink()

    verdict = "CAUGHT" if "failed" in summary else "*** NOT CAUGHT ***"
    print(f"[{label}] {verdict}: {summary}")
    for line in failed[:2]:
        print(f"    {line}")

print("\nall files restored")
