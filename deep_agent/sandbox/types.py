from __future__ import annotations

import base64
import binascii
import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
ExecuteMode = Literal["expression", "script"]

MAX_CODE_BYTES = 64 * 1024
MAX_INPUTS_JSON_BYTES = 2 * 1024 * 1024
MAX_SCRIPT_RESOURCE_FILES = 16
MAX_SCRIPT_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_SCRIPT_ARGUMENTS = 64
MAX_SCRIPT_ARGUMENT_BYTES = 4 * 1024
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


class SandboxInputFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    content_base64: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or path.parts[0] in {"outputs", "inputs.json", "main.py"}
        ):
            raise ValueError("sandbox input file path is unsafe")
        return path.as_posix()

    @field_validator("content_base64")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise ValueError("sandbox input file is not valid base64") from exc
        return value

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> SandboxInputFile:
        return cls(
            path=path,
            content_base64=base64.b64encode(content).decode("ascii"),
        )

    def decoded(self) -> bytes:
        return base64.b64decode(self.content_base64.encode("ascii"), validate=True)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    code: str
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, gt=0)
    mode: ExecuteMode = "expression"
    arguments: tuple[str, ...] = ()
    files: tuple[SandboxInputFile, ...] = ()

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

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_SCRIPT_ARGUMENTS:
            raise ValueError(f"arguments exceed {MAX_SCRIPT_ARGUMENTS} items")
        for item in value:
            if "\x00" in item or _utf8_byte_length(item) > MAX_SCRIPT_ARGUMENT_BYTES:
                raise ValueError("script argument is invalid or too large")
        return value

    @model_validator(mode="after")
    def validate_mode_files(self) -> ExecuteRequest:
        if self.mode == "expression" and (self.arguments or self.files):
            raise ValueError("expression mode does not accept arguments or files")
        if len(self.files) > MAX_SCRIPT_RESOURCE_FILES:
            raise ValueError(f"files exceed {MAX_SCRIPT_RESOURCE_FILES} items")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("sandbox input file paths must be unique")
        total = sum(len(item.decoded()) for item in self.files)
        if total > MAX_SCRIPT_RESOURCE_BYTES:
            raise ValueError(f"sandbox input files exceed {MAX_SCRIPT_RESOURCE_BYTES} bytes")
        return self


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
    resource_limits: ResourceLimitsSummary | None = None
