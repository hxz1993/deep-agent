from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic_ai import (
    BinaryContent,
    FunctionToolset,
    ModelRetry,
    RunContext,
    ToolFailed,
)
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
from deep_agent.capabilities.registry import ToolInfo, ToolPackage
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
        raise ToolFailed(f"附件 {ref.name or ref.id} 下载失败: {exc}") from exc
    except Exception as exc:
        raise ToolFailed(
            f"附件 {ref.name or ref.id} 下载失败: {type(exc).__name__}"
        ) from exc
    finally:
        ctx.deps.attachment_download_tasks.pop(ref.id, None)

    if len(data) > max_upload_bytes():
        raise ToolFailed(
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
        raise ToolFailed(str(exc)) from exc
    except Exception as exc:
        raise ToolFailed(f"附件解析失败: {type(exc).__name__}: {exc}") from exc
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


@dataclass(frozen=True)
class _PageSource:
    items: tuple[str, ...]
    metadata: dict[str, Any]


ParsedT = TypeVar("ParsedT")


class _PagedAttachmentReader(ABC, Generic[ParsedT]):
    """Template method for a paged, on-demand attachment tool."""
    tool_name: str
    allowed_mimes: frozenset[str]
    unit: str
    default_limit: int
    max_limit: int

    async def read(
        self,
        ctx: RunContext[AgentDeps],
        file_id: str,
        *,
        offset: int | None,
        limit: int | None,
    ) -> AttachmentReadResult | ToolReturn:
        ref = _attachment_ref(ctx, file_id)
        mime = _require_mime(ref, allowed=self.allowed_mimes, tool_name=self.tool_name,)
        resolved_offset, resolved_limit = _normalized_range(
            offset=offset,
            limit=limit,
            default_limit=self.default_limit,
            max_limit=self.max_limit,
        )
        data = await _download_cached(ctx, ref)
        parsed = await _parse_cached(
            ctx,
            key=self._cache_key(ref, mime),
            parser=lambda: self._parse(data, ref=ref, mime=mime),
        )
        source = self._page_source(
            ctx,
            parsed,
            offset=resolved_offset,
        )
        page = paginate_items(
            source.items[resolved_offset - 1 : resolved_offset - 1 + resolved_limit],
            total_count=len(source.items),
            offset=resolved_offset,
            limit=resolved_limit,
        )
        result = _result(
            ref=ref,
            unit=self.unit,
            offset=resolved_offset,
            limit=resolved_limit,
            total_count=len(source.items),
            page=page,
            metadata=source.metadata,
        )
        return self._wrap_result(ctx, parsed, offset=resolved_offset, result=result,)

    @abstractmethod
    def _cache_key(self, ref: AttachmentRef, mime: str) -> str: ...

    @abstractmethod
    def _parse(
        self,
        data: bytes,
        *,
        ref: AttachmentRef,
        mime: str,
    ) -> ParsedT: ...

    @abstractmethod
    def _page_source(
        self,
        ctx: RunContext[AgentDeps],
        parsed: ParsedT,
        *,
        offset: int,
    ) -> _PageSource: ...

    def _wrap_result(
        self,
        ctx: RunContext[AgentDeps],
        parsed: ParsedT,
        *,
        offset: int,
        result: AttachmentReadResult,
    ) -> AttachmentReadResult | ToolReturn:
        return result


class _TextReader(_PagedAttachmentReader[tuple[tuple[str, ...], dict[str, Any]]]):
    tool_name = "read_text_attachment"
    allowed_mimes = _TEXT_MIMES
    unit = "line"
    default_limit = _TEXT_DEFAULT_LIMIT
    max_limit = _TEXT_MAX_LIMIT

    def _cache_key(self, ref: AttachmentRef, mime: str) -> str:
        return f"text:{ref.id}:{mime}"

    def _parse(
        self,
        data: bytes,
        *,
        ref: AttachmentRef,
        mime: str,
    ) -> tuple[tuple[str, ...], dict[str, Any]]:
        return text_document(data, name=ref.name, mime=mime)

    def _page_source(
        self,
        _ctx: RunContext[AgentDeps],
        parsed: tuple[tuple[str, ...], dict[str, Any]],
        *,
        offset: int,
    ) -> _PageSource:
        lines, metadata = parsed
        return _PageSource(items=lines, metadata=metadata)


class _DocxReader(_PagedAttachmentReader[DocxDocument]):
    tool_name = "read_docx"
    allowed_mimes = frozenset({_DOCX_MIME})
    unit = "block"
    default_limit = _DOCX_DEFAULT_LIMIT
    max_limit = _DOCX_MAX_LIMIT

    def _cache_key(self, ref: AttachmentRef, mime: str) -> str:
        return f"docx:{ref.id}"

    def _parse(
        self,
        data: bytes,
        *,
        ref: AttachmentRef,
        mime: str,
    ) -> DocxDocument:
        return docx_document(data)

    def _page_source(
        self,
        ctx: RunContext[AgentDeps],
        parsed: DocxDocument,
        *,
        offset: int,
    ) -> _PageSource:
        include_images = self._include_images(ctx, parsed, offset=offset)
        return _PageSource(
            items=parsed.blocks,
            metadata={
                **parsed.metadata,
                "embedded_images_included": include_images,
                "embedded_images_note": (
                    "图片按文档级集合返回，不代表它们在正文中的准确位置"
                    if parsed.images else None
                ),
            },
        )

    def _wrap_result(
        self,
        ctx: RunContext[AgentDeps],
        parsed: DocxDocument,
        *,
        offset: int,
        result: AttachmentReadResult,
    ) -> AttachmentReadResult | ToolReturn:
        if not self._include_images(ctx, parsed, offset=offset):
            return result

        multimodal: list[Any] = [
            f"以下是附件 {result.name or result.file_id} 中的文档级内嵌图片集合，共 {len(parsed.images)} 张："
        ]
        for index, (name, raw) in enumerate(parsed.images, start=1):
            mime = image_mime(raw, name)
            if mime == "application/octet-stream":
                continue
            try:
                processed, output_mime, _ = process_image(raw, filename=name, mime=mime,)
            except AttachmentParseError:
                processed, output_mime = raw, mime
            multimodal.append(f"内嵌图片 {index}（{name}）:")
            multimodal.append(BinaryContent(data=processed, media_type=output_mime))
        if len(multimodal) == 1:
            return result
        return ToolReturn(return_value=result, content=multimodal)

    @staticmethod
    def _include_images(
        ctx: RunContext[AgentDeps],
        document: DocxDocument,
        *,
        offset: int,
    ) -> bool:
        return (
            offset == 1
            and "image" in set(ctx.deps.input_modalities)
            and bool(document.images)
        )


class _XlsxReader(_PagedAttachmentReader[XlsxWorkbook]):
    tool_name = "read_xlsx"
    allowed_mimes = frozenset({_XLSX_MIME})
    unit = "row"
    default_limit = _XLSX_DEFAULT_LIMIT
    max_limit = _XLSX_MAX_LIMIT

    def __init__(self, sheet: str | None) -> None:
        self._sheet = sheet

    def _cache_key(self, ref: AttachmentRef, mime: str) -> str:
        return f"xlsx:{ref.id}"

    def _parse(
        self,
        data: bytes,
        *,
        ref: AttachmentRef,
        mime: str,
    ) -> XlsxWorkbook:
        return xlsx_workbook(data)

    def _page_source(
        self,
        _ctx: RunContext[AgentDeps],
        parsed: XlsxWorkbook,
        *,
        offset: int,
    ) -> _PageSource:
        try:
            selected = select_xlsx_sheet(parsed, self._sheet)
        except AttachmentParseError as exc:
            raise ModelRetry(str(exc)) from exc
        return _PageSource(
            items=selected.rows,
            metadata={
                "selected_sheet": selected.name,
                "sheets": [
                    {
                        "name": item.name,
                        "state": item.state,
                        "rows": len(item.rows),
                        "columns": item.columns,
                        "headers": list(item.headers),
                    }
                    for item in parsed.sheets
                ],
                "formula_mode": "cached_values_only",
            },
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
    return await _TextReader().read(ctx, file_id, offset=offset, limit=limit,)


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
    return await _DocxReader().read(ctx, file_id, offset=offset, limit=limit,)


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
    return await _XlsxReader(sheet).read(ctx, file_id, offset=offset, limit=limit,)


def build_attachment_tool_package() -> ToolPackage:
    toolset = FunctionToolset(
        instructions=(
            "用户上传的文本、DOCX、XLSX 只以 file_id 告知模型，需要时使用对应附件工具读取。"
            "首次调用通常只传 file_id；工具会返回安全大小的第一页。若 has_more=true，"
            "使用 next_offset 继续读取。不要猜测或编造 file_id。PDF 不由这些工具解析；"
            "只有模型声明支持原生 PDF 输入时平台才会发送 PDF。"
        ),
        tools=[read_text_attachment, read_docx, read_xlsx],
    )
    return ToolPackage(
        code="builtin:attachment",
        category="builtin",
        toolset=toolset,
        label="附件读取",
        source="builtin",
        description="按需读取当前会话上传的文本、Word 和 Excel 附件。",
        tools=[
            ToolInfo(
                "read_text_attachment",
                "读取文本附件",
                "按行分页读取 TXT、CSV、JSON、Markdown、HTML 或 XML。",
            ),
            ToolInfo(
                "read_docx",
                "读取 Word 文档",
                "按逻辑块分页读取 DOCX 的页眉、正文、表格、页脚和批注。",
            ),
            ToolInfo(
                "read_xlsx",
                "读取 Excel 工作簿",
                "按物理行分页读取 XLSX，可指定工作表。",
            ),
        ],
        default_enabled=False,
        disclosure_mode="conditional",
        disclosure_condition="session_has_attachment",
    )
