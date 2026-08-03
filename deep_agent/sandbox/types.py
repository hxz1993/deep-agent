from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonValue = Any

SandboxErrorCode = Literal[
    "PYTHON_EXCEPTION",
    "INVALID_REQUEST",
    "OUTPUT_LIMIT",
    "TIMEOUT",
    "MEMORY_LIMIT",
    "PID_LIMIT",
    "SECURITY_POLICY_VIOLATION",
    "EXECUTION_FAILED",
    "BACKEND_PROTOCOL_ERROR",
    "CAPACITY_EXCEEDED",
    "BACKEND_UNAVAILABLE",
]

SecurityLevel = Literal["none", "linux-isolated"]
ExecuteStatus = Literal["ok", "error"]
StateScope = Literal["request"]

MAX_CODE_BYTES = 64 * 1024
MAX_INPUTS_JSON_BYTES = 2 * 1024 * 1024
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_TRACEBACK_BYTES = 32 * 1024
MAX_RESULT_BYTES = 512 * 1024
DEFAULT_TIMEOUT_MS = 30_000


def _utf8_byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _json_utf8_byte_length(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


class TruncationMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    stdout: bool = False
    stderr: bool = False
    traceback: bool = False
    result: bool = False


class SandboxError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: SandboxErrorCode
    message: str
    traceback: str | None = None

    @field_validator("traceback")
    @classmethod
    def validate_traceback_size(cls, value: str | None) -> str | None:
        if value is not None and _utf8_byte_length(value) > MAX_TRACEBACK_BYTES:
            raise ValueError(f"traceback exceeds {MAX_TRACEBACK_BYTES} bytes")
        return value


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    code: str
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, gt=0)

    @field_validator("code")
    @classmethod
    def validate_code_size(cls, value: str) -> str:
        if _utf8_byte_length(value) > MAX_CODE_BYTES:
            raise ValueError(f"code exceeds {MAX_CODE_BYTES} bytes")
        return value

    @field_validator("inputs")
    @classmethod
    def validate_inputs_size(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if _json_utf8_byte_length(value) > MAX_INPUTS_JSON_BYTES:
            raise ValueError(f"inputs JSON exceeds {MAX_INPUTS_JSON_BYTES} bytes")
        return value


class ExecuteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    status: ExecuteStatus
    exit_code: int | None
    stdout: str
    stderr: str
    result: JsonValue | None
    error: SandboxError | None
    duration_ms: int = Field(ge=0)
    backend: str
    environment: str
    security_level: SecurityLevel
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    truncated: TruncationMetadata

    @field_validator("stdout")
    @classmethod
    def validate_stdout_size(cls, value: str) -> str:
        if _utf8_byte_length(value) > MAX_STDOUT_BYTES:
            raise ValueError(f"stdout exceeds {MAX_STDOUT_BYTES} bytes")
        return value

    @field_validator("stderr")
    @classmethod
    def validate_stderr_size(cls, value: str) -> str:
        if _utf8_byte_length(value) > MAX_STDERR_BYTES:
            raise ValueError(f"stderr exceeds {MAX_STDERR_BYTES} bytes")
        return value

    @field_validator("result")
    @classmethod
    def validate_result_size(cls, value: JsonValue | None) -> JsonValue | None:
        if value is not None and _json_utf8_byte_length(value) > MAX_RESULT_BYTES:
            raise ValueError(f"result exceeds {MAX_RESULT_BYTES} bytes")
        return value


class ResourceLimitsSummary(BaseModel):
    """Server-configured resource caps exposed via health (no host paths).

    ``memory_bytes`` is always the product **RSS budget** (same semantics as stage-C
    cgroup ``memory.max``). Backends that cannot enforce RSS may also report the
    translated limit they actually apply (e.g. ``memory_as_bytes`` for RLIMIT_AS).
    """

    model_config = ConfigDict(frozen=True)

    memory_bytes: int | None = Field(default=None, ge=0)
    memory_enforcement: Literal["rss", "rlimit_as", "none"] | None = None
    memory_as_bytes: int | None = Field(default=None, ge=0)
    cpu_seconds: int | None = Field(default=None, ge=0)
    max_pids: int | None = Field(default=None, ge=0)
    max_stdout_bytes: int = Field(default=MAX_STDOUT_BYTES, gt=0)
    max_stderr_bytes: int = Field(default=MAX_STDERR_BYTES, gt=0)
    max_result_bytes: int = Field(default=MAX_RESULT_BYTES, gt=0)


class BackendHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    backend: str
    state_scope: StateScope
    security_level: SecurityLevel
    production_ready: bool
    resource_limits: ResourceLimitsSummary | None = None
