from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic_ai import BinaryContent, FunctionToolset, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    DocxDocument,
    XlsxWorkbook,
    docx_document,
    image_mime,
    normalize_range,
    paginate_items,
    process_image,
    select_xlsx_sheet,
    text_document,
    xlsx_workbook,
)
from deep_agent.agent.attachments import AttachmentDownloadError, _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.capabilities.attachments.schema import AttachmentReadResult
from deep_agent.capabilities.registry import Capability, ToolInfo
from deep_agent.schemas.attachment import AttachmentRef

_TEXT_MIMES = frozenset(
    {
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/html",
        "text/xml",
        "application/xml",
        "application/json",
        "text/json",
    }
)
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_TEXT_DEFAULT_LIMIT = 500
_TEXT_MAX_LIMIT = 2_000
_DOCX_DEFAULT_LIMIT = 100
_DOCX_MAX_LIMIT = 500
_XLSX_DEFAULT_LIMIT = 100
_XLSX_MAX_LIMIT = 500

T = TypeVar("T")


def _attachment_ref(ctx: RunContext[AgentDeps], file_id: str) -> AttachmentRef:
    ref = ctx.deps.attachment_by_id.get(file_id)
    if ref is None:
        raise ModelRetry(
            "file_id 不属于当前会话的附件，请只使用本轮提示或当前会话历史中提供的 file_id"
        )
    return ref


def _normalized_mime(ref: AttachmentRef) -> str:
    return (ref.mime or "").split(";", 1)[0].strip().lower()


def _normalized_range(
    *,
    offset: int | None,
    limit: int | None,
    default_limit: int,
    max_limit: int,
) -> tuple[int, int]:
    try:
        return normalize_range(
            offset=offset,
            limit=limit,
            default_limit=default_limit,
            max_limit=max_limit,
        )
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc


def _require_mime(
    ref: AttachmentRef,
    *,
    allowed: frozenset[str],
    tool_name: str,
) -> str:
    mime = _normalized_mime(ref)
    if mime not in allowed:
        raise ModelRetry(
            f"{tool_name} 不能读取 MIME 为 {ref.mime or 'unknown'} 的附件"
        )
    return mime


async def _download_cached(
    ctx: RunContext[AgentDeps],
    ref: AttachmentRef,
) -> bytes:
    cached = ctx.deps.attachment_bytes.get(ref.id)
    if cached is not None:
        return cached

    task = ctx.deps.attachment_download_tasks.get(ref.id)
    if task is None:
        task = asyncio.create_task(
            _download_file(ref.id, ctx.deps.request_headers),
            name=f"attachment-download:{ref.id}",
        )
        ctx.deps.attachment_download_tasks[ref.id] = task
    try:
        data = await task
    except AttachmentDownloadError as exc:
        raise ModelRetry(f"附件 {ref.name or ref.id} 下载失败: {exc}") from exc
    except Exception as exc:
        raise ModelRetry(
            f"附件 {ref.name or ref.id} 下载失败: {type(exc).__name__}"
        ) from exc
    finally:
        ctx.deps.attachment_download_tasks.pop(ref.id, None)

    if len(data) > max_upload_bytes():
        raise ModelRetry(
            f"附件 {ref.name or ref.id} 超过当前 {max_upload_bytes()} 字节读取上限"
        )
    ctx.deps.attachment_bytes[ref.id] = data
    return data


async def _parse_cached(
    ctx: RunContext[AgentDeps],
    *,
    key: str,
    parser: Callable[[], T],
) -> T:
    cached = ctx.deps.attachment_parsed.get(key)
    if cached is not None:
        return cached
    task = ctx.deps.attachment_parse_tasks.get(key)
    if task is None:
        task = asyncio.create_task(
            asyncio.to_thread(parser),
            name=f"attachment-parse:{key}",
        )
        ctx.deps.attachment_parse_tasks[key] = task
    try:
        parsed = await task
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc
    except Exception as exc:
        raise ModelRetry(f"附件解析失败: {type(exc).__name__}: {exc}") from exc
    finally:
        ctx.deps.attachment_parse_tasks.pop(key, None)
    ctx.deps.attachment_parsed[key] = parsed
    return parsed


def _result(
    *,
    ref: AttachmentRef,
    unit: str,
    offset: int,
    limit: int,
    total_count: int,
    page: Any,
    metadata: dict[str, Any],
) -> AttachmentReadResult:
    return AttachmentReadResult(
        file_id=ref.id,
        name=ref.name,
        mime=ref.mime,
        unit=unit,
        offset=offset,
        limit=limit,
        returned_count=page.returned_count,
        total_count=total_count,
        content=page.content,
        has_more=page.has_more,
        next_offset=page.next_offset,
        truncated_by=page.truncated_by,
        metadata=metadata,
    )


async def read_text_attachment(
    ctx: RunContext[AgentDeps],
    file_id: str,
    offset: int | None = None,
    limit: int | None = None,
) -> AttachmentReadResult:
    """按行读取文本附件。

    首次调用通常只传 ``file_id``。默认从第 1 行读取安全大小的一页；当
    ``has_more`` 为 true 时，使用返回的 ``next_offset`` 继续读取。

    Args:
        file_id: 当前会话附件提示中给出的文件 ID。
        offset: 可选的起始行，1 表示第一行。
        limit: 可选的最大行数；超出内部上限时会自动收紧。
    """
    ref = _attachment_ref(ctx, file_id)
    mime = _require_mime(
        ref,
        allowed=_TEXT_MIMES,
        tool_name="read_text_attachment",
    )
    resolved_offset, resolved_limit = _normalized_range(
        offset=offset,
        limit=limit,
        default_limit=_TEXT_DEFAULT_LIMIT,
        max_limit=_TEXT_MAX_LIMIT,
    )
    data = await _download_cached(ctx, ref)
    lines, metadata = await _parse_cached(
        ctx,
        key=f"text:{ref.id}:{mime}",
        parser=lambda: text_document(data, name=ref.name, mime=mime),
    )
    start = resolved_offset - 1
    page = paginate_items(
        lines[start : start + resolved_limit],
        total_count=len(lines),
        offset=resolved_offset,
        limit=resolved_limit,
    )
    return _result(
        ref=ref,
        unit="line",
        offset=resolved_offset,
        limit=resolved_limit,
        total_count=len(lines),
        page=page,
        metadata=metadata,
    )


async def read_docx(
    ctx: RunContext[AgentDeps],
    file_id: str,
    offset: int | None = None,
    limit: int | None = None,
) -> AttachmentReadResult | ToolReturn:
    """按逻辑块读取 DOCX。

    逻辑块依次包含页眉、正文段落/表格、页脚和批注附录。首次调用通常只传
    ``file_id``；如果还有内容，使用 ``next_offset`` 继续读取。文档内嵌图片
    仅在首次读取且当前模型支持图片输入时，以文档级图片集合附带返回。

    Args:
        file_id: 当前会话附件提示中给出的文件 ID。
        offset: 可选的起始逻辑块，1 表示第一个块。
        limit: 可选的最大逻辑块数量；超出内部上限时会自动收紧。
    """
    ref = _attachment_ref(ctx, file_id)
    _require_mime(
        ref,
        allowed=frozenset({_DOCX_MIME}),
        tool_name="read_docx",
    )
    resolved_offset, resolved_limit = _normalized_range(
        offset=offset,
        limit=limit,
        default_limit=_DOCX_DEFAULT_LIMIT,
        max_limit=_DOCX_MAX_LIMIT,
    )
    data = await _download_cached(ctx, ref)
    document: DocxDocument = await _parse_cached(
        ctx,
        key=f"docx:{ref.id}",
        parser=lambda: docx_document(data),
    )
    start = resolved_offset - 1
    page = paginate_items(
        document.blocks[start : start + resolved_limit],
        total_count=len(document.blocks),
        offset=resolved_offset,
        limit=resolved_limit,
    )
    image_capable = "image" in set(ctx.deps.input_modalities)
    include_images = resolved_offset == 1 and image_capable and bool(document.images)
    metadata = {
        **document.metadata,
        "embedded_images_included": include_images,
        "embedded_images_note": (
            "图片按文档级集合返回，不代表它们在正文中的准确位置"
            if document.images
            else None
        ),
    }
    result = _result(
        ref=ref,
        unit="block",
        offset=resolved_offset,
        limit=resolved_limit,
        total_count=len(document.blocks),
        page=page,
        metadata=metadata,
    )
    if not include_images:
        return result

    multimodal: list[Any] = [
        f"以下是附件 {ref.name or ref.id} 中的文档级内嵌图片集合，共 {len(document.images)} 张："
    ]
    for index, (name, raw) in enumerate(document.images, start=1):
        mime = image_mime(raw, name)
        if mime == "application/octet-stream":
            continue
        try:
            processed, output_mime, _ = process_image(
                raw,
                filename=name,
                mime=mime,
            )
        except AttachmentParseError:
            processed, output_mime = raw, mime
        multimodal.append(f"内嵌图片 {index}（{name}）:")
        multimodal.append(BinaryContent(data=processed, media_type=output_mime))
    if len(multimodal) == 1:
        return result
    return ToolReturn(return_value=result, content=multimodal)


async def read_xlsx(
    ctx: RunContext[AgentDeps],
    file_id: str,
    sheet: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> AttachmentReadResult:
    """按物理行读取 XLSX 工作表。

    未指定 ``sheet`` 时默认读取第一个可见工作表。首次调用通常只传
    ``file_id``；如果还有内容，使用 ``next_offset`` 继续读取。

    Args:
        file_id: 当前会话附件提示中给出的文件 ID。
        sheet: 可选工作表名，不区分大小写。
        offset: 可选起始物理行，1 包含表头行。
        limit: 可选最大行数；超出内部上限时会自动收紧。
    """
    ref = _attachment_ref(ctx, file_id)
    _require_mime(
        ref,
        allowed=frozenset({_XLSX_MIME}),
        tool_name="read_xlsx",
    )
    resolved_offset, resolved_limit = _normalized_range(
        offset=offset,
        limit=limit,
        default_limit=_XLSX_DEFAULT_LIMIT,
        max_limit=_XLSX_MAX_LIMIT,
    )
    data = await _download_cached(ctx, ref)
    workbook: XlsxWorkbook = await _parse_cached(
        ctx,
        key=f"xlsx:{ref.id}",
        parser=lambda: xlsx_workbook(data),
    )
    try:
        selected = select_xlsx_sheet(workbook, sheet)
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc

    start = resolved_offset - 1
    page = paginate_items(
        selected.rows[start : start + resolved_limit],
        total_count=len(selected.rows),
        offset=resolved_offset,
        limit=resolved_limit,
    )
    sheet_metadata = [
        {
            "name": item.name,
            "state": item.state,
            "rows": len(item.rows),
            "columns": item.columns,
            "headers": list(item.headers),
        }
        for item in workbook.sheets
    ]
    return _result(
        ref=ref,
        unit="row",
        offset=resolved_offset,
        limit=resolved_limit,
        total_count=len(selected.rows),
        page=page,
        metadata={
            "selected_sheet": selected.name,
            "sheets": sheet_metadata,
            "formula_mode": "cached_values_only",
        },
    )


def build_attachment_capability() -> Capability:
    toolset = FunctionToolset(
        instructions=(
            "用户上传的文本、DOCX、XLSX 只以 file_id 告知模型，需要时使用对应附件工具读取。"
            "首次调用通常只传 file_id；工具会返回安全大小的第一页。若 has_more=true，"
            "使用 next_offset 继续读取。不要猜测或编造 file_id。PDF 不由这些工具解析；"
            "只有模型声明支持原生 PDF 输入时平台才会发送 PDF。"
        ),
        tools=[read_text_attachment, read_docx, read_xlsx],
    )
    return Capability(
        name="attachment",
        kind="attachment",
        toolset=toolset,
        description="按需读取当前会话上传的文本、Word 和 Excel 附件。",
        tools=[
            ToolInfo(
                "read_text_attachment",
                "按行分页读取 TXT、CSV、JSON、Markdown、HTML 或 XML。",
            ),
            ToolInfo(
                "read_docx",
                "按逻辑块分页读取 DOCX 的页眉、正文、表格、页脚和批注。",
            ),
            ToolInfo(
                "read_xlsx",
                "按物理行分页读取 XLSX，可指定工作表。",
            ),
        ],
        default_enabled=False,
    )
