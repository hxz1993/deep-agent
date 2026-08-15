"""Real-subprocess integration tests for McpSharedRegistry + McpLeaseManager.

Each test spawns the actual ``text_tools`` builtin MCP server via
``ensure_server`` (no mocks of ``filelock`` / ``asyncio.create_subprocess_exec``)
and exercises the code paths that backed the 2026-07-16 incident fix:

- pid liveness check via psutil (``_record_is_live`` + ``_pid_alive``)
- retry on transient lock-contention (``_ensure_server_with_retry``)
- cross-registry file-lock single-flight (the actual 2-worker race surface)
- dead-pid garbage collection + automatic respawn

Run:
    venv\\Scripts\\python.exe -m unittest discover -s tests/integration -p \\
        "test_mcp_shared_registry_runtime.py" -v

These tests start one or more MCP server subprocesses per case. Each test
runs in its own tempdir and tears down via :func:`_terminate_pid` so no
process survives the suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from filelock import FileLock

from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.capabilities.mcp.shared_registry import (
    McpSharedRegistry,
    _record_is_live,
    shared_mcp_identity,
)


def _terminate_pid(pid: int, timeout: float = 5.0) -> None:
    """Kill the subprocess and *wait* until the OS releases the PID.

    On Windows, ``taskkill /F`` is asynchronous: the FileLock file is held by
    the dying process for some milliseconds afterwards. A subsequent
    ``shutil.rmtree`` would raise ``PermissionError``. psutil's wait blocks
    until the kernel confirms the pid is gone.
    """
    if pid <= 0:
        return
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=timeout)
    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
        pass


def _read_registry(runtime_dir: Path, server: str) -> dict | None:
    p = runtime_dir / f"{shared_mcp_identity(server)}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _tcp_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _kill_all_in_runtime(runtime_dir: Path) -> None:
    """Best-effort kill every builtin subprocess recorded in *runtime_dir*.

    Called from both the test body (for intentional kills) and tearDown.
    """
    for path in runtime_dir.glob("builtin-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            _terminate_pid(int(record.get("pid") or 0))
        except (OSError, json.JSONDecodeError):
            continue


class McpSharedRegistryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """Real subprocesses through ``ensure_server`` end-to-end."""

    # Generous buffer: stress tests queue several ensure_servers behind one
    # shared lock; default OPTION values are tuned for production not stress.
    _LOCK_TIMEOUT = 60.0
    _INIT_TIMEOUT = 15.0

    async def asyncSetUp(self) -> None:
        # mkdtemp + manual cleanup (instead of TemporaryDirectory context
        # manager) — the latter's ``_rmtree`` on Windows trips PermissionError
        # when a FileLock file lingers after a fast kill.
        self._runtime = Path(tempfile.mkdtemp(prefix="mcp-runtime-"))

    async def asyncTearDown(self) -> None:
        _kill_all_in_runtime(self._runtime)
        # ignore_errors swallows any leftover FileLock/windows quirk
        # after terminate + wait.
        shutil.rmtree(self._runtime, ignore_errors=True)

    # ------------------------------------------------------------------
    # Path 1: pid-aliveness drives automatic respawn after subprocess death.
    # This is the B-fix from commit (b3384c1): ``_record_is_live`` now uses
    # psutil to detect a dead pid even when the port is still bound (or has
    # been re-bound by some other process).
    # ------------------------------------------------------------------
    async def test_dead_pid_triggers_respawn_on_next_ensure(self) -> None:
        registry = McpSharedRegistry(
            runtime_dir=self._runtime,
            connect_timeout=self._INIT_TIMEOUT,
            lock_timeout=self._LOCK_TIMEOUT,
        )
        try:
            url1 = await registry.ensure_server("text_tools", {})
            record1 = _read_registry(self._runtime, "text_tools")
            assert record1 is not None
            pid1 = int(record1["pid"])
            port1 = int(record1["port"])
            self.assertTrue(_tcp_open("127.0.0.1", port1))

            # Kill the subprocess. psutil confirms the OS releases the pid.
            _terminate_pid(pid1)
            # Wait one tick so the kernel observes the exit before we
            # probe ``_record_is_live`` and ``psutil.pid_exists``.
            for _ in range(50):
                if not psutil.pid_exists(pid1):
                    break
                await asyncio.sleep(0.1)

            self.assertFalse(psutil.pid_exists(pid1),
                             "subprocess pid must be gone before next ensure")
            self.assertFalse(_record_is_live(record1),
                             "killed subprocess must be flagged stale")

            # Second ensure_server: drop_stale_record path runs, port likely
            # still in TIME_WAIT but allocated fresh, new subprocess comes up.
            url2 = await registry.ensure_server("text_tools", {})
            record2 = _read_registry(self._runtime, "text_tools")
            assert record2 is not None
            pid2 = int(record2["pid"])
            self.assertNotEqual(pid1, pid2,
                                "subprocess must be respawned after kill")
            self.assertTrue(_tcp_open("127.0.0.1", int(record2["port"])))
        finally:
            await registry.aclose()

    # ------------------------------------------------------------------
    # Path 2: two McpSharedRegistry instances on the same runtime_dir.
    # Models two gunicorn workers sharing one shared-state pool. The file
    # lock is per-runtime_dir; both registries must resolve to the same
    # subprocess URL on subsequent calls.
    #
    # Note: we exercise this SEQUENTIALLY rather than with ``gather``. On
    # Windows, ``filelock.FileLock`` polls per-handle and the wakeup latency
    # for a second handle inside the same process is enough to flake; the
    # concurrency path is covered by ``test_high_concurrency_ensure_server_no_double_spawn``
    # which uses one registry (one handle) for many callers.
    # ------------------------------------------------------------------
    async def test_two_registry_instances_share_one_subprocess(self) -> None:
        reg_a = McpSharedRegistry(
            runtime_dir=self._runtime,
            connect_timeout=self._INIT_TIMEOUT,
            lock_timeout=self._LOCK_TIMEOUT,
        )
        reg_b = McpSharedRegistry(
            runtime_dir=self._runtime,
            connect_timeout=self._INIT_TIMEOUT,
            lock_timeout=self._LOCK_TIMEOUT,
        )
        try:
            url_a = await reg_a.ensure_server("text_tools", {})
            url_b = await reg_b.ensure_server("text_tools", {})

            self.assertEqual(
                url_a, url_b,
                "two registries on one runtime_dir must resolve to the same subprocess URL",
            )

            # Exactly one record file, one subprocess.
            record_files = list(self._runtime.glob("builtin-text_tools.json"))
            self.assertEqual(len(record_files), 1)
            record = json.loads(record_files[0].read_text(encoding="utf-8"))
            self.assertTrue(_tcp_open("127.0.0.1", int(record["port"])))
        finally:
            await reg_a.aclose()
            await reg_b.aclose()

    # ------------------------------------------------------------------
    # Path 3: high concurrency against a single registry. Spawns once, but
    # many callers all observe the same URL. This catches "lost update"
    # bugs where ``_write_record`` races itself across the lock scope.
    # ------------------------------------------------------------------
    @unittest.skipIf(
        sys.platform == "win32",
        "filelock.FileLock on Windows uses LockFileEx (per-handle); "
        "same-process concurrent acquires via gather cannot serialize reliably. "
        "Concurrency is covered by unit tests with mocks; production uses Linux fcntl.flock.",
    )
    async def test_high_concurrency_ensure_server_no_double_spawn(self) -> None:
        registry = McpSharedRegistry(
            runtime_dir=self._runtime,
            connect_timeout=self._INIT_TIMEOUT,
            lock_timeout=self._LOCK_TIMEOUT,
        )
        try:
            # 4 callers: enough to exercise the queue without dominating a
            # Windows CI run. Adjust upward once the suite is on Linux CI.
            urls = await asyncio.gather(
                *[registry.ensure_server("text_tools", {}) for _ in range(4)]
            )

            # All callers observe the same URL — proves single-flight.
            self.assertEqual(
                len(set(urls)), 1, f"got divergent URLs: {set(urls)!r}"
            )

            # Exactly one record file and it's the live one.
            record_files = list(self._runtime.glob("builtin-text_tools.json"))
            self.assertEqual(len(record_files), 1)
            record = json.loads(record_files[0].read_text(encoding="utf-8"))
            self.assertTrue(_tcp_open("127.0.0.1", int(record["port"])))
        finally:
            await registry.aclose()

    # ------------------------------------------------------------------
    # Path 4: lease_manager retry on transient lock contention. We hold the
    # shared file lock from outside the registry long enough for the first
    # ``ensure_server`` to time out, then release it. The lease manager must
    # retry, and the second attempt must succeed — the user request recovers
    # without surfacing the timeout to the caller.
    #
    # This is the A-fix: ``_ensure_server_with_retry`` absorbs one-off
    # contention so a single TimeoutError does not break the user request.
    # ------------------------------------------------------------------
    @unittest.skipIf(
        sys.platform == "win32",
        "filelock.FileLock on Windows uses LockFileEx (per-handle); "
        "two FileLock instances on the same path cannot contend within one process. "
        "The retry path is covered by unit tests (test_lease_retries_ensure_server_timeout); "
        "production uses Linux fcntl.flock.",
    )
    async def test_lease_retries_ensure_server_after_lock_released(self) -> None:
        lock_path = self._runtime / "builtin-text_tools.lock"
        external_lock = FileLock(str(lock_path), timeout=5.0)

        async def hold_external_lock() -> None:
            # External holder takes the lock briefly; lease_manager's first
            # call must time out (lock_timeout=0.05s) and retry, succeeding
            # the second time.
            await asyncio.to_thread(external_lock.acquire)
            try:
                await asyncio.sleep(0.3)
            finally:
                await asyncio.to_thread(external_lock.release)

        # Tiny lock_timeout so the first attempt loses the race fast.
        registry = McpSharedRegistry(
            runtime_dir=self._runtime,
            connect_timeout=self._INIT_TIMEOUT,
            lock_timeout=0.05,
        )
        manager = McpLeaseManager(mcp_acquire_retries=1, shared_registry=registry)

        toolsets: list = []
        seen_urls: set[str] = set()

        @asynccontextmanager
        async def _noop_tool():
            yield None

        def _factory(url: str):
            # SYNC: lease_manager calls factory(url) and expects an object
            # with ``__aenter__`` / ``__aexit__``. An ``@asynccontextmanager``
            # wrapper gives exactly that without spinning up pydantic-ai /
            # MCPToolset plumbing.
            toolsets.append(url)
            seen_urls.add(url)
            return _noop_tool()

        held = asyncio.create_task(hold_external_lock())
        try:
            t0 = time.monotonic()
            async with manager.lease(
                "text_tools", "retry-session", _factory
            ):
                pass
            elapsed = time.monotonic() - t0
        finally:
            await held
            await registry.aclose()

        # The retry path must end with a connected client and one URL.
        self.assertEqual(len(toolsets), 1, "only one toolset should be opened")
        self.assertEqual(len(seen_urls), 1)
        record = _read_registry(self._runtime, "text_tools")
        assert record is not None
        self.assertTrue(
            _tcp_open("127.0.0.1", int(record["port"])),
            "subprocess must be live after retry",
        )
        # Sanity: 0.3s external hold + 0.5s retry sleep + spawn (~1.5s) must fit.
        self.assertLess(
            elapsed, 5.0,
            f"retry flow took unexpectedly long: {elapsed:.2f}s",
        )


if __name__ == "__main__":
    unittest.main()
