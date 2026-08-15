from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from deep_agent.sandbox.framing import FramingError, read_frame_async, write_frame_async
from deep_agent.sandbox.subprocess_dev.env import build_worker_env
from deep_agent.sandbox.subprocess_dev.preexec import build_subprocess_preexec
from deep_agent.sandbox.subprocess_dev.process import remove_work_dir, terminate_process_group
from deep_agent.sandbox.subprocess_dev.protocol import (
    BACKEND_NAME,
    DEFAULT_CPU_SECONDS,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_PIDS,
    DEFAULT_MEMORY_RSS_BYTES,
    error_result,
    result_from_frame,
)
from deep_agent.sandbox.subprocess_dev.rlimit import (
    rlimit_as_supported,
    rss_bytes_to_rlimit_as,
)
from deep_agent.sandbox.types import (
    BackendHealth,
    ExecuteRequest,
    ExecuteResult,
    MAX_RESULT_BYTES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    ResourceLimitsSummary,
)


class SubprocessBackend:
    def __init__(
        self,
        *,
        python_executable: str,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        environment: str = DEFAULT_ENVIRONMENT,
        memory_limit_bytes: int | None = DEFAULT_MEMORY_RSS_BYTES,
        cpu_limit_seconds: int | None = DEFAULT_CPU_SECONDS,
        max_pids: int | None = DEFAULT_MAX_PIDS,
    ) -> None:
        if not str(python_executable).strip():
            raise ValueError("python_executable is required")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self._python_executable = str(python_executable)
        self._max_concurrency = max_concurrency
        self._environment = environment
        # RSS-intent budget shared with future cgroup backends.
        self._memory_limit_bytes = memory_limit_bytes
        self._cpu_limit_seconds = cpu_limit_seconds
        self._max_pids = max_pids
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._closed = False

    @property
    def python_executable(self) -> str:
        return self._python_executable

    def semaphore_locked(self) -> bool:
        return self._semaphore.locked()

    def _memory_as_bytes(self) -> int | None:
        if self._memory_limit_bytes is None:
            return None
        return rss_bytes_to_rlimit_as(self._memory_limit_bytes)

    def resource_limits_summary(self) -> ResourceLimitsSummary:
        memory_as_bytes = self._memory_as_bytes() if rlimit_as_supported() else None
        return ResourceLimitsSummary(
            memory_bytes=self._memory_limit_bytes,
            memory_enforcement="rlimit_as" if memory_as_bytes is not None else "none",
            memory_as_bytes=memory_as_bytes,
            cpu_seconds=self._cpu_limit_seconds,
            # 0 / None => not applied (RLIMIT_NPROC is a poor stand-in for cgroup pids.max).
            max_pids=self._max_pids if self._max_pids else None,
            max_stdout_bytes=MAX_STDOUT_BYTES,
            max_stderr_bytes=MAX_STDERR_BYTES,
            max_result_bytes=MAX_RESULT_BYTES,
        )

    async def health(self) -> BackendHealth:
        return BackendHealth(
            enabled=not self._closed,
            backend=BACKEND_NAME,
            state_scope="request",
            security_level="none",
            resource_limits=self.resource_limits_summary(),
        )

    async def execute(self, request: ExecuteRequest) -> ExecuteResult:
        if self._closed:
            return error_result(
                request.request_id,
                code="BACKEND_UNAVAILABLE",
                message="subprocess sandbox backend is closed",
                duration_ms=0,
                environment=self._environment,
            )

        capacity_error = self._check_capacity(request)
        if capacity_error is not None:
            return capacity_error

        await self._semaphore.acquire()
        started = time.monotonic()
        work_dir: str | None = None
        proc: asyncio.subprocess.Process | None = None
        try:
            work_dir = tempfile.mkdtemp(prefix="deep-agent-sandbox-")
            worker_request = self._prepare_work_dir(Path(work_dir), request)
            proc = await self._spawn_worker(work_dir, worker_request)
            return await self._read_worker_response(
                proc,
                request,
                started=started,
            )
        except asyncio.CancelledError:
            if proc is not None:
                await terminate_process_group(proc)
            raise
        except Exception as exc:
            return error_result(
                request.request_id,
                code="EXECUTION_FAILED",
                message=f"sandbox work directory preparation failed: {type(exc).__name__}",
                duration_ms=int((time.monotonic() - started) * 1000),
                environment=self._environment,
            )
        finally:
            await self._finalize(proc, work_dir)

    async def aclose(self) -> None:
        self._closed = True

    def _check_capacity(self, request: ExecuteRequest) -> ExecuteResult | None:
        if self._semaphore.locked():
            return error_result(
                request.request_id,
                code="CAPACITY_EXCEEDED",
                message="sandbox concurrency limit reached",
                duration_ms=0,
                environment=self._environment,
            )
        return None

    async def _spawn_worker(
        self,
        work_dir: str,
        request: ExecuteRequest,
    ) -> asyncio.subprocess.Process:
        preexec = build_subprocess_preexec(
            work_dir=work_dir,
            memory_as_bytes=self._memory_as_bytes(),
            cpu_seconds=self._cpu_limit_seconds,
            max_pids=self._max_pids if self._max_pids else None,
        )
        popen_kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            # Worker captures user stderr internally; discard worker diagnostics
            # so an unbounded PIPE cannot stall the parent.
            "stderr": asyncio.subprocess.DEVNULL,
            "env": build_worker_env(),
            "cwd": work_dir,
            "start_new_session": True,
        }
        if preexec is not None:
            popen_kwargs["preexec_fn"] = preexec

        proc = await asyncio.create_subprocess_exec(
            self._python_executable,
            "-m",
            "deep_agent.sandbox.worker",
            **popen_kwargs,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None

        await write_frame_async(
            proc.stdin,
            request.model_dump(mode="json"),
        )
        proc.stdin.close()
        await proc.stdin.wait_closed()
        return proc

    @staticmethod
    def _prepare_work_dir(work_dir: Path, request: ExecuteRequest) -> ExecuteRequest:
        if request.mode != "script":
            return request

        script_path = work_dir / "main.py"
        script_path.write_text(request.code, encoding="utf-8")
        script_path.chmod(0o600)
        inputs_path = work_dir / "inputs.json"
        inputs_path.write_text(
            json.dumps(request.inputs, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        inputs_path.chmod(0o600)
        (work_dir / "outputs").mkdir(mode=0o700)

        resolved_root = work_dir.resolve()
        for item in request.files:
            target = (work_dir / item.path).resolve()
            if resolved_root not in target.parents:
                raise ValueError("sandbox input file escaped the work directory")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(item.decoded())
            target.chmod(0o600)

        # The development worker reads files from its isolated cwd. Keeping the
        # bytes out of the framed child request avoids sending binary payloads twice.
        return request.model_copy(update={"files": ()})

    async def _read_worker_response(
        self,
        proc: asyncio.subprocess.Process,
        request: ExecuteRequest,
        *,
        started: float,
    ) -> ExecuteResult:
        timeout_sec = request.timeout_ms / 1000.0
        response_frame: dict[str, Any] | None = None
        try:
            assert proc.stdout is not None
            response_frame = await asyncio.wait_for(
                read_frame_async(proc.stdout),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            await terminate_process_group(proc)
            duration_ms = int((time.monotonic() - started) * 1000)
            return error_result(
                request.request_id,
                code="TIMEOUT",
                message="sandbox execution timed out",
                duration_ms=duration_ms,
                environment=self._environment,
            )
        except FramingError as exc:
            await terminate_process_group(proc)
            await proc.wait()
            duration_ms = int((time.monotonic() - started) * 1000)
            exit_code = proc.returncode
            if exit_code not in (None, 0):
                return error_result(
                    request.request_id,
                    code="EXECUTION_FAILED",
                    message=f"worker exited with status {exit_code}",
                    duration_ms=duration_ms,
                    environment=self._environment,
                    exit_code=exit_code,
                )
            return error_result(
                request.request_id,
                code="BACKEND_PROTOCOL_ERROR",
                message=str(exc),
                duration_ms=duration_ms,
                environment=self._environment,
                exit_code=exit_code,
            )

        await proc.wait()
        duration_ms = int((time.monotonic() - started) * 1000)
        exit_code = proc.returncode

        if exit_code != 0:
            return error_result(
                request.request_id,
                code="EXECUTION_FAILED",
                message=f"worker exited with status {exit_code}",
                duration_ms=duration_ms,
                environment=self._environment,
                exit_code=exit_code,
            )

        assert response_frame is not None
        return result_from_frame(
            request.request_id,
            response_frame,
            duration_ms=duration_ms,
            exit_code=exit_code,
            environment=self._environment,
        )

    async def _finalize(
        self,
        proc: asyncio.subprocess.Process | None,
        work_dir: str | None,
    ) -> None:
        if proc is not None and proc.returncode is None:
            await terminate_process_group(proc)
        if work_dir is not None:
            remove_work_dir(work_dir)
        self._semaphore.release()

    async def _terminate_process_group(
        self,
        proc: asyncio.subprocess.Process,
    ) -> None:
        await terminate_process_group(proc)
