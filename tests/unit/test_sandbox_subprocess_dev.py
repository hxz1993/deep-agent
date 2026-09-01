from __future__ import annotations

import asyncio
import os
import resource
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.sandbox.framing import encode_frame
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.subprocess_dev.env import build_worker_env, repo_root
from deep_agent.sandbox.subprocess_dev.preexec import build_subprocess_preexec
from deep_agent.sandbox.subprocess_dev.protocol import BACKEND_NAME
from deep_agent.sandbox.subprocess_dev.rlimit import (
    rlimit_as_supported,
    rss_bytes_to_rlimit_as,
)
from deep_agent.sandbox.types import ExecuteRequest, SandboxInputFile
from tests.helpers.sandbox import FAKE_SANDBOX_PYTHON, sandbox_test_python


def _worker_pids() -> list[int]:
    completed = subprocess.run(
        ["pgrep", "-f", "deep_agent.sandbox.worker"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [int(pid) for pid in completed.stdout.split() if pid.strip()]


class BuildWorkerEnvTests(unittest.TestCase):
    def test_strips_pythonpath_and_pythonhome(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "PYTHONPATH": "/evil",
                "PYTHONHOME": "/evil-home",
                "DEEP_AGENT_SECRET": "token",
            },
            clear=True,
        ):
            env = build_worker_env()
        self.assertEqual(env.get("PATH"), "/usr/bin")
        self.assertEqual(env.get("PYTHONPATH"), str(repo_root()))
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("DEEP_AGENT_SECRET", env)
        self.assertEqual(env.get("PYTHONNOUSERSITE"), "1")
        self.assertEqual(env.get("NO_PROXY"), "*")
        self.assertEqual(env.get("no_proxy"), "*")

    def test_rss_budget_translates_to_rlimit_as(self) -> None:
        self.assertEqual(rss_bytes_to_rlimit_as(512 * 1024 * 1024), 1536 * 1024 * 1024)
        self.assertEqual(rss_bytes_to_rlimit_as(100, ratio=3), 300)


class SubprocessBackendTests(unittest.IsolatedAsyncioTestCase):
    def _mock_worker_process(self, *, stdout: bytes = b"", returncode: int = 0) -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.pid = 4242
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdin.wait_closed = AsyncMock()
        mock_proc.stdout = asyncio.StreamReader()
        if stdout:
            mock_proc.stdout.feed_data(stdout)
        mock_proc.stdout.feed_eof()
        mock_proc.stderr = asyncio.StreamReader()
        mock_proc.stderr.feed_eof()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = returncode
        return mock_proc

    async def asyncSetUp(self) -> None:
        self.backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=4,
        )

    async def asyncTearDown(self) -> None:
        await self.backend.aclose()

    async def test_health_reports_no_security(self) -> None:
        health = await self.backend.health()
        self.assertTrue(health.enabled)
        self.assertEqual(health.backend, BACKEND_NAME)
        self.assertEqual(health.state_scope, "request")
        self.assertEqual(health.security_level, "none")
        self.assertIsNotNone(health.resource_limits)
        assert health.resource_limits is not None
        self.assertEqual(health.resource_limits.memory_bytes, 512 * 1024 * 1024)
        expected_enforcement = "rlimit_as" if rlimit_as_supported() else "none"
        expected_as = 1536 * 1024 * 1024 if rlimit_as_supported() else None
        self.assertEqual(health.resource_limits.memory_enforcement, expected_enforcement)
        self.assertEqual(health.resource_limits.memory_as_bytes, expected_as)
        self.assertEqual(health.resource_limits.cpu_seconds, 30)
        self.assertIsNone(health.resource_limits.max_pids)

    async def test_execute_uses_configured_python_not_sys_executable(self) -> None:
        response = {
            "request_id": "argv-1",
            "status": "ok",
            "stdout": "",
            "stderr": "",
            "result": 1,
            "error": None,
            "truncated": {
                "stdout": False,
                "stderr": False,
                "traceback": False,
                "result": False,
            },
        }
        configured = FAKE_SANDBOX_PYTHON
        backend = SubprocessBackend(
            python_executable=configured,
            max_concurrency=4,
        )
        try:
            with patch(
                "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec:
                mock_exec.return_value = self._mock_worker_process(
                    stdout=encode_frame(response),
                )

                request = ExecuteRequest(request_id="argv-1", code="1", inputs={})
                result = await backend.execute(request)

            mock_exec.assert_awaited_once()
            args = mock_exec.await_args.args
            self.assertEqual(args[0], configured)
            self.assertNotEqual(args[0], sys.executable)
            self.assertEqual(args[1:], ("-m", "deep_agent.sandbox.worker"))
            kwargs = mock_exec.await_args.kwargs
            self.assertTrue(kwargs.get("start_new_session"))
            self.assertEqual(kwargs.get("stderr"), asyncio.subprocess.DEVNULL)
            self.assertIsNotNone(kwargs.get("preexec_fn"))
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.result, 1)
        finally:
            await backend.aclose()

    async def test_resource_preexec_sets_posix_rlimits(self) -> None:
        preexec = build_subprocess_preexec(
            work_dir=tempfile.mkdtemp(prefix="rlimit-test-"),
            memory_as_bytes=64 * 1024 * 1024,
            cpu_seconds=5,
            max_pids=16,
        )
        self.assertIsNotNone(preexec)
        assert preexec is not None
        # Apply in a forked child so the test process itself is not limited.
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import resource; "
            "print(resource.getrlimit(resource.RLIMIT_AS)[0]); "
            "print(resource.getrlimit(resource.RLIMIT_CPU)[0]); "
            "print(resource.getrlimit(resource.RLIMIT_NPROC)[0])",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=preexec,
        )
        stdout, _ = await proc.communicate()
        self.assertEqual(proc.returncode, 0)
        as_limit, cpu_limit, nproc_limit = stdout.decode().splitlines()
        expected_as = 64 * 1024 * 1024 if rlimit_as_supported() else resource.RLIM_INFINITY
        self.assertEqual(int(as_limit), expected_as)
        self.assertEqual(int(cpu_limit), 5)
        self.assertEqual(int(nproc_limit), 16)

    async def test_rlimit_kill_maps_to_execution_failed_not_memory_limit(self) -> None:
        if not rlimit_as_supported():
            self.skipTest("finite RLIMIT_AS is not supported on this platform")
        backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=1,
            # RSS intent; subprocess-dev applies ×3 as RLIMIT_AS (~48MiB).
            memory_limit_bytes=16 * 1024 * 1024,
            cpu_limit_seconds=30,
            max_pids=64,
        )
        created_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def _tracking_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created_dirs.append(path)
            return path

        try:
            with patch(
                "deep_agent.sandbox.subprocess_dev.backend.tempfile.mkdtemp",
                side_effect=_tracking_mkdtemp,
            ):
                result = await backend.execute(
                    ExecuteRequest(
                        request_id="mem-1",
                        code="x = bytearray(80 * 1024 * 1024)",
                        inputs={},
                        timeout_ms=10_000,
                    )
                )
        finally:
            await backend.aclose()
        self.assertEqual(result.status, "error")
        assert result.error is not None
        # AS headroom may surface as worker MemoryError or process death; never isolation codes.
        self.assertIn(result.error.code, {"EXECUTION_FAILED", "PYTHON_EXCEPTION"})
        self.assertNotEqual(result.error.code, "MEMORY_LIMIT")
        self.assertNotEqual(result.error.code, "PID_LIMIT")
        self.assertNotEqual(result.error.code, "SECURITY_POLICY_VIOLATION")
        # Parent finally must remove the per-request work dir even when AS kills the worker.
        self.assertEqual(len(created_dirs), 1)
        self.assertFalse(
            os.path.exists(created_dirs[0]),
            f"sandbox work_dir left behind after AS limit: {created_dirs[0]}",
        )

    async def test_frame_isolation_error_codes_are_remapped(self) -> None:
        response = {
            "request_id": "remap-1",
            "status": "error",
            "stdout": "",
            "stderr": "",
            "result": None,
            "error": {
                "code": "MEMORY_LIMIT",
                "message": "should not leak",
                "traceback": None,
            },
            "truncated": {
                "stdout": False,
                "stderr": False,
                "traceback": False,
                "result": False,
            },
        }
        with patch(
            "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = self._mock_worker_process(
                stdout=encode_frame(response),
            )
            result = await self.backend.execute(
                ExecuteRequest(request_id="remap-1", code="1", inputs={})
            )
        self.assertEqual(result.status, "error")
        assert result.error is not None
        self.assertEqual(result.error.code, "EXECUTION_FAILED")

    async def test_execute_passes_whitelisted_env_only(self) -> None:
        response = {
            "request_id": "env-1",
            "status": "ok",
            "stdout": "",
            "stderr": "",
            "result": None,
            "error": None,
            "truncated": {
                "stdout": False,
                "stderr": False,
                "traceback": False,
                "result": False,
            },
        }
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "LANG": "C.UTF-8",
                "PYTHONPATH": "/evil",
                "PYTHONHOME": "/evil-home",
                "DEEP_AGENT_TOKEN": "secret",
            },
            clear=True,
        ):
            with patch(
                "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec:
                mock_exec.return_value = self._mock_worker_process(
                    stdout=encode_frame(response),
                )

                request = ExecuteRequest(request_id="env-1", code="pass", inputs={})
                await self.backend.execute(request)

                env = mock_exec.await_args.kwargs["env"]
                self.assertEqual(env["PATH"], "/usr/bin")
                self.assertEqual(env["LANG"], "C.UTF-8")
                self.assertEqual(env["PYTHONPATH"], str(repo_root()))
                self.assertNotIn("PYTHONHOME", env)
                self.assertNotIn("DEEP_AGENT_TOKEN", env)

    async def test_each_execute_uses_new_pid_and_temp_cwd(self) -> None:
        first = await self.backend.execute(
            ExecuteRequest(
                request_id="pid-1",
                code='import os; print(os.getpid()); print(os.getcwd())',
                inputs={},
            )
        )
        second = await self.backend.execute(
            ExecuteRequest(
                request_id="pid-2",
                code='import os; print(os.getpid()); print(os.getcwd())',
                inputs={},
            )
        )

        self.assertEqual(first.status, "ok")
        self.assertEqual(second.status, "ok")
        first_lines = first.stdout.strip().splitlines()
        second_lines = second.stdout.strip().splitlines()
        self.assertNotEqual(first_lines[0], second_lines[0])
        self.assertNotEqual(first_lines[1], second_lines[1])
        for line in (first_lines[1], second_lines[1]):
            self.assertIn("deep-agent-sandbox-", line)
            self.assertTrue(
                os.path.realpath(line).startswith(os.path.realpath(tempfile.gettempdir()))
            )

    async def test_successful_expression(self) -> None:
        result = await self.backend.execute(
            ExecuteRequest(request_id="ok-1", code="2 + 2", inputs={})
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result, 4)
        self.assertIsNone(result.error)
        self.assertEqual(result.backend, BACKEND_NAME)
        self.assertEqual(result.security_level, "none")

    async def test_expression_reads_authorized_input_and_collects_published_output(self) -> None:
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="files-1",
                code=(
                    "from deepagent_files import input_path, publish_path\n"
                    "source = input_path('source-id')\n"
                    "target = publish_path('result.txt', mime='text/plain')\n"
                    "open(target, 'wb').write(open(source, 'rb').read() + b'!')\n"
                    "{'saved': True}"
                ),
                files=(
                    SandboxInputFile.from_bytes(
                        "inputs/source.txt",
                        b"hello",
                        file_id="source-id",
                    ),
                ),
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result, {"saved": True})
        self.assertEqual(len(result.published_files), 1)
        output = result.published_files[0]
        self.assertEqual(output.file_name, "result.txt")
        self.assertEqual(output.mime, "text/plain")
        self.assertEqual(output.data, b"hello!")
        self.assertNotIn("published_files", result.model_dump(mode="json"))

    async def test_missing_published_file_fails_without_partial_output(self) -> None:
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="files-missing",
                code=(
                    "from deepagent_files import publish_path\n"
                    "publish_path('missing.txt', mime='text/plain')\n"
                    "{'saved': True}"
                ),
            )
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.published_files, ())
        assert result.error is not None
        self.assertIn("FileNotFoundError", result.error.message)

    async def test_public_network_is_allowed_and_private_network_is_blocked(self) -> None:
        public = await self.backend.execute(
            ExecuteRequest(
                request_id="public-net",
                code=(
                    "import urllib.request\n"
                    "urllib.request.urlopen('https://example.com', timeout=10).status"
                ),
                timeout_ms=15_000,
            )
        )
        self.assertEqual(public.status, "ok")
        self.assertEqual(public.result, 200)

        private = await self.backend.execute(
            ExecuteRequest(
                request_id="private-net",
                code=(
                    "import socket\n"
                    "socket.create_connection(('127.0.0.1', 80), timeout=1)"
                ),
                timeout_ms=5_000,
            )
        )
        self.assertEqual(private.status, "error")
        assert private.error is not None
        self.assertIn("blocks non-public", private.error.message)

    async def test_timeout_returns_timeout_error(self) -> None:
        result = await self.backend.execute(
            ExecuteRequest(
                request_id="timeout-1",
                code="import time; time.sleep(30)",
                inputs={},
                timeout_ms=200,
            )
        )
        self.assertEqual(result.status, "error")
        assert result.error is not None
        self.assertEqual(result.error.code, "TIMEOUT")
        await asyncio.sleep(0.2)
        self.assertEqual(_worker_pids(), [])

    async def test_cancel_kills_process_group(self) -> None:
        request = ExecuteRequest(
            request_id="cancel-1",
            code="import time; time.sleep(120)",
            inputs={},
            timeout_ms=120_000,
        )
        task = asyncio.create_task(self.backend.execute(request))
        for _ in range(50):
            if _worker_pids():
                break
            await asyncio.sleep(0.05)
        self.assertTrue(_worker_pids())

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        for _ in range(50):
            if not _worker_pids():
                break
            await asyncio.sleep(0.05)
        self.assertEqual(_worker_pids(), [])

    async def test_nonzero_exit_returns_execution_failed(self) -> None:
        with patch(
            "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = self._mock_worker_process(returncode=1)

            result = await self.backend.execute(
                ExecuteRequest(request_id="fail-1", code="pass", inputs={})
            )

        self.assertEqual(result.status, "error")
        assert result.error is not None
        self.assertEqual(result.error.code, "EXECUTION_FAILED")

    async def test_corrupt_response_returns_protocol_error(self) -> None:
        with patch(
            "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = self._mock_worker_process(stdout=b"not-a-frame")

            result = await self.backend.execute(
                ExecuteRequest(request_id="proto-1", code="pass", inputs={})
            )

        self.assertEqual(result.status, "error")
        assert result.error is not None
        self.assertEqual(result.error.code, "BACKEND_PROTOCOL_ERROR")

    async def test_oversized_response_returns_protocol_error(self) -> None:
        oversized_stdout = "x" * (64 * 1024 + 1)
        response = {
            "request_id": "proto-2",
            "status": "ok",
            "stdout": oversized_stdout,
            "stderr": "",
            "result": None,
            "error": None,
            "truncated": {
                "stdout": False,
                "stderr": False,
                "traceback": False,
                "result": False,
            },
        }
        with patch(
            "deep_agent.sandbox.subprocess_dev.backend.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = self._mock_worker_process(
                stdout=encode_frame(response),
            )

            result = await self.backend.execute(
                ExecuteRequest(request_id="proto-2", code="pass", inputs={})
            )

        self.assertEqual(result.status, "error")
        assert result.error is not None
        self.assertEqual(result.error.code, "BACKEND_PROTOCOL_ERROR")

    async def test_capacity_exceeded_when_semaphore_full(self) -> None:
        limited = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=1,
        )
        try:
            blocker = ExecuteRequest(
                request_id="block-1",
                code="import time; time.sleep(5)",
                inputs={},
                timeout_ms=10_000,
            )
            quick = ExecuteRequest(request_id="block-2", code="1", inputs={})

            first_task = asyncio.create_task(limited.execute(blocker))
            for _ in range(50):
                if limited.semaphore_locked():
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(limited.semaphore_locked())

            second = await limited.execute(quick)
            self.assertEqual(second.status, "error")
            assert second.error is not None
            self.assertEqual(second.error.code, "CAPACITY_EXCEEDED")

            first_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first_task
        finally:
            await limited.aclose()

    async def test_terminate_process_group_uses_killpg(self) -> None:
        backend = SubprocessBackend(
            python_executable=FAKE_SANDBOX_PYTHON,
            max_concurrency=4,
        )
        try:
            with tempfile.TemporaryDirectory() as work_dir:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); os.setpgrp(); time.sleep(120)",
                    cwd=work_dir,
                    start_new_session=True,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                assert proc.pid is not None
                for _ in range(50):
                    try:
                        os.kill(proc.pid, 0)
                        break
                    except ProcessLookupError:
                        await asyncio.sleep(0.05)

                await backend._terminate_process_group(proc)

                with self.assertRaises(ProcessLookupError):
                    os.kill(proc.pid, signal.SIGKILL)
        finally:
            await backend.aclose()


if __name__ == "__main__":
    unittest.main()
