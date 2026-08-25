"""Break each agriculture guard; report which test notices.

The two traps are the point: a crop YEAR taken as a measure, and area /
production / yield taken for each other. A guard against confident nonsense is
only real if removing it produces the nonsense and something fails.
"""

import pathlib
import shutil
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())
TEST = "tests/test_agriculture.py"

CASES = [
    (
        "TRAP 1: measures stop refusing a crop year",
        "app/analytics/roles.py",
        (
            '    if _reads_as_year(series, column):\n        return 0.0, ["reads as a crop year - a label, not a measured quantity"]',
            "    pass",
        ),
    ),
    (
        "TRAP 1: the year value-range check is removed",
        "app/analytics/roles.py",
        (
            "    return bool((non_null >= _YEAR_MIN).all() and (non_null <= _YEAR_MAX).all())",
            "    return False",
        ),
    ),
    (
        "TRAP 2: the mandatory-name guard is removed (shape alone assigns)",
        "app/analytics/roles.py",
        (
            "    if not _NAME_HINTS[role].search(column):",
            "    if False:",
        ),
    ),
    (
        "TRAP 2: siblings stop standing down for a more specific name",
        "app/analytics/roles.py",
        (
            "        if _NAME_HINTS[other].search(column) and not _NAME_HINTS[role].search(column):",
            "        if False:",
        ),
    ),
    (
        "zero area divides anyway, producing an infinite yield",
        "app/agriculture/preprocess.py",
        ("        safe_area = area.where(area > 0)", "        safe_area = area"),
    ),
    (
        "label normalisation stops being recorded",
        "app/agriculture/preprocess.py",
        ("                normalisations.extend(_normalise_labels(work, column, role))", "                _normalise_labels(work, column, role)"),
    ),
    (
        "yield collapse compares against other districts, not its own history",
        "app/agriculture/rules.py",
        (
            "        history = values.iloc[:-1]\n        mean = float(history.mean())",
            "        history = values.iloc[:-1]\n        mean = float(df[DERIVED_YIELD].mean())",
        ),
    ),
    (
        "the history minimum is removed (two points become a history)",
        "app/agriculture/rules.py",
        (
            "        if len(values) < config.min_history_points + 1:\n            continue\n        latest = float(values.iloc[-1])",
            "        if len(values) < 2:\n            continue\n        latest = float(values.iloc[-1])",
        ),
    ),
    (
        "key values render an absent measure as zero",
        "app/agriculture/rules.py",
        (
            '        undefined["rainfall_mean"] = "this source has no rainfall column"',
            "        rainfall_mean = 0.0",
        ),
    ),
    (
        "the crop-year grain is dropped, collapsing every history",
        "app/agriculture/preprocess.py",
        (
            "        grain_roles = [*grain_roles, ColumnRole.CROP_YEAR]",
            "        pass",
        ),
    ),
]


def run() -> tuple[str, list[str]]:
    result = subprocess.run(
        [PY, "-m", "pytest", TEST, "-q", "--no-header", "--tb=no"],
        capture_output=True,
        text=True,
    )
    summary = [l for l in result.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
    failed = [l.replace(f"FAILED {TEST}::", "") for l in result.stdout.splitlines() if l.startswith("FAILED")]
    return (summary[-1] if summary else "no summary"), failed


for label, rel, (old, new) in CASES:
    path = pathlib.Path(rel)
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy(path, backup)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"[{label}] SKIPPED - anchor not found in {rel}")
        backup.unlink()
        continue
    mutated = text.replace(old, new, 1)
    assert mutated != text, "mutation did not change the file"
    path.write_text(mutated, encoding="utf-8")

    summary, failed = run()

    shutil.copy(backup, path)
    backup.unlink()

    verdict = "CAUGHT" if ("failed" in summary or "error" in summary) else "*** NOT CAUGHT ***"
    print(f"[{label}] {verdict}: {summary}")
    for line in failed[:2]:
        print(f"    {line}")

print("\nall files restored")
