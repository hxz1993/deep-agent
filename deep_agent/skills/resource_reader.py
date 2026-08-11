from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    docx_document,
    normalize_range,
    paginate_items,
    select_xlsx_sheet,
    xlsx_workbook,
)
from deep_agent.skills.package import READABLE_SUFFIXES


SkillResourceFormat = Literal["text", "docx", "xlsx"]
SkillResourceUnit = Literal["character", "block", "row"]

MAX_TEXT_PAGE = 32 * 1024
DEFAULT_OFFICE_PAGE = 100
MAX_OFFICE_PAGE = 500


class SkillResourceReadError(ValueError):
    def __init__(self, message: str, *, correctable: bool) -> None:
        self.correctable = correctable
        super().__init__(message)


@dataclass(frozen=True)
class SkillResourcePage:
    format: SkillResourceFormat
    unit: SkillResourceUnit
    content: str
    metadata: dict[str, Any]
    offset: int
    next_offset: int | None
    has_more: bool
    returned_count: int
    total_count: int
    truncated_by: str | None


def resource_format(entry: dict[str, Any]) -> SkillResourceFormat | None:
    if entry.get("category") == "script":
        return None
    suffix = Path(str(entry.get("path") or "")).suffix.lower()
    mime = str(entry.get("mime") or "").split(";", 1)[0].strip().lower()
    if suffix == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    if suffix == ".xlsx" or mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return "xlsx"
    if bool(entry.get("readable")) or suffix in READABLE_SUFFIXES:
        return "text"
    return None


def resource_unit(entry: dict[str, Any]) -> SkillResourceUnit | None:
    kind = resource_format(entry)
    if kind == "text":
        return "character"
    if kind == "docx":
        return "block"
    if kind == "xlsx":
        return "row"
    return None


def present_resource_entry(entry: dict[str, Any]) -> dict[str, Any]:
    kind = resource_format(entry)
    return {
        **entry,
        "readable": kind is not None,
        "read_format": kind,
        "read_unit": resource_unit(entry),
    }


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SkillResourceReadError(
            f"Skill resource could not be read: {path.name}",
            correctable=False,
        ) from exc


def _text_page(path: Path, *, offset: int | None, limit: int | None) -> SkillResourcePage:
    resolved_offset = 0 if offset is None else int(offset)
    resolved_limit = MAX_TEXT_PAGE if limit is None else int(limit)
    if resolved_offset < 0:
        raise SkillResourceReadError("offset must be non-negative for text resources", correctable=True)
    if resolved_limit < 1 or resolved_limit > MAX_TEXT_PAGE:
        raise SkillResourceReadError(
            f"limit must be between 1 and {MAX_TEXT_PAGE} for text resources",
            correctable=True,
        )
    try:
        content = path.read_text("utf-8")
    except UnicodeError as exc:
        raise SkillResourceReadError(
            f"Skill text resource is not valid UTF-8: {path.name}",
            correctable=False,
        ) from exc
    except OSError as exc:
        raise SkillResourceReadError(
            f"Skill resource could not be read: {path.name}",
            correctable=False,
        ) from exc
    page = content[resolved_offset : resolved_offset + resolved_limit]
    next_offset = resolved_offset + len(page)
    has_more = next_offset < len(content)
    return SkillResourcePage(
        format="text",
        unit="character",
        content=page,
        metadata={"encoding": "utf-8", "total_characters": len(content)},
        offset=resolved_offset,
        next_offset=next_offset if has_more else None,
        has_more=has_more,
        returned_count=len(page),
        total_count=len(content),
        truncated_by="limit" if has_more else None,
    )


def _office_range(offset: int | None, limit: int | None) -> tuple[int, int]:
    try:
        return normalize_range(
            offset=offset,
            limit=limit,
            default_limit=DEFAULT_OFFICE_PAGE,
            max_limit=MAX_OFFICE_PAGE,
        )
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=True) from exc


def _docx_page(path: Path, *, offset: int | None, limit: int | None) -> SkillResourcePage:
    resolved_offset, resolved_limit = _office_range(offset, limit)
    try:
        document = docx_document(_read_bytes(path))
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=False) from exc
    page = paginate_items(
        document.blocks[resolved_offset - 1 : resolved_offset - 1 + resolved_limit],
        total_count=len(document.blocks),
        offset=resolved_offset,
        limit=resolved_limit,
    )
    return SkillResourcePage(
        format="docx",
        unit="block",
        content=page.content,
        metadata={
            **document.metadata,
            "embedded_images_included": False,
            "embedded_images_note": (
                "Skill DOCX 读取当前只返回文本块，不把内嵌图片注入模型上下文"
                if document.images
                else None
            ),
        },
        offset=resolved_offset,
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=len(document.blocks),
        truncated_by=page.truncated_by,
    )


def _xlsx_page(
    path: Path,
    *,
    offset: int | None,
    limit: int | None,
    sheet: str | None,
) -> SkillResourcePage:
    resolved_offset, resolved_limit = _office_range(offset, limit)
    try:
        workbook = xlsx_workbook(_read_bytes(path))
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=False) from exc
    try:
        selected = select_xlsx_sheet(workbook, sheet)
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=True) from exc
    page = paginate_items(
        selected.rows[resolved_offset - 1 : resolved_offset - 1 + resolved_limit],
        total_count=len(selected.rows),
        offset=resolved_offset,
        limit=resolved_limit,
    )
    return SkillResourcePage(
        format="xlsx",
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
        offset=resolved_offset,
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=len(selected.rows),
        truncated_by=page.truncated_by,
    )


def read_resource_page(
    entry: dict[str, Any],
    path: Path,
    *,
    offset: int | None = None,
    limit: int | None = None,
    sheet: str | None = None,
) -> SkillResourcePage:
    kind = resource_format(entry)
    if kind is None:
        raise SkillResourceReadError(
            f"binary_resource_not_readable: {entry.get('path')!r}",
            correctable=True,
        )
    if sheet is not None and kind != "xlsx":
        raise SkillResourceReadError("sheet is only valid for XLSX resources", correctable=True)
    if kind == "text":
        return _text_page(path, offset=offset, limit=limit)
    if kind == "docx":
        return _docx_page(path, offset=offset, limit=limit)
    return _xlsx_page(path, offset=offset, limit=limit, sheet=sheet)
