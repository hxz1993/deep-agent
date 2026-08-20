from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.toolsets import PreparedToolset

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.files.export_schema import (
    ExistingPlatformFileSource,
    ExportFileResult,
    ExportFileSource,
    GeneratedTextFileSource,
)
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT, PlatformAPI
from deepfos_platform.client.space import SpaceAPI

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


def _display_size(byte_size: int) -> tuple[float, str]:
    size = float(max(0, byte_size))
    unit = "B"
    for candidate in ("KB", "MB", "GB"):
        if size < 1024:
            break
        size /= 1024
        unit = candidate
    return round(size, 2), unit


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
            data = await _download_file(file_id, _request_headers(ctx))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed("已有附件下载失败，无法登记到下载中心") from exc
        ctx.deps.file_bytes[cache_key] = data
        return data, len(data)

    artifact = ctx.deps.tool_output_artifacts.get(file_id)
    if artifact is None:
        raise ModelRetry(
            "file_id 不属于当前会话附件或大工具结果；"
            "请只使用当前会话中已明确提供的平台 File ID"
        )
    byte_size = artifact.get("byte_size")
    if isinstance(byte_size, int) and byte_size >= 0:
        return None, byte_size
    cache_key = f"tool-result:{file_id}"
    cached = ctx.deps.file_bytes.get(cache_key)
    if cached is not None:
        return cached, len(cached)
    try:
        data = await _download_file(file_id, _request_headers(ctx))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ToolFailed("大工具结果下载失败，无法登记到下载中心") from exc
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
    display_size, unit = _display_size(byte_size)
    payload: dict[str, Any] = {
        "app": headers["app"],
        "space": headers["space"],
        "fileId": file_id,
        "fileName": file_name,
        "fileSize": display_size,
        "unit": unit,
        "status": "SUCCESS",
        "percentage": 100,
        "opeType": "ELEMENT_EXPORT",
        "opeTypeDesc": "Agent文件导出",
        # The platform field is historically named `fileDescription`, but both
        # the Download Center UI and download response use it as the final file
        # name. It must therefore retain the extension.
        "fileDescription": file_name,
        "createTime": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        record_id = await SpaceAPI(header=headers).business_record.save(payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ToolFailed("下载中心登记失败，请稍后重试") from exc
    if not isinstance(record_id, str) or not record_id:
        raise ToolFailed("下载中心登记成功但未返回记录ID")
    return record_id


def build_file_export_toolset() -> PreparedToolset[AgentDeps]:
    toolset = FunctionToolset[AgentDeps](
        id="file-export",
        instructions=(
            "当用户明确需要导出、生成或保存可下载文件时，使用 "
            "export_file_to_download_center 将文件发布到当前用户的DeepFOS下载中心。"
            "可以复用当前会话附件或大工具结果，也可以提交新生成的UTF-8文本文件内容。"
            "成功后文件已经可以在下载中心获取，最终回答应说明文件名并引导用户前往下载中心。"
            "普通文字回答不要调用，不得传宿主机路径或未经当前会话授权的File ID。"
        ),
    )

    @toolset.tool
    async def export_file_to_download_center(
        ctx: RunContext[AgentDeps],
        source: ExportFileSource,
        file_name: str,
    ) -> ExportFileResult:
        """用户明确需要导出文件时，将新文本内容或当前会话平台文件发布到下载中心。

        使用 content 新建UTF-8文本文件；使用 platform_file 复用当前会话附件或大工具结果，
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
        platform = PlatformAPI(header=headers)
        try:
            uploaded = await platform.file.upload(
                file_type=FILE_TYPE_ATTACHMENT,
                file_name=normalized_name,
                file=data,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed("新文件上传到平台失败，请检查格式或稍后重试") from exc
        uploaded_id = getattr(uploaded, "id", None)
        if not isinstance(uploaded_id, str) or not uploaded_id:
            raise ToolFailed("平台文件上传成功但未返回File ID")
        uploaded_size = getattr(uploaded, "fileSize", None)
        byte_size = uploaded_size if isinstance(uploaded_size, int) and uploaded_size >= 0 else len(data)
        try:
            record_id = await _register_download(
                ctx,
                file_id=uploaded_id,
                file_name=normalized_name,
                byte_size=byte_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await platform.file.delete_file(uploaded_id)
            except Exception:
                pass
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
