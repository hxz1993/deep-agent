from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.toolsets import PreparedToolset

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.agent.tool_errors import observe_tool_operation, tool_error_message
from deep_agent.files.export_schema import (
    ExistingPlatformFileSource,
    ExportFileResult,
    ExportFileSource,
    GeneratedTextFileSource,
)
from deep_agent.files.storage import FileStore, PlatformFileStore
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

_MAX_FILE_NAME_CHARS = 255
_SOURCE_ADAPTER = TypeAdapter(ExportFileSource)


def _request_headers(ctx: RunContext[AgentDeps]) -> dict[str, str]:
    headers = dict(ctx.deps.request_headers)
    if ctx.deps.user:
        headers.setdefault("user", ctx.deps.user)
    return headers


def _has_download_center_context(ctx: RunContext[AgentDeps]) -> bool:
    headers = _request_headers(ctx)
    return bool(headers.get("user") and headers.get("app") and headers.get("space"))


def _validate_file_name(file_name: str) -> str:
    normalized = file_name.strip()
    if not normalized:
        raise ModelRetry("file_name 不能为空")
    if len(normalized) > _MAX_FILE_NAME_CHARS:
        raise ModelRetry(f"file_name 不能超过 {_MAX_FILE_NAME_CHARS} 个字符")
    if Path(normalized).name != normalized or normalized in {".", ".."}:
        raise ModelRetry("file_name 只能是文件名，不能包含目录或路径穿越")
    if any(ord(char) < 32 for char in normalized):
        raise ModelRetry("file_name 不能包含控制字符")
    if Path(normalized).suffix == "":
        raise ModelRetry("file_name 必须包含文件扩展名")
    return normalized


def _file_store(ctx: RunContext[AgentDeps]) -> FileStore:
    return ctx.deps.file_store or PlatformFileStore()


async def _authorized_existing_file(
    ctx: RunContext[AgentDeps],
    file_id: str,
) -> tuple[bytes | None, int | None]:
    attachment = ctx.deps.attachment_by_id.get(file_id)
    if attachment is not None:
        cache_key = f"attachment:{file_id}"
        cached = ctx.deps.file_bytes.get(cache_key)
        if cached is not None:
            return cached, len(cached)
        try:
            async with observe_tool_operation(ctx, "download", tool="export_file_to_download_center"):
                data = await _download_file(
                    file_id,
                    _request_headers(ctx),
                    file_store=_file_store(ctx),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed(tool_error_message(
                "已有附件下载失败，无法登记到下载中心", exc, _request_headers(ctx),
            )) from exc
        ctx.deps.file_bytes[cache_key] = data
        return data, len(data)

    artifact = ctx.deps.session_file_artifacts.get(file_id)
    if artifact is None:
        # Compatibility for request-scoped callers created before the unified
        # authorization map was introduced. Runtime history binding populates both.
        artifact = ctx.deps.tool_output_artifacts.get(file_id)
    if artifact is None:
        raise ModelRetry(
            "file_id 不属于当前会话附件或工具生成文件；"
            "请只使用当前会话中已明确提供的平台 File ID"
        )
    byte_size = artifact.get("byte_size")
    if isinstance(byte_size, int) and byte_size >= 0:
        return None, byte_size
    cache_prefix = (
        "tool-result" if artifact.get("kind") == "tool_result_overflow" else "session-file"
    )
    cache_key = f"{cache_prefix}:{file_id}"
    cached = ctx.deps.file_bytes.get(cache_key)
    if cached is not None:
        return cached, len(cached)
    try:
        async with observe_tool_operation(ctx, "download", tool="export_file_to_download_center"):
            data = await _download_file(
                file_id,
                _request_headers(ctx),
                file_store=_file_store(ctx),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ToolFailed(tool_error_message(
            "当前会话工具生成文件下载失败，无法登记到下载中心", exc, _request_headers(ctx),
        )) from exc
    ctx.deps.file_bytes[cache_key] = data
    return data, len(data)


async def _register_download(
    ctx: RunContext[AgentDeps],
    *,
    file_id: str,
    file_name: str,
    byte_size: int,
) -> str:
    headers = _request_headers(ctx)
    try:
        async with observe_tool_operation(ctx, "register_download", tool="export_file_to_download_center", byte_size=byte_size):
            record_id = await _file_store(ctx).register_download(
                file_id=file_id,
                file_name=file_name,
                byte_size=byte_size,
                request_headers=headers,
            )
            if not isinstance(record_id, str) or not record_id:
                raise ToolFailed("下载中心登记成功但未返回记录ID")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ToolFailed(tool_error_message("下载中心登记失败", exc, headers)) from exc
    return record_id


def build_file_export_toolset() -> PreparedToolset[AgentDeps]:
    toolset = FunctionToolset[AgentDeps](
        id="file-export",
        instructions=(
            "当用户明确需要导出、生成或保存可下载文件时，使用 "
            "export_file_to_download_center 将文件发布到当前用户的DeepFOS下载中心。"
            "可以复用当前会话附件或工具生成文件，也可以提交新生成的UTF-8文本文件内容。"
            "成功后文件已经可以在下载中心获取，最终回答应说明文件名并引导用户前往下载中心。"
            "普通文字回答不要调用，不得传宿主机路径或未经当前会话授权的File ID。"
            "文件格式是否允许由当前环境平台决定，不能从会话附件类型推断。"
            "失败时向用户说明返回的原生错误；没有原生证据时不要猜测是格式限制，"
            "不要反复重试或自行改后缀绕过限制。"
        ),
    )

    @toolset.tool
    async def export_file_to_download_center(
        ctx: RunContext[AgentDeps],
        source: ExportFileSource,
        file_name: str,
    ) -> ExportFileResult:
        """用户明确需要导出文件时，将新文本内容或当前会话平台文件发布到下载中心。

        使用 content 新建UTF-8文本文件；使用 platform_file 复用当前会话附件或工具生成文件，
        不重复上传。成功后在最终回答中说明文件名并提示用户前往下载中心。
        """
        if not _has_download_center_context(ctx):
            raise ToolFailed("当前请求缺少用户、应用或空间上下文，无法使用下载中心")
        normalized_name = _validate_file_name(file_name)
        headers = _request_headers(ctx)
        source = _SOURCE_ADAPTER.validate_python(source)

        if isinstance(source, ExistingPlatformFileSource):
            _data, byte_size = await _authorized_existing_file(ctx, source.file_id)
            assert byte_size is not None
            record_id = await _register_download(
                ctx,
                file_id=source.file_id,
                file_name=normalized_name,
                byte_size=byte_size,
            )
            return ExportFileResult(
                source_type="platform_file",
                download_record_id=record_id,
                file_id=source.file_id,
                file_name=normalized_name,
                byte_size=byte_size,
            )

        assert isinstance(source, GeneratedTextFileSource)
        data = source.content.encode("utf-8")
        if len(data) > max_upload_bytes():
            raise ModelRetry(
                f"新文件内容超过当前 {max_upload_bytes()} 字节上传上限；"
                "请缩小内容或先通过其他工具生成平台文件"
            )
        try:
            async with observe_tool_operation(ctx, "upload", tool="export_file_to_download_center", byte_size=len(data)):
                uploaded = await _file_store(ctx).upload(
                    file_type=FILE_TYPE_ATTACHMENT,
                    file_name=normalized_name,
                    mime="text/plain; charset=utf-8",
                    data=data,
                    request_headers=headers,
                )
                if not isinstance(uploaded.id, str) or not uploaded.id:
                    raise ToolFailed("平台文件上传成功但未返回File ID")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed(tool_error_message("新文件上传到平台失败", exc, headers)) from exc
        uploaded_id = uploaded.id
        byte_size = uploaded.file_size
        try:
            record_id = await _register_download(
                ctx,
                file_id=uploaded_id,
                file_name=normalized_name,
                byte_size=byte_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                async with observe_tool_operation(ctx, "rollback_upload", tool="export_file_to_download_center"):
                    await _file_store(ctx).delete(uploaded_id, headers)
            except Exception as cleanup_exc:
                raise ToolFailed(str(exc) + "；" + tool_error_message(
                    "本次新文件清理失败，可能仍有残留文件", cleanup_exc, headers,
                )) from exc
            raise
        return ExportFileResult(
            source_type="content",
            download_record_id=record_id,
            file_id=uploaded_id,
            file_name=normalized_name,
            byte_size=byte_size,
        )

    async def prepare(ctx: RunContext[AgentDeps], tools: list[Any]) -> list[Any]:
        if _has_download_center_context(ctx):
            return tools
        return []

    return PreparedToolset(toolset, prepare)


__all__ = ["build_file_export_toolset"]
