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
    text_document,
    xlsx_workbook,
    xlsx_sheet_metadata,
)
from deep_agent.skills.package import IMAGE_SUFFIXES, MAX_MODEL_TEXT_BYTES, READABLE_SUFFIXES, is_env_resource


SkillResourceFormat = Literal["text", "docx", "xlsx"]
SkillResourceUnit = Literal["line", "block", "row"]

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
    char_offset: int = 0
    next_char_offset: int | None = None


def resource_format(
    entry: dict[str, Any], *, console_preview: bool = False,
) -> SkillResourceFormat | None:
    suffix = Path(str(entry.get("path") or "")).suffix.lower()
    mime = str(entry.get("mime") or "").split(";", 1)[0].strip().lower()
    # Console has text/Office readers, but no PDF/PPTX or binary image reader.
    # Legacy package indexes marked these readable for the model's separate
    # read_file pipeline; that flag must not send binary bytes to this text path.
    if console_preview and (
        suffix in IMAGE_SUFFIXES | {".pdf", ".pptx"}
        or mime.startswith("image/")
        or mime in {"application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    ):
        return None
    if entry.get("category") == "script" and not console_preview:
        return None
    if suffix == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    if suffix == ".xlsx" or mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return "xlsx"
    if entry.get("category") == "script":
        if console_preview and (
            suffix == ".py"
            or suffix in READABLE_SUFFIXES
            or mime.startswith("text/")
            or mime in {"application/javascript", "application/x-sh", "application/x-shellscript"}
        ):
            return "text"
        return None
    if (
        bool(entry.get("readable"))
        or suffix in READABLE_SUFFIXES
        or is_env_resource(str(entry.get("path") or ""))
    ):
        return "text"
    return None


def resource_unit(
    entry: dict[str, Any], *, console_preview: bool = False,
) -> SkillResourceUnit | None:
    kind = resource_format(entry, console_preview=console_preview)
    if kind == "text":
        return "line"
    if kind == "docx":
        return "block"
    if kind == "xlsx":
        return "row"
    return None


def present_resource_entry(
    entry: dict[str, Any], *, console_preview: bool = False,
) -> dict[str, Any]:
    kind = resource_format(entry, console_preview=console_preview)
    return {
        **entry,
        "readable": kind is not None,
        "read_format": kind,
        "read_unit": resource_unit(entry, console_preview=console_preview),
    }


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SkillResourceReadError(
            f"Skill resource could not be read: {path.name}",
            correctable=False,
        ) from exc


def _text_page(
    path: Path, *, offset: int | None, limit: int | None, char_offset: int = 0,
) -> SkillResourcePage:
    try:
        resolved_offset, resolved_limit = normalize_range(
            offset=offset,
            limit=limit,
            default_limit=500,
            max_limit=2_000,
        )
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=True) from exc
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SkillResourceReadError(
            f"Skill resource could not be read: {path.name}",
            correctable=False,
        ) from exc
    lines, metadata = text_document(data, name=path.name, mime=None)
    page = paginate_items(
        lines[resolved_offset - 1 : resolved_offset - 1 + resolved_limit],
        total_count=len(lines),
        offset=resolved_offset,
        limit=resolved_limit,
        char_offset=char_offset,
    )
    return SkillResourcePage(
        format="text",
        unit="line",
        content=page.content,
        metadata=metadata,
        offset=resolved_offset,
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=len(lines),
        truncated_by=page.truncated_by,
        char_offset=char_offset,
        next_char_offset=page.next_char_offset,
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


def _docx_page(
    path: Path, *, offset: int | None, limit: int | None, char_offset: int = 0,
) -> SkillResourcePage:
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
        char_offset=char_offset,
    )
    return SkillResourcePage(
        format="docx",
        unit="block",
        content=page.content,
        metadata=document.metadata,
        offset=resolved_offset,
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=len(document.blocks),
        truncated_by=page.truncated_by,
        char_offset=char_offset,
        next_char_offset=page.next_char_offset,
    )


def _xlsx_page(
    path: Path,
    *,
    offset: int | None,
    limit: int | None,
    sheet: str | None,
    char_offset: int = 0,
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
        char_offset=char_offset,
    )
    return SkillResourcePage(
        format="xlsx",
        unit="row",
        content=page.content,
        metadata=xlsx_sheet_metadata(workbook, selected),
        offset=resolved_offset,
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=len(selected.rows),
        truncated_by=page.truncated_by,
        char_offset=char_offset,
        next_char_offset=page.next_char_offset,
    )


def read_resource_page(
    entry: dict[str, Any],
    path: Path,
    *,
    offset: int | None = None,
    limit: int | None = None,
    sheet: str | None = None,
    char_offset: int = 0,
    console_preview: bool = False,
) -> SkillResourcePage:
    kind = resource_format(entry, console_preview=console_preview)
    if kind is None:
        raise SkillResourceReadError(
            f"binary_resource_not_readable: {entry.get('path')!r}",
            correctable=True,
        )
    if sheet is not None and kind != "xlsx":
        raise SkillResourceReadError("sheet is only valid for XLSX resources", correctable=True)
    if console_preview and entry.get("category") == "script" and kind == "text":
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise SkillResourceReadError("Skill script could not be read", correctable=False) from exc
        if size > MAX_MODEL_TEXT_BYTES:
            raise SkillResourceReadError("Skill script is too large to preview; download the Skill package to view it", correctable=True)
    try:
        if kind == "text":
            return _text_page(path, offset=offset, limit=limit, char_offset=char_offset)
        if kind == "docx":
            return _docx_page(path, offset=offset, limit=limit, char_offset=char_offset)
        return _xlsx_page(path, offset=offset, limit=limit, sheet=sheet, char_offset=char_offset)
    except AttachmentParseError as exc:
        raise SkillResourceReadError(str(exc), correctable=True) from exc
