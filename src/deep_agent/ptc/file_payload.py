from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.diagnostics import current_collector
from deep_agent.agent.tool_errors import observe_tool_operation, tool_error_message
from deep_agent.capabilities.codes import PTC_PACKAGE_CODE
from deep_agent.files.storage import FileStore, PlatformFileStore
from deep_agent.files.public_download import load_public_file

if TYPE_CHECKING:
    from deep_agent.capabilities.registry import ToolPackage

CODE_MODE_METADATA_KEY = "deepagent_code_mode"
CODE_MODE_ORIGINAL_NAME_KEY = "deepagent_original_name"
CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY = "deepagent_trusted_return_schema"
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
    file_id: str | None
    url: str | None
    name: str
    mime: str
    byte_size: int
    sha256: str
    encoding: Literal["base64", "text"]
    content: str


@dataclass(frozen=True)
class FilePayload:
    token: str
    file_id: str | None
    name: str
    mime: str
    byte_size: int
    sha256: str
    data: bytes
    url: str | None = None


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
    session_artifacts = getattr(ctx.deps, "session_file_artifacts", None)
    artifact = (
        session_artifacts.get(file_id)
        if isinstance(session_artifacts, dict)
        else None
    )
    if artifact is None:
        artifact = ctx.deps.tool_output_artifacts.get(file_id)
    if attachment is None and artifact is None:
        raise ModelRetry(
            "file_id 不属于当前会话附件或当前会话工具产物，无法在程序编排中读取"
        )

    if artifact is not None:
        byte_size = artifact.get("byte_size")
        if isinstance(byte_size, int) and byte_size > MAX_FILE_BYTES:
            raise ModelRetry(f"文件超过程序编排固定 {MAX_FILE_BYTES} 字节上限")
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
            raise ToolFailed(_redact_result_text(
                tool_error_message("当前会话文件读取失败", exc, _request_headers(ctx)),
                payload_secrets(ctx.deps),
            )) from exc
        ctx.deps.file_bytes[cache_key] = data

    if len(data) > MAX_FILE_BYTES:
        raise ModelRetry(f"文件超过程序编排固定 {MAX_FILE_BYTES} 字节上限")
    return data, name, mime


async def _load_public_url(url: str) -> tuple[bytes, str, str, str]:
    return await load_public_file(url, max_bytes=MAX_FILE_BYTES)


def build_code_mode_tool_package() -> "ToolPackage":
    """Register the code-only file primitive behind the product capability."""
    from deep_agent.capabilities.registry import ToolInfo, ToolPackage

    toolset = FunctionToolset[AgentDeps](id="code-mode-files")

    @toolset.tool(
        metadata={
            CODE_MODE_METADATA_KEY: CODE_MODE_CODE_ONLY,
            CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY: True,
        }
    )
    async def load_file_data(
        ctx: RunContext[AgentDeps],
        file_id: str | None = None,
        url: str | None = None,
        encoding: Literal["base64", "text"] = "base64",
        text_encoding: str = "utf-8",
        max_chars: int = DEFAULT_TEXT_CHARS,
    ) -> FileData:
        """读取当前会话已授权文件或公网文件，供 run_code 原样中转。"""
        status = "completed"
        error_detail: dict[str, str] | None = None
        collector = current_collector.get()
        if collector is not None:
            collector.begin(ctx.tool_call_id, "load_file_data", {
                "file_id": file_id, "url": url, "encoding": encoding,
                "text_encoding": text_encoding, "max_chars": max_chars,
            })
        result: FileData | None = None
        try:
            normalized_file_id = file_id.strip() if isinstance(file_id, str) else None
            normalized_url = url.strip() if isinstance(url, str) else None
            if not normalized_file_id:
                normalized_file_id = None
            if not normalized_url:
                normalized_url = None
            if (normalized_file_id is None) == (normalized_url is None):
                raise ModelRetry("file_id 与 url 必须且只能提供一个")
            final_url: str | None = None
            if normalized_file_id is not None:
                async with observe_tool_operation(ctx, "file_download", tool="load_file_data"):
                    data, name, mime = await _load_authorized_file(
                        ctx,
                        normalized_file_id,
                    )
            else:
                assert normalized_url is not None
                async with observe_tool_operation(ctx, "public_file_download", tool="load_file_data"):
                    data, name, mime, final_url = await _load_public_url(normalized_url)
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
                    file_id=normalized_file_id,
                    url=final_url,
                    name=name,
                    mime=mime,
                    byte_size=len(data),
                    sha256=digest,
                    data=data,
                )
                ctx.deps.code_mode_file_payloads[token] = payload
                content = token

            if collector is not None and encoding == "text":
                collector.add_sensitive_text(content)

            result = FileData(
                file_id=normalized_file_id,
                url=final_url,
                name=name,
                mime=mime,
                byte_size=len(data),
                sha256=digest,
                encoding=encoding,
                content=content,
            )
            return result
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as exc:
            status = "failed"
            error_detail = {"type": type(exc).__name__}
            if isinstance(exc, (ModelRetry, ToolFailed)) and exc.args and isinstance(exc.args[0], str):
                error_detail["message"] = exc.args[0][:4096]
            raise
        finally:
            if collector is not None:
                collector.end(ctx.tool_call_id, status,
                              {key: value for key, value in result.items() if key != "content"} if result else None,
                              error_detail)

    return ToolPackage(
        code=PTC_PACKAGE_CODE,
        category="builtin",
        toolset=toolset,
        label="程序编排",
        source="builtin",
        description="使用 PTC 在受限环境中编排当前 Agent 已授权的 MCP 工具。",
        tools=[
            ToolInfo(
                "run_code",
                "工具调用编排",
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


def _redact_result_text(value: str, sensitive_values: frozenset[str]) -> str:
    for secret in sensitive_values:
        if secret:
            value = value.replace(secret, "[file-payload-redacted]")
    value = _FILE_TOKEN_RE.sub("[file-data-token]", value)
    value = _BASE64_SEGMENT_RE.sub("[base64-redacted]", value)
    value = _IDENTITY_CONTEXT_RE.sub("[identity-context-redacted]", value)
    value = _BEARER_RE.sub("Bearer [redacted]", value)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", value)


def _safe_result_text(value: str, sensitive_values: frozenset[str]) -> Any:
    text = _redact_result_text(value, sensitive_values)
    if _looks_like_base64(text):
        return {
            "type": "base64",
            "char_count": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        }
    return text


def safe_result_value(
    value: Any,
    *,
    sensitive_values: frozenset[str] = frozenset(),
) -> Any:
    """Redact the final result without applying the smaller audit preview budget.

    ToolOutputLimits owns model-output sizing and full-result spilling. Walk containers
    iteratively so cyclic values cannot hang and deep JSON does not exhaust our stack.
    Only cycles on the current path are replaced; repeated references remain intact.
    """
    root: list[Any] = [None]
    active: set[int] = set()
    pending: list[tuple[Any, Any, Any, bool]] = [(value, root, 0, False)]
    while pending:
        current, target, key, exiting = pending.pop()
        if exiting:
            active.remove(id(current))
            continue
        if isinstance(current, str):
            target[key] = _safe_result_text(current, sensitive_values)
        elif current is None or isinstance(current, (bool, int, float)):
            target[key] = current
        elif isinstance(current, (bytes, bytearray, memoryview)):
            target[key] = safe_audit_value(current, sensitive_values=sensitive_values)
        elif isinstance(current, (dict, list, tuple, set, frozenset)):
            if id(current) in active:
                target[key] = {"type": type(current).__name__, "circular": True}
                continue
            active.add(id(current))
            pending.append((current, None, None, True))
            if isinstance(current, dict):
                projected: Any = {}
                target[key] = projected
                children = []
                for child_key, child in current.items():
                    original_key = str(child_key)
                    safe_key = _safe_result_text(original_key, sensitive_values)
                    normalized = safe_key if isinstance(safe_key, str) else "[base64-redacted]"
                    # Different private keys may redact to the same label. Preserve
                    # their values without reintroducing the original key text.
                    base_key = normalized
                    suffix = 1
                    while normalized in projected:
                        normalized = f"{base_key} [key-{suffix}]"
                        suffix += 1
                    projected[normalized] = None
                    if original_key.lower() in _SENSITIVE_KEYS:
                        projected[normalized] = "[redacted]"
                    else:
                        children.append((child, projected, normalized, False))
            else:
                items = list(current)
                projected = [None] * len(items)
                target[key] = projected
                children = [(child, projected, index, False) for index, child in enumerate(items)]
            pending.extend(reversed(children))
        else:
            target[key] = {
                "type": type(current).__name__,
                "value": _safe_result_text(str(current), sensitive_values),
            }
    return root[0]


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
        value = _redact_result_text(value, sensitive_values)
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

    server_names: tuple[str, ...] = ()

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        tools = await self.wrapped.get_tools(ctx)
        result: dict[str, ToolsetTool[AgentDeps]] = {}
        for name, tool in tools.items():
            from deep_agent.agent.tool_discovery import (
                MCP_RETURN_SCHEMA_TOOLS,
                mcp_original_tool_name,
            )

            metadata = dict(tool.tool_def.metadata or {})
            # Upstream MCP metadata cannot identify a trusted host builtin.
            metadata.pop("deepagent_ptc_builtin", None)
            metadata[CODE_MODE_METADATA_KEY] = CODE_MODE_HYBRID
            metadata[CODE_MODE_ORIGINAL_NAME_KEY] = name
            original_name = mcp_original_tool_name(name, self.server_names)
            metadata[CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY] = bool(
                original_name in MCP_RETURN_SCHEMA_TOOLS
                and tool.tool_def.return_schema
            )
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
        status = "completed"
        result: Any = None
        error_type: str | None = None
        error_detail: dict[str, str] | None = None
        collector = current_collector.get()
        if collector is not None:
            collector.begin(ctx.tool_call_id, name, tool_args)
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
            error_detail = {"type": error_type}
            if isinstance(exc, (ModelRetry, ToolFailed)) and exc.args and isinstance(exc.args[0], str):
                error_detail["message"] = exc.args[0][:4096]
            raise
        finally:
            if collector is not None:
                collector.end(ctx.tool_call_id, status, result, error_detail or error_type)
