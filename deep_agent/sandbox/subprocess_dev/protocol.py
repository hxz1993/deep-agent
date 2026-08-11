from __future__ import annotations

from typing import Any

from deep_agent.sandbox.types import (
    ExecuteResult,
    SandboxError,
    TruncationMetadata,
)

BACKEND_NAME = "subprocess-dev"
DEFAULT_ENVIRONMENT = "development"
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MEMORY_RSS_BYTES = 512 * 1024 * 1024
DEFAULT_CPU_SECONDS = 30
# RLIMIT_NPROC is per-UID (not per-sandbox). Default off for subprocess-dev;
# stage-C cgroup pids.max is the real per-execution PID cap.
DEFAULT_MAX_PIDS = None

# Isolation-class codes must never be emitted by subprocess-dev (stage A′).
FORBIDDEN_SUBPROCESS_ERROR_CODES = frozenset(
    {
        "MEMORY_LIMIT",
        "PID_LIMIT",
        "SECURITY_POLICY_VIOLATION",
    }
)


def error_result(
    request_id: str,
    *,
    code: str,
    message: str,
    duration_ms: int,
    environment: str,
    exit_code: int | None = None,
    traceback: str | None = None,
) -> ExecuteResult:
    if code in FORBIDDEN_SUBPROCESS_ERROR_CODES:
        code = "EXECUTION_FAILED"
        message = f"{message} (mapped from isolation-class code)"
    return ExecuteResult(
        request_id=request_id,
        status="error",
        exit_code=exit_code,
        stdout="",
        stderr="",
        result=None,
        error=SandboxError(code=code, message=message, traceback=traceback),
        duration_ms=duration_ms,
        backend=BACKEND_NAME,
        environment=environment,
        security_level="none",
        memory_peak_bytes=None,
        truncated=TruncationMetadata(),
    )


def result_from_frame(
    request_id: str,
    frame: dict[str, Any],
    *,
    duration_ms: int,
    exit_code: int | None,
    environment: str,
) -> ExecuteResult:
    if frame.get("request_id") != request_id:
        return error_result(
            request_id,
            code="BACKEND_PROTOCOL_ERROR",
            message="response request_id mismatch",
            duration_ms=duration_ms,
            environment=environment,
            exit_code=exit_code,
        )

    status = frame.get("status")
    if status not in {"ok", "error"}:
        return error_result(
            request_id,
            code="BACKEND_PROTOCOL_ERROR",
            message="invalid response status",
            duration_ms=duration_ms,
            environment=environment,
            exit_code=exit_code,
        )

    reported_exit_code = frame.get("exit_code", exit_code)
    if reported_exit_code is not None and not isinstance(reported_exit_code, int):
        return error_result(
            request_id,
            code="BACKEND_PROTOCOL_ERROR",
            message="invalid execution exit_code",
            duration_ms=duration_ms,
            environment=environment,
            exit_code=exit_code,
        )

    error_payload = frame.get("error")
    error: SandboxError | None = None
    if error_payload is not None:
        if not isinstance(error_payload, dict):
            return error_result(
                request_id,
                code="BACKEND_PROTOCOL_ERROR",
                message="invalid error payload",
                duration_ms=duration_ms,
                environment=environment,
                exit_code=exit_code,
            )
        try:
            error = SandboxError.model_validate(error_payload)
        except Exception as exc:
            return error_result(
                request_id,
                code="BACKEND_PROTOCOL_ERROR",
                message=str(exc),
                duration_ms=duration_ms,
                environment=environment,
                exit_code=exit_code,
            )
        if error.code in FORBIDDEN_SUBPROCESS_ERROR_CODES:
            error = SandboxError(
                code="EXECUTION_FAILED",
                message=error.message,
                traceback=error.traceback,
            )

    truncated_payload = frame.get("truncated", {})
    try:
        truncated = TruncationMetadata.model_validate(truncated_payload)
    except Exception as exc:
        return error_result(
            request_id,
            code="BACKEND_PROTOCOL_ERROR",
            message=str(exc),
            duration_ms=duration_ms,
            environment=environment,
            exit_code=exit_code,
        )

    try:
        return ExecuteResult(
            request_id=request_id,
            status=status,
            exit_code=reported_exit_code,
            stdout=str(frame.get("stdout", "")),
            stderr=str(frame.get("stderr", "")),
            result=frame.get("result"),
            error=error,
            duration_ms=duration_ms,
            backend=BACKEND_NAME,
            environment=environment,
            security_level="none",
            memory_peak_bytes=None,
            truncated=truncated,
        )
    except Exception as exc:
        return error_result(
            request_id,
            code="BACKEND_PROTOCOL_ERROR",
            message=str(exc),
            duration_ms=duration_ms,
            environment=environment,
            exit_code=exit_code,
        )
