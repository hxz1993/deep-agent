from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, field_validator, model_validator

CONNECTION_PREFIX = "agent_connection:"
REMOTE_AGENT_PROTOCOLS = frozenset({"a2a", "codex", "codebuddy"})
Operation = Literal["start", "answer", "resume"]
Status = Literal["submitted", "working", "input_required", "completed", "failed", "canceled", "unknown"]


def tool_metadata(tool_name: str, metadata: object) -> dict | None:
    if tool_name not in {"delegate_task", "answer_task", "remote_task_result"} or not isinstance(metadata, Mapping):
        return None
    value = metadata.get("remote_agent")
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
        return None
    keys = ("id", "connection_id", "name", "protocol", "status", "question_id", "remote_task_id",
            "remote_context_id", "remote_session_id", "created_at", "updated_at")
    return {key: value[key] for key in keys if isinstance(value.get(key), str)}


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1


class A2AConfig(ConnectionConfig):
    protocol: Literal["a2a"] = "a2a"
    card_url: str = Field(max_length=2048)
    headers: dict[str, str | None] = Field(default_factory=dict, max_length=32, repr=False)
    approved_interface: dict | None = None

    @field_validator("card_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.fragment or any(character.isspace() for character in value)):
            raise ValueError("请输入有效的 HTTP(S) Agent Card 地址")
        _ = parsed.port
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: dict[str, str | None]) -> dict[str, str | None]:
        normalized = {}
        for name, value in headers.items():
            key = name.lower()
            if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
                    or key in normalized or key in {"host", "content-length", "transfer-encoding", "connection"}):
                raise ValueError("请求头名称无效或重复")
            if value is not None and (len(value) > 4096 or any(not 32 <= ord(char) < 127 for char in value)):
                raise ValueError("请求头值无效")
            normalized[key] = value
        return normalized


class CliConfig(ConnectionConfig):
    execution: Literal["local", "ssh"] = "local"
    ssh_host: str | None = Field(default=None, max_length=255)
    cwd: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=128)
    command: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_execution(self):
        if self.execution == "ssh" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.ssh_host or ""):
            raise ValueError("请填写 SSH 配置中的主机别名")
        if self.execution == "local" and self.ssh_host:
            raise ValueError("本机执行无需 SSH 主机")
        if self.command and not (self.command.startswith("/") or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.command)):
            raise ValueError("启动命令应为命令名或绝对路径")
        for value in (self.cwd, self.command, self.model):
            if value and any(ord(character) < 32 for character in value):
                raise ValueError("执行配置包含无效字符")
        if self.model and self.model.startswith("-"):
            raise ValueError("模型名无效")
        return self


class CodexConfig(CliConfig):
    protocol: Literal["codex"] = "codex"
    sandbox: Literal["read-only", "workspace-write"] = "read-only"
    profile: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")] | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


class CodeBuddyConfig(CliConfig):
    protocol: Literal["codebuddy"] = "codebuddy"


RemoteConfig = Annotated[A2AConfig | CodexConfig | CodeBuddyConfig, Field(discriminator="protocol")]
CONFIG_ADAPTER = TypeAdapter(RemoteConfig)


class ConnectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: str | None = Field(default=None, max_length=2000)
    revision: int | None = Field(default=None, ge=1)
    config: RemoteConfig


class RemoteState(BaseModel):
    task_id: str | None = None
    context_id: str | None = None
    session_id: str | None = None
    question_id: str | None = None
    status: Status = "submitted"
    cursor: dict = Field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class InputFile:
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class OutputFile:
    key: str
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RemoteResult:
    state: RemoteState
    text: str = ""
    files: tuple[OutputFile, ...] = ()
    warnings: tuple[str, ...] = ()


Checkpoint = Callable[[RemoteState], Awaitable[None]]


class RemoteAgentError(Exception):
    def __init__(self, message: str, *, status: Status = "failed") -> None:
        super().__init__(message)
        self.status = status


class RemoteTaskExpired(RemoteAgentError):
    """The provider confirmed that the original task is no longer available."""


class Adapter(Protocol):
    async def inspect(self) -> dict: ...

    async def run(self, state: RemoteState, message: str, attachments: Sequence[InputFile],
                  mode: Operation, checkpoint: Checkpoint, *, message_id: str) -> RemoteResult: ...

    async def refresh(self, state: RemoteState, checkpoint: Checkpoint) -> RemoteResult: ...

    async def cancel(self, state: RemoteState) -> RemoteResult: ...
