"""Process-local live execution scope; no durable snapshots or leases."""
from __future__ import annotations

import os
import socket
from collections import Counter
from contextlib import contextmanager
from threading import RLock
from typing import Any
from uuid import uuid4

_BOOT_ID = uuid4().hex
_LIVE_RUNS: Counter[str] = Counter()
_LIVE_RUNS_LOCK = RLock()


@contextmanager
def live_run(run_id: str | None):
    """Protect local execution, including nested runner/SDK lifetimes and retries.

    Refcounts let a runner cover a provider-retry gap while each SDK attempt
    independently protects normal streaming and non-streaming execution.
    """
    if not run_id:
        yield
        return
    with _LIVE_RUNS_LOCK:
        _LIVE_RUNS[run_id] += 1
    try:
        yield
    finally:
        with _LIVE_RUNS_LOCK:
            _LIVE_RUNS[run_id] -= 1
            if _LIVE_RUNS[run_id] <= 0:
                del _LIVE_RUNS[run_id]


def run_is_live(run_id: str | None) -> bool:
    with _LIVE_RUNS_LOCK:
        return bool(run_id and _LIVE_RUNS.get(run_id))


def run_owner() -> dict[str, Any]:
    return {"host": socket.gethostname(), "pid": os.getpid(), "boot_id": _BOOT_ID}
