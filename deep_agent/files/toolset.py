from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import BinaryContent, FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import ToolReturn
from pydantic_ai.toolsets import PreparedToolset

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
from deep_agent.files.schema import (
    AttachmentFileRef,
    FileReadResult,
    FileResourceRef,
    SkillFileRef,
)
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.skills.package import IMAGE_SUFFIXES, READABLE_SUFFIXES
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
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

_TEXT_DEFAULT_LIMIT = 500
_TEXT_MAX_LIMIT = 2_000
_OFFICE_DEFAULT_LIMIT = 100
_OFFICE_MAX_LIMIT = 500

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


def _normalized_mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _attachment_format(ref: AttachmentRef) -> str | None:
    mime = _normalized_mime(ref.mime)
    if mime in _TEXT_MIMES:
        return "text"
    if mime == _DOCX_MIME:
        return "docx"
    if mime == _XLSX_MIME:
        return "xlsx"
    if mime in _IMAGE_MIMES:
        return "image"
    return None


def _skill_format(entry: dict[str, Any]) -> str | None:
    if entry.get("category") == "script":
        return None
    path = Path(str(entry.get("path") or ""))
    suffix = path.suffix.lower()
    mime = _normalized_mime(str(entry.get("mime") or ""))
    if suffix == ".docx" or mime == _DOCX_MIME:
        return "docx"
    if suffix == ".xlsx" or mime == _XLSX_MIME:
        return "xlsx"
    if suffix in IMAGE_SUFFIXES - {".svg"} or mime in _IMAGE_MIMES:
        return "image"
    if bool(entry.get("readable")) or suffix in READABLE_SUFFIXES or mime in _TEXT_MIMES:
        return "text"
    return None


def has_readable_skill_resources(
    resources: dict[str, ResolvedSkill],
    *,
    image_supported: bool,
) -> bool:
    return any(
        kind is not None and (kind != "image" or image_supported)
        for item in resources.values()
        for entry in item.resource_index
        for kind in (_skill_format(entry),)
    )


def _has_readable_attachment(ctx: RunContext[AgentDeps]) -> bool:
    image_supported = "image" in set(ctx.deps.input_modalities)
    return any(
        kind is not None and (kind != "image" or image_supported)
        for ref in ctx.deps.attachment_by_id.values()
        for kind in (_attachment_format(ref),)
    )


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


async def _load_cached(
    ctx: RunContext[AgentDeps],
    *,
    key: str,
    loader: Callable[[], Awaitable[bytes]],
    failure_label: str,
    max_bytes: int | None = None,
    expected_hash: str | None = None,
) -> bytes:
    cached = ctx.deps.file_bytes.get(key)
    if cached is not None:
        return cached
    task = ctx.deps.file_load_tasks.get(key)
    if task is None:
        task = asyncio.create_task(loader(), name=f"file-load:{key}")
        ctx.deps.file_load_tasks[key] = task
    try:
        data = await task
    except asyncio.CancelledError:
        raise
    except AttachmentDownloadError as exc:
        raise ToolFailed(f"{failure_label}下载失败: {exc}") from exc
    except OSError as exc:
        raise ToolFailed(f"{failure_label}读取失败: {type(exc).__name__}") from exc
    except Exception as exc:
        raise ToolFailed(f"{failure_label}读取失败: {type(exc).__name__}") from exc
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
        task = asyncio.create_task(asyncio.to_thread(parser), name=f"file-parse:{key}")
        ctx.deps.file_parse_tasks[key] = task
    try:
        parsed = await task
    except asyncio.CancelledError:
        raise
    except AttachmentParseError as exc:
        raise ToolFailed(str(exc)) from exc
    except Exception as exc:
        raise ToolFailed(f"{failure_label}解析失败: {type(exc).__name__}: {exc}") from exc
    finally:
        ctx.deps.file_parse_tasks.pop(key, None)
    ctx.deps.file_parsed[key] = parsed
    return parsed


def _safe_skill_file(
    resources: dict[str, ResolvedSkill],
    ref: SkillFileRef,
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
        raise ModelRetry("Skill脚本不能通过 read_file 读取；需要时使用 execute_skill_script")
    target = (item.package_root / ref.path).resolve()
    root = item.package_root.resolve()
    if target != root and root not in target.parents:
        raise ModelRetry("Skill资源路径超出已安装包")
    if not target.is_file():
        raise ToolFailed(f"Skill资源文件缺失: {ref.path}")
    return item, entry, target


async def _resolve_file(
    ctx: RunContext[AgentDeps],
    resource: FileResourceRef | dict[str, Any],
    resources: dict[str, ResolvedSkill],
) -> _ResolvedFile:
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
        kind = _attachment_format(ref)
        if kind is None:
            raise ModelRetry(
                f"read_file 不支持 MIME 为 {ref.mime or 'unknown'} 的附件；PDF、音频和视频使用模型原生输入"
            )

        async def load_attachment() -> bytes:
            return await _download_file(ref.id, ctx.deps.request_headers)

        return _ResolvedFile(
            source="attachment",
            resource=resource,
            name=ref.name or ref.id,
            mime=ref.mime,
            format=kind,
            cache_key=f"attachment:{ref.id}",
            content_hash=None,
            load=load_attachment,
        )

    _item, entry, target = _safe_skill_file(resources, resource)
    kind = _skill_format(entry)
    if kind is None:
        raise ModelRetry(f"read_file 暂不支持 Skill资源 {resource.path!r} 的文件类型")

    async def load_skill_file() -> bytes:
        return await asyncio.to_thread(target.read_bytes)

    content_hash = str(entry.get("content_hash") or "") or None
    return _ResolvedFile(
        source="skill",
        resource=resource,
        name=Path(resource.path).name,
        mime=str(entry.get("mime") or "") or None,
        format=kind,
        cache_key=f"skill:{resource.skill_code}:{resource.path}:{content_hash or ''}",
        content_hash=content_hash,
        load=load_skill_file,
    )


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
        limit=limit,
        returned_count=returned_count,
        total_count=total_count,
        next_offset=next_offset,
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
) -> FileReadResult | ToolReturn:
    if resolved.format == "text":
        if sheet is not None:
            raise ModelRetry("sheet 仅适用于 XLSX 文件")
        start, page_limit = _normalized_range(
            offset=offset,
            limit=limit,
            default_limit=_TEXT_DEFAULT_LIMIT,
            max_limit=_TEXT_MAX_LIMIT,
        )
        lines, metadata = await _parse_cached(
            ctx,
            key=f"{resolved.cache_key}:text",
            parser=lambda: text_document(data, name=resolved.name, mime=resolved.mime),
            failure_label=resolved.name,
        )
        page = paginate_items(
            lines[start - 1 : start - 1 + page_limit],
            total_count=len(lines),
            offset=start,
            limit=page_limit,
        )
        return _result(
            resolved,
            unit="line",
            content=page.content,
            metadata=metadata,
            offset=start,
            limit=page_limit,
            returned_count=page.returned_count,
            total_count=len(lines),
            next_offset=page.next_offset,
            has_more=page.has_more,
            truncated_by=page.truncated_by,
            content_hash=content_hash,
        )

    if resolved.format == "docx":
        if sheet is not None:
            raise ModelRetry("sheet 仅适用于 XLSX 文件")
        start, page_limit = _normalized_range(
            offset=offset,
            limit=limit,
            default_limit=_OFFICE_DEFAULT_LIMIT,
            max_limit=_OFFICE_MAX_LIMIT,
        )
        document: DocxDocument = await _parse_cached(
            ctx,
            key=f"{resolved.cache_key}:docx",
            parser=lambda: docx_document(data),
            failure_label=resolved.name,
        )
        include_images = (
            start == 1
            and "image" in set(ctx.deps.input_modalities)
            and bool(document.images)
        )
        page = paginate_items(
            document.blocks[start - 1 : start - 1 + page_limit],
            total_count=len(document.blocks),
            offset=start,
            limit=page_limit,
        )
        result = _result(
            resolved,
            unit="block",
            content=page.content,
            metadata={
                **document.metadata,
                "embedded_images_included": include_images,
                "embedded_images_note": (
                    "图片按文档级集合返回，不代表它们在正文中的准确位置"
                    if document.images
                    else None
                ),
            },
            offset=start,
            limit=page_limit,
            returned_count=page.returned_count,
            total_count=len(document.blocks),
            next_offset=page.next_offset,
            has_more=page.has_more,
            truncated_by=page.truncated_by,
            content_hash=content_hash,
        )
        if not include_images:
            return result
        multimodal: list[Any] = [
            f"以下是 {resolved.name} 中的文档级内嵌图片集合，共 {len(document.images)} 张："
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
            multimodal.extend(
                [
                    f"内嵌图片 {index}（{name}）:",
                    BinaryContent(data=processed, media_type=output_mime),
                ]
            )
        return ToolReturn(return_value=result, content=multimodal) if len(multimodal) > 1 else result

    start, page_limit = _normalized_range(
        offset=offset,
        limit=limit,
        default_limit=_OFFICE_DEFAULT_LIMIT,
        max_limit=_OFFICE_MAX_LIMIT,
    )
    workbook: XlsxWorkbook = await _parse_cached(
        ctx,
        key=f"{resolved.cache_key}:xlsx",
        parser=lambda: xlsx_workbook(data),
        failure_label=resolved.name,
    )
    try:
        selected = select_xlsx_sheet(workbook, sheet)
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc
    page = paginate_items(
        selected.rows[start - 1 : start - 1 + page_limit],
        total_count=len(selected.rows),
        offset=start,
        limit=page_limit,
    )
    return _result(
        resolved,
        unit="row",
        content=page.content,
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
                for item in workbook.sheets
            ],
            "formula_mode": "cached_values_only",
        },
        offset=start,
        limit=page_limit,
        returned_count=page.returned_count,
        total_count=len(selected.rows),
        next_offset=page.next_offset,
        has_more=page.has_more,
        truncated_by=page.truncated_by,
        content_hash=content_hash,
    )


async def _read_image(
    ctx: RunContext[AgentDeps],
    resolved: _ResolvedFile,
    *,
    data: bytes,
    offset: int | None,
    limit: int | None,
    sheet: str | None,
    content_hash: str,
) -> ToolReturn:
    if "image" not in set(ctx.deps.input_modalities):
        raise ModelRetry("当前模型不支持图片输入，无法读取该图片")
    if offset not in (None, 1) or limit not in (None, 1) or sheet is not None:
        raise ModelRetry("图片不支持分页或 sheet 参数")
    detected = image_mime(data, resolved.name)
    if detected == "application/octet-stream":
        raise ToolFailed(f"图片格式无法识别: {resolved.name}")
    try:
        processed, output_mime, output_name = await _parse_cached(
            ctx,
            key=f"{resolved.cache_key}:image",
            parser=lambda: process_image(data, filename=resolved.name, mime=detected),
            failure_label=resolved.name,
        )
    except ToolFailed:
        raise
    result = _result(
        resolved,
        unit="image",
        content="",
        metadata={"output_name": output_name, "output_mime": output_mime},
        offset=1,
        limit=1,
        returned_count=1,
        total_count=1,
        next_offset=None,
        has_more=False,
        truncated_by=None,
        content_hash=content_hash,
    )
    return ToolReturn(
        return_value=result,
        content=[f"文件 {resolved.name} 的图片内容：", BinaryContent(data=processed, media_type=output_mime)],
    )


def build_file_toolset(
    resources: dict[str, ResolvedSkill] | None = None,
) -> PreparedToolset[AgentDeps]:
    authorized = dict(resources or {})
    toolset = FunctionToolset[AgentDeps](
        id="file-resources",
        instructions=(
            "read_file统一读取当前会话附件和已授权Skill文件。先使用来源提供的精确file_id，"
            "或先调用list_skill_resources取得Skill路径。首次通常只传resource；若has_more=true，"
            "使用next_offset继续读取。不要猜测file_id、Skill编码或路径，脚本不能作为文件正文读取。"
        ),
    )

    @toolset.tool
    async def read_file(
        ctx: RunContext[AgentDeps],
        resource: FileResourceRef,
        offset: int | None = None,
        limit: int | None = None,
        sheet: str | None = None,
    ) -> FileReadResult | ToolReturn:
        """按需读取当前会话附件或已授权 Skill 文件；有下一页时使用 next_offset 继续。"""
        resolved = await _resolve_file(ctx, resource, authorized)
        data = await _load_cached(
            ctx,
            key=resolved.cache_key,
            loader=resolved.load,
            failure_label=resolved.name,
            max_bytes=max_upload_bytes() if resolved.source == "attachment" else None,
            expected_hash=resolved.content_hash,
        )
        content_hash = resolved.content_hash or hashlib.sha256(data).hexdigest()
        if resolved.format == "image":
            return await _read_image(
                ctx,
                resolved,
                data=data,
                offset=offset,
                limit=limit,
                sheet=sheet,
                content_hash=content_hash,
            )
        return await _read_paged(
            ctx,
            resolved,
            data=data,
            offset=offset,
            limit=limit,
            sheet=sheet,
            content_hash=content_hash,
        )

    async def prepare(ctx: RunContext[AgentDeps], tools: dict[str, Any]) -> dict[str, Any]:
        image_supported = "image" in set(ctx.deps.input_modalities)
        if _has_readable_attachment(ctx) or has_readable_skill_resources(
            authorized,
            image_supported=image_supported,
        ):
            return tools
        return {}

    return PreparedToolset(toolset, prepare)
