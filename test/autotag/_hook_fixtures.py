"""Top-level callables used by multi-process hook-timeout tests.

WARNING: The contents of this module MUST stay at the top level (not
nested inside test classes or functions) because the process-based
timeout backend resolves the handler via
``import_module(modulename).funcname``.  Lambdas, closures, and nested
functions are not pickleable / resolvable from a child process.
"""

from __future__ import annotations

import threading
import time


# ---------------------------------------------------------------------------
# Process-mode helpers: module-level, importable by name.
# ---------------------------------------------------------------------------


def infinite_cpu_loop(**_kwargs) -> None:
    """Simulate a runaway pure-CPU plugin hook.

    A thread-based timeout CANNOT stop this because the GIL is never
    released.  Only a process-level ``SIGTERM`` / ``SIGKILL`` can kill it.
    """
    # Spin the CPU without any syscalls / sleeps.
    while True:
        pass


def sleep_forever(**_kwargs) -> None:
    """Simulate a plugin hook blocked on I/O.

    This IS stoppable by thread-based timeout (because ``time.sleep``
    releases the GIL), but also by the new process-based backend.
    """
    while True:
        time.sleep(10)


def raise_value_error(**_kwargs) -> None:
    raise ValueError("intentional ValueError from hook fixture")


def raise_runtime_error(**_kwargs) -> None:
    raise RuntimeError("intentional RuntimeError from hook fixture")


def return_forty_two(**_kwargs) -> int:
    return 42


def echo_context(context=None, **_kwargs):
    """Return a pickleable subset of the context for round-trip testing."""
    if context is None:
        return None
    return {
        "stage_name": getattr(getattr(context, "stage", None), "name", None),
        "extra_keys": sorted(list(getattr(context, "extra", {}).keys())),
    }


# ---------------------------------------------------------------------------
# Thread-mode helpers: may be bound locally, but re-exposed here for symmetry.
# ---------------------------------------------------------------------------


def sleep_for(seconds: float, **_kwargs) -> str:
    time.sleep(seconds)
    return f"slept {seconds}s"


def increment_counter(**_kwargs) -> str:
    """Used to verify that non-failing listeners still run after a timeout."""
    return "ok"
