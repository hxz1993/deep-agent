from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.toolsets import PreparedToolset

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    DocxDocument,
    DocumentPage,
    XlsxWorkbook,
    docx_document,
    select_xlsx_sheet,
    text_document,
    xlsx_workbook,
    xlsx_sheet_metadata,
)
from deep_agent.agent.attachments import AttachmentDownloadError, _download_file
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import is_log_text_attachment, max_upload_bytes
from deep_agent.agent.tool_errors import observe_tool_operation, tool_error_message
from deep_agent.files.office_markdown import pptx_body
from deep_agent.files.pdf_reading import pdf_document
from deep_agent.files.reading_budget import ReadingBudget, paginate_items
from deep_agent.files.schema import (
    AttachmentFileRef,
    FileReadResult,
    FileResourceRef,
    SkillFileRef,
)
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.skills.package import READABLE_SUFFIXES
from deep_agent.skills.runtime import ResolvedSkill

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
        "application/yaml",
        "application/x-yaml",
        "text/yaml",
    }
)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

T = TypeVar("T")
_RESOURCE_ADAPTER = TypeAdapter(FileResourceRef)


@dataclass(frozen=True)
class _ResolvedFile:
    source: str
    resource: AttachmentFileRef | SkillFileRef
    name: str
    mime: str | None
    format: str
    cache_key: str
    content_hash: str | None
    load: Callable[[], Awaitable[bytes]]
    resource_metadata: dict[str, Any] = field(default_factory=dict)


def _normalized_mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _attachment_format(ref: AttachmentRef) -> str | None:
    mime = _normalized_mime(ref.mime)
    if is_log_text_attachment(filename=ref.name or "", mime=mime):
        return "text"
    if mime in _TEXT_MIMES:
        return "text"
    if mime == "application/pdf":
        return "pdf"
    if mime == _DOCX_MIME:
        return "docx"
    if mime == _XLSX_MIME:
        return "xlsx"
    if mime == _PPTX_MIME:
        return "pptx"
    return None


def _skill_format(entry: dict[str, Any]) -> str | None:
    if entry.get("category") == "script":
        return None
    path = Path(str(entry.get("path") or ""))
    suffix = path.suffix.lower()
    mime = _normalized_mime(str(entry.get("mime") or ""))
    if suffix == ".pdf" or mime == "application/pdf":
        return "pdf"
    if suffix == ".docx" or mime == _DOCX_MIME:
        return "docx"
    if suffix == ".xlsx" or mime == _XLSX_MIME:
        return "xlsx"
    if suffix == ".pptx" or mime == _PPTX_MIME:
        return "pptx"
    if bool(entry.get("readable")) or suffix in READABLE_SUFFIXES or mime in _TEXT_MIMES:
        return "text"
    return None


def has_readable_skill_resources(
    resources: dict[str, ResolvedSkill],
) -> bool:
    return any(
        _skill_format(entry) is not None
        for item in resources.values()
        for entry in item.resource_index
    )


def _has_readable_attachment(ctx: RunContext[AgentDeps]) -> bool:
    return any(
        _attachment_format(ref) is not None
        for ref in ctx.deps.attachment_by_id.values()
    )


async def load_file_bytes(
    ctx: RunContext[AgentDeps],
    *,
    key: str,
    loader: Callable[[], Awaitable[bytes]],
    failure_label: str,
    max_bytes: int | None = None,
    expected_hash: str | None = None,
    tool_name: str = "read_file",
) -> bytes:
    cached = ctx.deps.file_bytes.get(key)
    if cached is not None:
        return cached
    task = ctx.deps.file_load_tasks.get(key)
    if task is None:
        async def observed_load() -> bytes:
            async with observe_tool_operation(ctx, "file_read", tool=tool_name):
                return await loader()

        task = asyncio.create_task(observed_load(), name=f"file-load:{key}")
        ctx.deps.file_load_tasks[key] = task
    try:
        data = await task
    except asyncio.CancelledError:
        raise
    except AttachmentDownloadError as exc:
        raise ToolFailed(tool_error_message(f"{failure_label}下载失败", exc, ctx.deps.request_headers)) from exc
    except OSError as exc:
        raise ToolFailed(tool_error_message(f"{failure_label}读取失败", exc, ctx.deps.request_headers)) from exc
    except Exception as exc:
        raise ToolFailed(tool_error_message(f"{failure_label}读取失败", exc, ctx.deps.request_headers)) from exc
    finally:
        ctx.deps.file_load_tasks.pop(key, None)
    if max_bytes is not None and len(data) > max_bytes:
        raise ToolFailed(f"{failure_label}超过当前 {max_bytes} 字节读取上限")
    if expected_hash is not None and hashlib.sha256(data).hexdigest() != expected_hash:
        raise ToolFailed(f"{failure_label}内容Hash与已安装Skill索引不一致")
    ctx.deps.file_bytes[key] = data
    return data


async def _parse_cached(
    ctx: RunContext[AgentDeps],
    *,
    key: str,
    parser: Callable[[], T],
    failure_label: str,
) -> T:
    cached = ctx.deps.file_parsed.get(key)
    if cached is not None:
        return cached
    task = ctx.deps.file_parse_tasks.get(key)
    if task is None:
        async def observed_parse() -> T:
            async with observe_tool_operation(ctx, "file_parse", tool="read_file"):
                return await asyncio.to_thread(parser)

        task = asyncio.create_task(observed_parse(), name=f"file-parse:{key}")
        ctx.deps.file_parse_tasks[key] = task
    try:
        parsed = await task
    except asyncio.CancelledError:
        raise
    except AttachmentParseError as exc:
        raise ToolFailed(str(exc)) from exc
    except Exception as exc:
        raise ToolFailed(tool_error_message(f"{failure_label}解析失败", exc, ctx.deps.request_headers)) from exc
    finally:
        ctx.deps.file_parse_tasks.pop(key, None)
    ctx.deps.file_parsed[key] = parsed
    return parsed


def _safe_skill_file(
    resources: dict[str, ResolvedSkill],
    ref: SkillFileRef,
    *, tool_name: str = "read_file",
) -> tuple[ResolvedSkill, dict[str, Any], Path]:
    item = resources.get(ref.skill_code)
    if item is None:
        choices = "、".join(sorted(resources)) or "无"
        raise ModelRetry(
            f"Skill {ref.skill_code!r} 未授权或当前不可用；可选 Skill：{choices}"
        )
    entry = next(
        (candidate for candidate in item.resource_index if candidate.get("path") == ref.path),
        None,
    )
    if entry is None:
        raise ModelRetry(f"Skill {ref.skill_code!r} 中不存在资源 {ref.path!r}")
    if entry.get("category") == "script":
        raise ModelRetry(f"Skill脚本不能通过 {tool_name} 读取；需要时使用 execute_skill_script")
    target = (item.package_root / ref.path).resolve()
    root = item.package_root.resolve()
    if target != root and root not in target.parents:
        raise ModelRetry("Skill资源路径超出已安装包")
    if not target.is_file():
        raise ToolFailed(f"Skill资源文件缺失: {ref.path}")
    return item, entry, target


async def resolve_file_source(
    ctx: RunContext[AgentDeps],
    resource: FileResourceRef | dict[str, Any],
    resources: dict[str, ResolvedSkill],
    *, tool_name: str = "read_file",
) -> _ResolvedFile:
    """Authorize the original source on every call, independently of parsers/caches."""
    try:
        resource = _RESOURCE_ADAPTER.validate_python(resource)
    except ValidationError as exc:
        raise ModelRetry(
            "resource 必须是附件 {type: 'attachment', file_id: ...}，"
            "或 Skill 文件 {type: 'skill', skill_code: ..., path: ...}"
        ) from exc
    if isinstance(resource, AttachmentFileRef):
        ref = ctx.deps.attachment_by_id.get(resource.file_id)
        if ref is None:
            raise ModelRetry(
                "file_id 不属于当前会话附件；请只使用本轮提示或当前原始会话历史中的 file_id"
            )

        async def load_attachment() -> bytes:
            return await _download_file(
                ref.id, ctx.deps.request_headers, file_store=ctx.deps.file_store,
            )

        return _ResolvedFile(
            source="attachment", resource=resource, name=ref.name or ref.id,
            mime=ref.mime, format="", cache_key=f"attachment:{ref.id}",
            content_hash=None, load=load_attachment,
        )

    _item, entry, target = _safe_skill_file(resources, resource, tool_name=tool_name)

    async def load_skill_file() -> bytes:
        return await asyncio.to_thread(target.read_bytes)

    content_hash = str(entry.get("content_hash") or "") or None
    return _ResolvedFile(
        source="skill", resource=resource, name=Path(resource.path).name,
        mime=str(entry.get("mime") or "") or None, format="",
        cache_key=f"skill:{resource.skill_code}:{resource.path}:{content_hash or ''}",
        content_hash=content_hash, load=load_skill_file, resource_metadata=entry,
    )


async def _resolve_file(
    ctx: RunContext[AgentDeps],
    resource: FileResourceRef | dict[str, Any],
    resources: dict[str, ResolvedSkill],
) -> _ResolvedFile:
    resolved = await resolve_file_source(ctx, resource, resources)
    if isinstance(resolved.resource, AttachmentFileRef):
        ref = ctx.deps.attachment_by_id[resolved.resource.file_id]
        kind = _attachment_format(ref)
        if kind is None:
            raise ModelRetry(
                f"read_file 不支持 MIME 为 {ref.mime or 'unknown'} 的附件；"
                "图片、音频和视频只在当前轮作为模型原生附件提交，本工具不执行OCR；旧二进制DOC/XLS/PPT不支持"
            )
    else:
        kind = _skill_format(resolved.resource_metadata)
        if kind is None:
            raise ModelRetry(f"read_file 暂不支持 Skill资源 {resolved.resource.path!r} 的文件类型")
    return replace(resolved, format=kind)


def _result(
    resolved: _ResolvedFile,
    *,
    unit: str,
    content: str,
    metadata: dict[str, Any],
    offset: int,
    limit: int,
    returned_count: int,
    total_count: int,
    next_offset: int | None,
    has_more: bool,
    truncated_by: str | None,
    content_hash: str,
    char_offset: int,
    next_char_offset: int | None,
) -> FileReadResult:
    return FileReadResult(
        source=resolved.source,
        resource=resolved.resource,
        name=resolved.name,
        mime=resolved.mime,
        format=resolved.format,
        unit=unit,
        content=content,
        metadata=metadata,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        returned_count=returned_count,
        total_count=total_count,
        next_offset=next_offset,
        next_char_offset=next_char_offset,
        has_more=has_more,
        truncated_by=truncated_by,
        content_hash=content_hash,
    )


async def _read_paged(
    ctx: RunContext[AgentDeps],
    resolved: _ResolvedFile,
    *,
    data: bytes,
    offset: int | None,
    limit: int | None,
    sheet: str | None,
    content_hash: str,
    char_offset: int,
    budget: ReadingBudget,
) -> FileReadResult:
    start = 1 if offset is None else offset
    if start < 1 or (limit is not None and limit < 1):
        raise AttachmentParseError("offset、limit 必须是正整数（offset 从 1 开始）")
    if sheet is not None and resolved.format != "xlsx":
        raise ModelRetry("sheet 仅适用于 XLSX 文件")
    if resolved.format == "text":
        items, metadata = await _parse_cached(
            ctx, key=f"{resolved.cache_key}:text",
            parser=lambda: text_document(data, name=resolved.name, mime=resolved.mime),
            failure_label=resolved.name,
        )
        unit = "line"
    elif resolved.format == "docx":
        document: DocxDocument = await _parse_cached(
            ctx, key=f"{resolved.cache_key}:docx", parser=lambda: docx_document(data),
            failure_label=resolved.name,
        )
        items, metadata, unit = document.blocks, document.metadata, "block"
    elif resolved.format == "pdf":
        items, metadata = await _parse_cached(
            ctx, key=f"{resolved.cache_key}:pdf", parser=lambda: pdf_document(data),
            failure_label=resolved.name,
        )
        unit = "page"
    elif resolved.format == "pptx":
        items, metadata = await _parse_cached(
            ctx, key=f"{resolved.cache_key}:pptx", parser=lambda: pptx_body(data),
            failure_label=resolved.name,
        )
        unit = "block"
    else:
        workbook: XlsxWorkbook = await _parse_cached(
            ctx, key=f"{resolved.cache_key}:xlsx", parser=lambda: xlsx_workbook(data),
            failure_label=resolved.name,
        )
        selected = select_xlsx_sheet(workbook, sheet)
        items, metadata, unit = selected.rows, xlsx_sheet_metadata(workbook, selected), "row"
    # An omitted limit is a source-sized range, not an arbitrary item cap. Keep
    # the established integer result field, including empty and EOF reads.
    page_limit = limit if limit is not None else max(1, len(items) - start + 1)

    def result(page: DocumentPage) -> FileReadResult:
        batch_metadata = metadata
        if resolved.format == "pdf":
            end = start + page.returned_count - 1
            batch_metadata = {
                **metadata,
                "pages_without_text": [
                    number for number in metadata.get("pages_without_text", [])
                    if start <= number <= end
                ],
                "pages_without_text_count": len(metadata.get("pages_without_text", [])),
            }
        if resolved.format == "xlsx":
            from openpyxl.utils.cell import range_boundaries

            end = start + page.returned_count - 1
            batch_metadata = {
                **metadata,
                "merged_ranges": [
                    bounds for bounds in selected.merged_ranges
                    if range_boundaries(bounds)[1] <= end
                    and range_boundaries(bounds)[3] >= start
                ],
            }
        return _result(
            resolved, unit=unit, content=page.content, metadata=batch_metadata,
            offset=start, limit=page_limit, returned_count=page.returned_count,
            total_count=len(items), next_offset=page.next_offset,
            has_more=page.has_more, truncated_by=page.truncated_by,
            content_hash=content_hash, char_offset=char_offset,
            next_char_offset=page.next_char_offset,
        )

    page = await asyncio.to_thread(
        paginate_items,
        items[start - 1 : start - 1 + page_limit], total_count=len(items),
        offset=start, limit=page_limit, char_offset=char_offset, budget=budget,
        envelope=lambda page: result(page).model_dump(mode="json"),
    )
    return result(page)


def build_file_toolset(
    resources: dict[str, ResolvedSkill] | None = None,
    *,
    context_window_tokens: int | None = None,
    estimator: TokenEstimator | None = None,
    document_available: bool = False,
) -> PreparedToolset[AgentDeps]:
    budget = ReadingBudget(estimator or TokenEstimator.conservative(), context_window_tokens)
    authorized = dict(resources or {})
    toolset = FunctionToolset[AgentDeps](
        id="file-resources",
        instructions=(
            "read_file直接读取文本、DOCX正文和补充元信息、XLSX表格、PPTX文本/表格/备注及PDF文本层。PDF保留物理页，未提取到文本不等于原件无内容。本工具不支持旧DOC/XLS/PPT，不执行图片或扫描件OCR。"
            "来源限当前会话附件和已授权Skill文件。先使用来源提供的精确file_id，"
            "或先调用list_skill_resources取得Skill路径。首次通常只传resource；若has_more=true，"
            "把next_offset和next_char_offset分别作为offset和char_offset继续读取；"
            "长行/段落可能分多页，必须同时传两个续读值。不要猜测file_id、Skill编码或路径，脚本不能作为文件正文读取。"
            + ("扫描PDF、图片或需要OCR/复杂版面时，可使用本轮已提供的parse_document，以当前供应商支持格式为准。" if document_available else "")
        ),
    )

    @toolset.tool
    async def read_file(
        ctx: RunContext[AgentDeps],
        resource: FileResourceRef,
        offset: int | None = None,
        limit: int | None = None,
        sheet: str | None = None,
        char_offset: int = 0,
    ) -> FileReadResult:
        """直接读取文本、DOCX正文和元信息、XLSX表格、PPTX文本/表格/备注及PDF文本层。PDF按物理页读取，不执行OCR；不支持旧DOC/XLS/PPT。

        有下一页时同时使用 next_offset/next_char_offset 续读。

        Args:
            resource: 当前会话附件或已授权 Skill 文件引用。
            offset: 起始文本行、DOCX/PPTX逻辑块、XLSX物理行或PDF物理页，从 1 开始。
            limit: 最多读取的源项数；省略时按 Token 预算尽量返回全文（含包装 24000/48000 Token）。
            sheet: XLSX 工作表名，省略时读取第一个可见工作表。
            char_offset: 当前 offset 项内部的字符偏移，从 0 开始；续读时使用 next_char_offset。
        """
        resolved = await _resolve_file(ctx, resource, authorized)
        data = await load_file_bytes(
            ctx,
            key=resolved.cache_key,
            loader=resolved.load,
            failure_label=resolved.name,
            max_bytes=max_upload_bytes() if resolved.source == "attachment" else None,
            expected_hash=resolved.content_hash,
        )
        content_hash = resolved.content_hash or hashlib.sha256(data).hexdigest()
        try:
            return await _read_paged(
                ctx,
                resolved,
                data=data,
                offset=offset,
                limit=limit,
                sheet=sheet,
                content_hash=content_hash,
                char_offset=char_offset,
                budget=budget,
            )
        except AttachmentParseError as exc:
            raise ModelRetry(str(exc)) from exc

    async def prepare(ctx: RunContext[AgentDeps], tools: dict[str, Any]) -> dict[str, Any]:
        if _has_readable_attachment(ctx) or has_readable_skill_resources(authorized):
            return tools
        return {}

    return PreparedToolset(toolset, prepare)
