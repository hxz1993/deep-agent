from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.files.storage import FileStore, PlatformFileStore

if TYPE_CHECKING:
    from deep_agent.capabilities.registry import ToolPackage

CODE_MODE_METADATA_KEY = "deepagent_code_mode"
CODE_MODE_ORIGINAL_NAME_KEY = "deepagent_original_name"
CODE_MODE_HYBRID = "hybrid"
CODE_MODE_CODE_ONLY = "code_only"

FILE_DATA_TOKEN_PREFIX = "deepagent-file-data://"
MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_TEXT_CHARS = 65_536
MAX_TEXT_CHARS = 262_144

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "cookie",
        "extra_identity_context",
        "headers",
        "password",
        "provider_details",
        "request_headers",
        "refresh_token",
        "secret",
        "signature",
        "token",
        "x-user-token",
    }
)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_FILE_TOKEN_RE = re.compile(re.escape(FILE_DATA_TOKEN_PREFIX) + r"[A-Za-z0-9]+")
_FULL_FILE_TOKEN_RE = re.compile(
    r"^" + re.escape(FILE_DATA_TOKEN_PREFIX) + r"[0-9a-f]{32}$"
)
_BASE64_SEGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}(?![A-Za-z0-9+/])"
)
_IDENTITY_CONTEXT_RE = re.compile(
    r"<extra_identity_context>.*?</extra_identity_context>",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|token|cookie)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)


class FileData(TypedDict):
    file_id: str
    name: str
    mime: str
    byte_size: int
    sha256: str
    encoding: Literal["base64", "text"]
    content: str


@dataclass(frozen=True)
class FilePayload:
    token: str
    file_id: str
    name: str
    mime: str
    byte_size: int
    sha256: str
    data: bytes


def _request_headers(ctx: RunContext[AgentDeps]) -> dict[str, str]:
    headers = dict(ctx.deps.request_headers)
    if ctx.deps.user:
        headers.setdefault("user", ctx.deps.user)
    return headers


def _file_store(ctx: RunContext[AgentDeps]) -> FileStore:
    return ctx.deps.file_store or PlatformFileStore()


async def _load_authorized_file(
    ctx: RunContext[AgentDeps],
    file_id: str,
) -> tuple[bytes, str, str]:
    attachment = ctx.deps.attachment_by_id.get(file_id)
    artifact = ctx.deps.tool_output_artifacts.get(file_id)
    if attachment is None and artifact is None:
        raise ModelRetry(
            "file_id 不属于当前会话附件或当前会话大工具结果，无法在代码编排中读取"
        )

    if artifact is not None:
        byte_size = artifact.get("byte_size")
        if isinstance(byte_size, int) and byte_size > MAX_FILE_BYTES:
            raise ModelRetry(f"文件超过代码编排固定 {MAX_FILE_BYTES} 字节上限")
        cache_key = f"tool-result:{file_id}"
        name = str(artifact.get("file_name") or f"tool-result-{file_id}.txt")
        mime = str(artifact.get("mime") or "text/plain")
    else:
        assert attachment is not None
        cache_key = f"attachment:{file_id}"
        name = attachment.name or f"attachment-{file_id}"
        mime = attachment.mime or "application/octet-stream"

    cached = ctx.deps.file_bytes.get(cache_key)
    if cached is not None:
        data = cached
    else:
        try:
            data = await _download_file(
                file_id,
                _request_headers(ctx),
                file_store=_file_store(ctx),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed("当前会话文件读取失败") from exc
        ctx.deps.file_bytes[cache_key] = data

    if len(data) > MAX_FILE_BYTES:
        raise ModelRetry(f"文件超过代码编排固定 {MAX_FILE_BYTES} 字节上限")
    return data, name, mime


def build_code_mode_tool_package() -> "ToolPackage":
    """Register the code-only file primitive behind the product capability."""
    from deep_agent.capabilities.registry import ToolInfo, ToolPackage

    toolset = FunctionToolset[AgentDeps](id="code-mode-files")

    @toolset.tool(metadata={CODE_MODE_METADATA_KEY: CODE_MODE_CODE_ONLY})
    async def load_file_data(
        ctx: RunContext[AgentDeps],
        file_id: str,
        encoding: Literal["base64", "text"] = "base64",
        text_encoding: str = "utf-8",
        max_chars: int = DEFAULT_TEXT_CHARS,
    ) -> FileData:
        """读取当前会话已授权文件，供 run_code 直接中转给后续 MCP 工具。"""
        started = time.perf_counter()
        status = "completed"
        try:
            data, name, mime = await _load_authorized_file(ctx, file_id)
            digest = hashlib.sha256(data).hexdigest()
            if encoding == "text":
                if not 1 <= max_chars <= MAX_TEXT_CHARS:
                    raise ModelRetry(
                        f"max_chars 必须在 1 到 {MAX_TEXT_CHARS} 之间"
                    )
                try:
                    content = data.decode(text_encoding, errors="strict")
                except (LookupError, UnicodeDecodeError) as exc:
                    raise ModelRetry(f"文件无法按 {text_encoding!r} 严格解码") from exc
                if len(content) > max_chars:
                    raise ModelRetry(
                        f"文本包含 {len(content)} 个字符，超过 max_chars={max_chars}；"
                        "请使用外层 read_file 分页读取"
                    )
            else:
                token = f"{FILE_DATA_TOKEN_PREFIX}{uuid.uuid4().hex}"
                payload = FilePayload(
                    token=token,
                    file_id=file_id,
                    name=name,
                    mime=mime,
                    byte_size=len(data),
                    sha256=digest,
                    data=data,
                )
                ctx.deps.code_mode_file_payloads[token] = payload
                content = token

            return FileData(
                file_id=file_id,
                name=name,
                mime=mime,
                byte_size=len(data),
                sha256=digest,
                encoding=encoding,
                content=content,
            )
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception:
            status = "failed"
            raise
        finally:
            ctx.deps.code_mode_audit.append(
                {
                    "tool_name": "load_file_data",
                    "call_id": ctx.tool_call_id,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "args": {
                        "file_id": file_id,
                        "encoding": encoding,
                        "text_encoding": text_encoding,
                        "max_chars": max_chars,
                    },
                }
            )

    return ToolPackage(
        code="builtin:code-mode",
        category="builtin",
        toolset=toolset,
        label="代码编排",
        source="builtin",
        description="在受限 Monty 环境中编排当前 Agent 已授权的 MCP 工具。",
        tools=[
            ToolInfo(
                "run_code",
                "代码编排",
                "使用受限 Python 子集完成多步、并发、循环、聚合和文件载荷中转。",
                execution_type="code",
            )
        ],
    )


def _resolve_payload_value(value: Any, deps: AgentDeps) -> Any:
    if isinstance(value, str):
        if _FULL_FILE_TOKEN_RE.fullmatch(value):
            payload = deps.code_mode_file_payloads.get(value)
            if not isinstance(payload, FilePayload):
                raise ModelRetry("文件变量无效、已过期或不属于当前 Run")
            return base64.b64encode(payload.data).decode("ascii")
        return value
    if isinstance(value, list):
        return [_resolve_payload_value(item, deps) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_payload_value(item, deps) for item in value)
    if isinstance(value, dict):
        return {
            key: _resolve_payload_value(item, deps)
            for key, item in value.items()
        }
    return value


def _looks_like_base64(value: str) -> bool:
    compact = "".join(value.split())
    return (
        len(compact) >= 256
        and len(compact) % 4 == 0
        and bool(_BASE64_RE.fullmatch(compact))
    )


def payload_secrets(deps: AgentDeps) -> frozenset[str]:
    """Return run-scoped opaque and encoded payload values for exact redaction."""
    secrets: set[str] = set()
    for token, payload in deps.code_mode_file_payloads.items():
        if not isinstance(payload, FilePayload):
            continue
        secrets.add(token)
        secrets.add(base64.b64encode(payload.data).decode("ascii"))
    return frozenset(secrets)


def safe_audit_value(
    value: Any,
    *,
    depth: int = 0,
    sensitive_values: frozenset[str] = frozenset(),
) -> Any:
    """Bound and redact values before they can enter events, logs, or traces."""
    if depth > 4:
        return {"type": type(value).__name__, "redacted": True}
    if isinstance(value, str):
        for secret in sensitive_values:
            if secret:
                value = value.replace(secret, "[file-payload-redacted]")
        value = _FILE_TOKEN_RE.sub("[file-data-token]", value)
        value = _BASE64_SEGMENT_RE.sub("[base64-redacted]", value)
        value = _IDENTITY_CONTEXT_RE.sub("[identity-context-redacted]", value)
        value = _BEARER_RE.sub("Bearer [redacted]", value)
        value = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", value)
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        if _looks_like_base64(value):
            return {"type": "base64", "char_count": len(value), "sha256": digest}
        if len(value) > 4096:
            return {
                "type": "string",
                "char_count": len(value),
                "sha256": digest,
                "preview": value[:160],
            }
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "type": "bytes",
            "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 32:
                result["__truncated__"] = len(value) - index
                break
            normalized = str(key)
            if normalized.lower() in _SENSITIVE_KEYS:
                result[normalized] = "[redacted]"
            else:
                result[normalized] = safe_audit_value(
                    item,
                    depth=depth + 1,
                    sensitive_values=sensitive_values,
                )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        projected = [
            safe_audit_value(
                item,
                depth=depth + 1,
                sensitive_values=sensitive_values,
            )
            for item in items[:32]
        ]
        if len(items) > 32:
            projected.append({"truncated_items": len(items) - 32})
        return projected
    return {"type": type(value).__name__, "value": str(value)[:160]}


@dataclass(kw_only=True)
class _ResolvingTool(ToolsetTool[AgentDeps]):
    source_tool: ToolsetTool[AgentDeps]


@dataclass
class FilePayloadResolvingToolset(WrapperToolset[AgentDeps]):
    """Mark MCP tools for hybrid mode and resolve opaque file variables at call time."""

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        tools = await self.wrapped.get_tools(ctx)
        result: dict[str, ToolsetTool[AgentDeps]] = {}
        for name, tool in tools.items():
            metadata = dict(tool.tool_def.metadata or {})
            metadata[CODE_MODE_METADATA_KEY] = CODE_MODE_HYBRID
            metadata[CODE_MODE_ORIGINAL_NAME_KEY] = name
            tool_def = replace(tool.tool_def, metadata=metadata)
            result[name] = _ResolvingTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source_tool=tool,
            )
        return result

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        if not isinstance(tool, _ResolvingTool):
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        started = time.perf_counter()
        status = "completed"
        result: Any = None
        error_type: str | None = None
        try:
            resolved = _resolve_payload_value(tool_args, ctx.deps)
            result = await self.wrapped.call_tool(
                name,
                resolved,
                ctx,
                tool.source_tool,
            )
            return result
        except asyncio.CancelledError:
            status = "interrupted"
            error_type = "CancelledError"
            raise
        except Exception as exc:
            status = "failed"
            error_type = type(exc).__name__
            raise
        finally:
            secrets = payload_secrets(ctx.deps)
            audit_entry = {
                "tool_name": name,
                "call_id": ctx.tool_call_id,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "args": safe_audit_value(
                    tool_args,
                    sensitive_values=secrets,
                ),
            }
            if status == "completed":
                audit_entry["result"] = safe_audit_value(
                    result,
                    sensitive_values=secrets,
                )
            elif error_type:
                audit_entry["error_type"] = error_type
            ctx.deps.code_mode_audit.append(
                audit_entry
            )
