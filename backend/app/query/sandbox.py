"""Part 3: sandboxed execution of validated pandas code.

Runs in a genuinely separate OS process (multiprocessing, 'spawn' context -
consistent behavior on every platform, never relies on fork semantics),
with a wall-clock timeout and a memory cap, and returns a size-capped,
JSON-safe result. A timeout, a memory kill, or an exception from the
generated code is ALWAYS an escalation - this module never retries.

"No filesystem access, no network" is enforced by WHAT'S IN THE EXEC
NAMESPACE, not by OS-level process isolation: the child's globals contain
only a small, pure builtins allowlist (app/query/pandas_validation.py's
ALLOWED_BUILTIN_NAMES - no open, no __import__, no socket) plus the
dataframe itself. Combined with the AST allowlist already having rejected
anything that could reach for such a capability before this module ever
runs the code, there is no path from the executed code to the filesystem
or network - not a promise enforced by convention, a namespace that
structurally does not contain the means.

MEMORY ENFORCEMENT IS PLATFORM-DEPENDENT, disclosed rather than silently
assumed: on POSIX, `resource.setrlimit(RLIMIT_AS, ...)` inside the child is
a hard OS-enforced cap - the kernel itself refuses the allocation. `resource`
does not exist on Windows, so there the parent-side psutil watchdog (polling
the child's RSS and killing it on breach) is the only enforcement available -
a check, not a structural guarantee, and weaker by exactly that much. Both
paths run unconditionally so the behavior is the same on every platform;
only the strength of the guarantee differs.
"""

from __future__ import annotations

import json
import multiprocessing
import queue as queue_module
import threading
import time
from dataclasses import dataclass

import pandas as pd
import psutil

from app.query.pandas_validation import ALLOWED_BUILTIN_NAMES, DATAFRAME_NAME, RESULT_NAME

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB
DEFAULT_ROW_CAP = 10_000
_MEMORY_POLL_INTERVAL_SECONDS = 0.05
_KILL_GRACE_SECONDS = 1.0


@dataclass
class SandboxResult:
    success: bool
    value: dict | None = None  # JSON-safe result payload - see _serialize_result
    truncated: bool = False
    row_count: int | None = None
    error: str | None = None
    timed_out: bool = False
    memory_killed: bool = False
    duration_seconds: float | None = None


def _safe_builtins() -> dict:
    import builtins as real_builtins

    return {name: getattr(real_builtins, name) for name in ALLOWED_BUILTIN_NAMES if hasattr(real_builtins, name)}


def _apply_memory_limit(limit_bytes: int | None) -> None:
    if limit_bytes is None:
        return
    try:
        import resource  # POSIX only

        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except ImportError:
        pass  # Windows: no equivalent - the parent-side psutil watchdog is the enforcement there


def serialize_dataframe(df: pd.DataFrame, row_cap: int) -> dict:
    """Shared by the pandas sandbox result and app/query/sql_execution.py's
    result (via app/query/pipeline.py) so both paths cap/truncate rows the
    same way and hand the API the same {type, columns, rows} shape
    regardless of which query_kind actually ran."""
    total = len(df)
    capped = df.head(row_cap)
    return {
        "value": {
            "type": "dataframe",
            "columns": [str(c) for c in capped.columns],
            "rows": json.loads(capped.to_json(orient="records", date_format="iso")),
        },
        "truncated": total > row_cap,
        "row_count": total,
    }


def _serialize_result(result: object, row_cap: int) -> dict:
    if isinstance(result, pd.DataFrame):
        return serialize_dataframe(result, row_cap)
    if isinstance(result, pd.Series):
        total = len(result)
        capped = result.head(row_cap)
        return {
            "value": {
                "type": "series",
                "name": str(capped.name) if capped.name is not None else None,
                "values": json.loads(capped.to_json(orient="values", date_format="iso")),
            },
            "truncated": total > row_cap,
            "row_count": total,
        }
    # A bare `df['x'].sum()`/`.mean()`/etc returns a numpy scalar (int64,
    # float64, bool_...), not a native Python type. json.dumps's `default`
    # hook applies to the top-level value too, so without this conversion a
    # numpy scalar silently becomes the STRING "30" instead of the number
    # 30 - a wrong answer shape, not a serialization error, so nothing
    # would have raised to catch it.
    if hasattr(result, "item"):
        try:
            result = result.item()
        except ValueError:
            pass  # e.g. a multi-element numpy array - falls through to default=str below
    try:
        safe_value = json.loads(json.dumps(result, default=str))
    except TypeError:
        safe_value = str(result)
    return {"value": {"type": "scalar", "value": safe_value}, "truncated": False, "row_count": None}


def _child_main(result_queue: multiprocessing.Queue, code: str, df: pd.DataFrame, memory_limit_bytes: int | None, row_cap: int) -> None:
    _apply_memory_limit(memory_limit_bytes)
    try:
        restricted_globals: dict = {"__builtins__": _safe_builtins(), DATAFRAME_NAME: df}
        compiled = compile(code, "<generated_pandas_code>", "exec")
        exec(compiled, restricted_globals)  # the AST allowlist already validated this code before it ever reached here
        result = restricted_globals.get(RESULT_NAME)
        result_queue.put({"success": True, **_serialize_result(result, row_cap)})
    except MemoryError:
        result_queue.put({"success": False, "error": "memory limit exceeded"})
    except Exception as exc:  # noqa: BLE001 - any exception from generated code becomes a clean escalation, never a crash
        result_queue.put({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def run_pandas_sandbox(
    code: str,
    df: pd.DataFrame,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    row_cap: int = DEFAULT_ROW_CAP,
) -> SandboxResult:
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(target=_child_main, args=(result_queue, code, df, memory_limit_bytes, row_cap), daemon=True)

    started_at = time.monotonic()
    process.start()

    memory_killed = threading.Event()
    stop_watchdog = threading.Event()

    def _watchdog() -> None:
        try:
            handle = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return
        while not stop_watchdog.is_set() and process.is_alive():
            try:
                if handle.memory_info().rss > memory_limit_bytes:
                    memory_killed.set()
                    process.kill()
                    return
            except psutil.NoSuchProcess:
                return
            time.sleep(_MEMORY_POLL_INTERVAL_SECONDS)

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
    watchdog_thread.start()

    process.join(timeout=timeout_seconds)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join(timeout=_KILL_GRACE_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(timeout=_KILL_GRACE_SECONDS)

    stop_watchdog.set()
    watchdog_thread.join(timeout=_KILL_GRACE_SECONDS)
    duration = time.monotonic() - started_at

    if timed_out:
        return SandboxResult(success=False, error="execution exceeded the wall-clock timeout", timed_out=True, duration_seconds=duration)
    if memory_killed.is_set():
        return SandboxResult(success=False, error="execution exceeded the memory limit and was killed", memory_killed=True, duration_seconds=duration)

    try:
        outcome = result_queue.get_nowait()
    except queue_module.Empty:
        # Not timed out, not memory-killed, but nothing came back - a hard
        # crash/segfault in the child. Still an escalation, never a retry.
        return SandboxResult(
            success=False, error=f"process exited (code={process.exitcode}) without producing a result", duration_seconds=duration
        )

    if not outcome.get("success"):
        return SandboxResult(success=False, error=outcome.get("error", "execution failed"), duration_seconds=duration)

    return SandboxResult(
        success=True,
        value=outcome["value"],
        truncated=outcome.get("truncated", False),
        row_count=outcome.get("row_count"),
        duration_seconds=duration,
    )
