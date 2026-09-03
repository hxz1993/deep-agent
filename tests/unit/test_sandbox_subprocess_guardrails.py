from __future__ import annotations

import multiprocessing as mp
import os
import socket
import sys
import tempfile
import unittest

from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.subprocess_dev.guardrails import (
    SoftGuardrailResult,
    apply_soft_guardrails,
    try_apply_landlock,
    try_apply_network_seccomp,
)
from deep_agent.sandbox.subprocess_dev.preexec import build_subprocess_preexec
from deep_agent.sandbox.types import ExecuteRequest
from tests.helpers.sandbox import sandbox_test_python


def _run_in_child(fn, *args):
    queue: mp.Queue = mp.Queue()

    def _target() -> None:
        try:
            queue.put(("ok", fn(*args)))
        except Exception as exc:  # pragma: no cover - surfaced via queue
            queue.put(("err", repr(exc)))

    proc = mp.Process(target=_target)
    proc.start()
    proc.join(15)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
        raise AssertionError("child process hung")
    if queue.empty():
        raise AssertionError(f"no child result, exit={proc.exitcode}")
    status, payload = queue.get()
    if status != "ok":
        raise AssertionError(payload)
    return payload


@unittest.skipUnless(sys.platform == "linux", "soft guardrails are Linux-only")
class SoftGuardrailUnitTests(unittest.TestCase):
    def test_network_seccomp_blocks_inet_allows_unix(self) -> None:
        def probe() -> dict[str, object]:
            applied = try_apply_network_seccomp()
            out: dict[str, object] = {"applied": applied}
            for label, family in (
                ("inet", socket.AF_INET),
                ("unix", socket.AF_UNIX),
            ):
                try:
                    sock = socket.socket(
                        family,
                        socket.SOCK_STREAM if family != socket.AF_UNIX else socket.SOCK_DGRAM,
                    )
                    sock.close()
                    out[label] = "ok"
                except OSError as exc:
                    out[label] = f"err:{exc.errno}"
            return out

        result = _run_in_child(probe)
        if not result["applied"]:
            self.skipTest("libseccomp unavailable or failed to load")
        self.assertEqual(result["inet"], "err:1")
        self.assertEqual(result["unix"], "ok")

    def test_landlock_limits_writes_to_work_dir(self) -> None:
        work_dir = tempfile.mkdtemp(prefix="soft-fs-")

        def probe(path: str) -> dict[str, object]:
            applied = try_apply_landlock(path)
            out: dict[str, object] = {"applied": applied}
            try:
                with open(os.path.join(path, "inside.txt"), "w", encoding="utf-8") as handle:
                    handle.write("ok")
                out["inside"] = "ok"
            except OSError as exc:
                out["inside"] = f"err:{exc.errno}"
            try:
                with open("/tmp/soft-guard-landlock-outside", "w", encoding="utf-8") as handle:
                    handle.write("no")
                out["outside"] = "ok"
            except OSError as exc:
                out["outside"] = f"err:{exc.errno}"
            return out

        result = _run_in_child(probe, work_dir)
        if not result["applied"]:
            self.skipTest("Landlock unavailable on this kernel")
        self.assertEqual(result["inside"], "ok")
        self.assertEqual(result["outside"], "err:13")

    def test_apply_soft_guardrails_never_raises_and_degrades(self) -> None:
        # Must run in a child: seccomp is process-global and would break later
        # tests in the same unittest worker (e.g. MCP shared_registry sockets).
        def probe() -> bool:
            result = apply_soft_guardrails(work_dir="/nonexistent-soft-guard-dir")
            return isinstance(result, SoftGuardrailResult)

        self.assertTrue(_run_in_child(probe))

    def test_preexec_builder_includes_soft_guards_without_rlimits(self) -> None:
        preexec = build_subprocess_preexec(
            work_dir=tempfile.mkdtemp(prefix="soft-pre-"),
            memory_as_bytes=None,
            cpu_seconds=None,
            max_pids=None,
        )
        self.assertIsNotNone(preexec)


@unittest.skipUnless(sys.platform == "linux", "soft guardrails are Linux-only")
class SoftGuardrailBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=2,
        )

    async def asyncTearDown(self) -> None:
        await self.backend.aclose()

    async def test_execute_still_ok_with_guards(self) -> None:
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="soft-ok",
                code="1 + 1",
                inputs={},
                timeout_ms=30_000,
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result, 2)
        self.assertEqual(result.security_level, "none")

    async def test_execute_does_not_apply_blanket_network_seccomp(self) -> None:
        code = (
            "import socket\n"
            "outcome = 'allowed'\n"
            "try:\n"
            "    socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "except OSError as e:\n"
            "    outcome = f'blocked:{e.errno}'\n"
            "outcome\n"
        )
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="soft-net",
                code=code,
                inputs={},
                timeout_ms=30_000,
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result, "allowed")

    async def test_execute_blocks_write_outside_work_dir_when_landlock_works(self) -> None:
        code = (
            "outcome = 'allowed'\n"
            "try:\n"
            "    open('/tmp/soft-guard-backend-outside', 'w').write('x')\n"
            "except OSError as e:\n"
            "    outcome = f'blocked:{e.errno}'\n"
            "outcome\n"
        )
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="soft-fs",
                code=code,
                inputs={},
                timeout_ms=30_000,
            )
        )
        self.assertEqual(result.status, "ok")
        if result.result == "allowed":
            self.skipTest("Landlock did not apply in worker (degraded)")
        self.assertEqual(result.result, "blocked:13")


if __name__ == "__main__":
    unittest.main()
