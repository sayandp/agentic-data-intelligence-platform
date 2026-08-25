"""Break each Part 3 guarantee; report which test notices.

The reuse constraints are the interesting ones. "Reuse the Pareto machinery"
and "use Part 1's comparison machinery" are only real if replacing them with a
local implementation actually fails something.
"""

import pathlib
import shutil
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())
TEST = "tests/test_marketing_depth.py"

CASES = [
    (
        "spend concentration stops calling the analytics Pareto engine",
        "app/marketing/depth.py",
        (
            "from app.analytics.pareto import run_abc_pareto",
            "run_abc_pareto = None  # was: from app.analytics.pareto import run_abc_pareto",
        ),
    ),
    (
        "period movement stops using Part 1's Delta",
        "app/marketing/depth.py",
        (
            "from app.comparison.models import Delta",
            "Delta = None  # was: from app.comparison.models import Delta",
        ),
    ),
    (
        "fatigue fires whenever frequency rises, ignoring performance",
        "app/marketing/depth.py",
        (
            "        if freq_direction <= 0 or perf_direction >= 0:",
            "        if freq_direction <= 0:",
        ),
    ),
    (
        "the materiality floor is removed",
        "app/marketing/depth.py",
        (
            "            if delta.relative is not None and abs(delta.relative) < config.min_relative_movement:\n                continue",
            "            pass",
        ),
    ),
    (
        "efficiency ranking ranks against the mean instead of the median",
        "app/marketing/depth.py",
        ("        median = float(by_adset.median())", "        median = float(by_adset.mean())"),
    ),
    (
        # Targets the clause that states the VALUE. An earlier version of this
        # case rewrote only the leading phrase, leaving "(median 7.042; ...)"
        # intact - so the guarantee still held and nothing failed, correctly.
        "efficiency ranking stops stating the median VALUE",
        "app/marketing/depth.py",
        (
            'f"(median {median:,.4g}; within this run, since {metric} is derived after baseline "',
            'f"(within this run, since {metric} is derived after baseline "',
        ),
    ),
    (
        "the minimum-ad-sets floor is removed",
        "app/marketing/depth.py",
        (
            "        if by_adset.empty or len(by_adset) < config.min_adsets_for_ranking:",
            "        if by_adset.empty:",
        ),
    ),
    (
        "a depth rule stops stating its within-run basis",
        "app/marketing/depth.py",
        (
            'f"(within this run; {metric} is derived after baseline profiling, so no stored baseline exists for it)"',
            '""',
        ),
    ),
    (
        "the depth rules are unregistered from the engine",
        "app/marketing/rules.py",
        ("    *DEPTH_RULES,\n)", ")"),
    ),
]


def run() -> tuple[str, list[str]]:
    result = subprocess.run(
        [PY, "-m", "pytest", TEST, "-q", "--no-header", "--tb=no"],
        capture_output=True,
        text=True,
    )
    summary = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line or "error" in line]
    failed = [line.replace(f"FAILED {TEST}::", "") for line in result.stdout.splitlines() if line.startswith("FAILED")]
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
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    summary, failed = run()

    shutil.copy(backup, path)
    backup.unlink()

    verdict = "CAUGHT" if ("failed" in summary or "error" in summary) else "*** NOT CAUGHT ***"
    print(f"[{label}] {verdict}: {summary}")
    for line in failed[:2]:
        print(f"    {line}")

print("\nall files restored")
