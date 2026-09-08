"""Small leases protect live Runs during overlapping API deployments."""
from __future__ import annotations

import os
import socket
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from deep_agent.time import ensure_utc

LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 10
_BOOT_ID = uuid4().hex
_LIVE_RUNS: Counter[str] = Counter()
_LIVE_RUNS_LOCK = RLock()


@contextmanager
def live_run(run_id: str | None):
    """Protect local execution, including nested runner/SDK lifetimes and retries.

    The lease still covers the short gap between SDK exit and terminal commit.
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


def owned_progress_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {**payload, "owner": run_owner(),
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()}


def progress_is_abandoned(
    payload: Mapping[str, Any], updated_at: datetime, *, run_id: str | None = None,
) -> bool:
    owner = payload.get("owner") or {}
    if owner.get("host") == socket.gethostname():
        try:
            pid = int(owner["pid"])
            if pid <= 0:
                return False
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except (KeyError, ValueError, TypeError, PermissionError):
            pass
        else:
            # We cannot inspect another live local process's in-memory Runs.
            # For this process we can distinguish real execution from a failed
            # terminal write left behind by an already exited SDK Run.
            if pid != os.getpid():
                return False
            if owner.get("boot_id") == _BOOT_ID and run_is_live(run_id):
                return False
    try:
        expires = ensure_utc(datetime.fromisoformat(str(payload["lease_expires_at"])))
    except (KeyError, TypeError, ValueError):
        expires = ensure_utc(updated_at) + timedelta(seconds=LEASE_SECONDS)
    return expires <= datetime.now(timezone.utc)
