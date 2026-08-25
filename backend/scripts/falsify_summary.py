"""Break each Session Summary guarantee; report which test notices.

The brief called these non-negotiable: stage 2 never sees a raw artifact, the
post-checks are deterministic, quality context comes first, the template states
its reason, and every outbound call is redacted and recorded. A guarantee
nobody has seen fail is a guarantee nobody has shown to work.
"""

import pathlib
import shutil
import subprocess

PY = str(pathlib.Path(".venv/Scripts/python.exe").resolve())
TEST = "tests/test_session_summary.py"

CASES = [
    (
        "stage 2 is handed the raw facts as well as the claims",
        "app/summary/agent.py",
        (
            "    def _build_stage2_prompt(self, claims: list[GroundedClaim]):",
            "    def _build_stage2_prompt(self, claims: list[GroundedClaim], facts=None):",
        ),
    ),
    (
        "the grounding filter is skipped, so a claim may cite anything",
        "app/summary/agent.py",
        (
            "        grounded = filter_grounded_claims(\n            outcome.claims, {fact.id for fact in facts}, self.config.narrative\n        )",
            "        from app.narrative.grounding import GroundingResult\n\n        grounded = GroundingResult(valid_claims=outcome.claims, rejected_reasons=[])",
        ),
    ),
    (
        "post-check failures no longer fall back to the template",
        "app/summary/pipeline.py",
        (
            "                if all(outcome.passed for outcome in checked.outcomes):",
            "                if True:",
        ),
    ),
    (
        "quality context is appended after the summary instead of first",
        "app/summary/pipeline.py",
        (
            'return f"{record.quality_context}\\n\\n{record.summary_text}"',
            'return f"{record.summary_text}\\n\\n{record.quality_context}"',
        ),
    ),
    (
        "the template stops stating why it was used",
        "app/summary/template.py",
        (
            'lines = [f"This summary was written without a language model ({reason})."]',
            "lines = []",
        ),
    ),
    (
        "the length check is removed",
        "app/summary/postchecks.py",
        (
            "    attempt_result.outcomes.append(check_length(summary_text, config))",
            "    pass",
        ),
    ),
    (
        "the summary path stops redacting quoted column values",
        "app/summary/agent.py",
        (
            "        redacted = redact_records(quoted_records, self._privacy, SUMMARY_POLICY)",
            "        redacted = redact_records(quoted_records, None, SUMMARY_POLICY)",
        ),
    ),
    (
        "stage 1 stops recording its egress",
        "app/summary/pipeline.py",
        (
            "        if claims_outcome.egress is not None:\n            egress_records.append(claims_outcome.egress)",
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
    summary = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line]
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

    verdict = "CAUGHT" if "failed" in summary else "*** NOT CAUGHT ***"
    print(f"[{label}] {verdict}: {summary}")
    for line in failed[:2]:
        print(f"    {line}")

print("\nall files restored")
