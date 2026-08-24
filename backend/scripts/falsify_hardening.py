"""Break each hardening guarantee in turn; report which tests notice.

A guard nobody has seen fail is a guard nobody has shown to work.
"""

import pathlib
import shutil
import subprocess
import sys

BACKEND = pathlib.Path(".")
PY = str(BACKEND / ".venv/Scripts/python.exe")

CASES = [
    (
        "middleware not registered",
        "app/main.py",
        ("app.add_middleware(RateLimitMiddleware)", "# app.add_middleware(RateLimitMiddleware)"),
    ),
    (
        "loopback exemption removed",
        "app/security/rate_limit.py",
        ("        if is_local_address(host):\n            return await call_next(request)", "        if False:\n            return await call_next(request)"),
    ),
    (
        "upload stops sniffing content",
        "app/routers/sources.py",
        ("    mismatch = detect_mismatch(head, extension)", "    mismatch = None if True else detect_mismatch(head, extension)"),
    ),
    (
        "contract size gate removed",
        "app/contract.py",
        ("        rows = len(self.data)\n        if rows > MAX_INGEST_ROWS:", "        rows = len(self.data)\n        if False:"),
    ),
]

for label, rel, (old, new) in CASES:
    path = BACKEND / rel
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy(path, backup)
    text = path.read_text()
    if old not in text:
        print(f"[{label}] SKIPPED - anchor not found in {rel}")
        backup.unlink()
        continue
    path.write_text(text.replace(old, new, 1))

    result = subprocess.run(
        [PY, "-m", "pytest", "tests/test_security_hardening.py", "-q", "--no-header", "-x", "--tb=no"],
        capture_output=True,
        text=True,
    )
    tail = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line]
    summary = tail[-1] if tail else "no summary"
    failing = [line for line in result.stdout.splitlines() if line.startswith("FAILED")]

    shutil.copy(backup, path)
    backup.unlink()

    verdict = "CAUGHT" if "failed" in summary else "*** NOT CAUGHT ***"
    print(f"[{label}] {verdict}: {summary}")
    for line in failing[:3]:
        print(f"    {line}")

print("\nall files restored")
