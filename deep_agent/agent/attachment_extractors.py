from __future__ import annotations

import hashlib
import io
import json
import posixpath
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Iterable

from deep_agent.options import OPTION

MAX_TOOL_OUTPUT_CHARS = 50_000
MAX_OFFICE_ARCHIVE_MEMBERS = 10_000
MAX_OFFICE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_OFFICE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 200
MAX_XLSX_CELLS = 2_000_000
MAX_CELL_CHARS = 4_000
MAX_DOCX_INLINE_IMAGES = 5


class AttachmentParseError(ValueError):
    """Uploaded attachment cannot be parsed safely."""


@dataclass(frozen=True)
class DocumentPage:
    content: str
    returned_count: int
    has_more: bool
    next_offset: int | None
    truncated_by: str | None


@dataclass(frozen=True)
class DocxDocument:
    blocks: tuple[str, ...]
    metadata: dict[str, Any]
    images: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class XlsxSheet:
    name: str
    state: str
    rows: tuple[str, ...]
    columns: int
    headers: tuple[str, ...]


@dataclass(frozen=True)
class XlsxWorkbook:
    sheets: tuple[XlsxSheet, ...]


def normalize_range(
    *,
    offset: int | None,
    limit: int | None,
    default_limit: int,
    max_limit: int,
) -> tuple[int, int]:
    resolved_offset = 1 if offset is None else int(offset)
    resolved_limit = default_limit if limit is None else int(limit)
    if resolved_offset < 1:
        raise AttachmentParseError("offset 必须是正整数（从 1 开始）")
    if resolved_limit < 1:
        raise AttachmentParseError("limit 必须是正整数")
    return resolved_offset, min(resolved_limit, max_limit)


def paginate_items(
    items: Iterable[str],
    *,
    total_count: int,
    offset: int,
    limit: int,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> DocumentPage:
    selected = list(items)
    output: list[str] = []
    used = 0
    truncated_by: str | None = None

    for item in selected[:limit]:
        separator = 1 if output else 0
        available = max_chars - used - separator
        if available <= 0:
            truncated_by = "output_size"
            break
        if len(item) > available:
            suffix = "\n…（该项内容过长，已截断）"
            keep = max(0, available - len(suffix))
            output.append(item[:keep] + suffix)
            used = max_chars
            truncated_by = "item_size"
            break
        output.append(item)
        used += separator + len(item)

    returned_count = len(output)
    next_offset = offset + returned_count
    has_more = next_offset <= total_count
    if truncated_by is None and returned_count >= limit and has_more:
        truncated_by = "limit"
    return DocumentPage(
        content="\n".join(output),
        returned_count=returned_count,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
        truncated_by=truncated_by,
    )


def decode_text_document(
    data: bytes,
    *,
    name: str | None = None,
    mime: str | None = None,
) -> tuple[str, str]:
    """Decode common text encodings and pretty-print explicit JSON documents."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
        encoding = "utf-16"
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig")
        encoding = "utf-8-sig"
    else:
        for candidate in ("utf-8", "gb18030"):
            try:
                text = data.decode(candidate)
                encoding = candidate
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="replace")
            encoding = "utf-8-replacement"

    if _is_json_document(name=name, mime=mime):
        text = _pretty_json_or_raw(text)
    return text, encoding


def text_document(
    data: bytes,
    *,
    name: str | None = None,
    mime: str | None = None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    text, encoding = decode_text_document(data, name=name, mime=mime)
    lines = tuple(text.splitlines())
    metadata: dict[str, Any] = {
        "encoding": encoding,
        "total_lines": len(lines),
    }
    if _is_json_document(name=name, mime=mime):
        metadata["format"] = "json"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            metadata["json_parse_ok"] = False
        else:
            metadata["json_parse_ok"] = True
            metadata["json_type"] = (
                "array"
                if isinstance(parsed, list)
                else "object"
                if isinstance(parsed, dict)
                else type(parsed).__name__
            )
    return lines, metadata


def validate_office_archive(data: bytes) -> None:
    """Reject malformed/encrypted/path-traversal/zip-bomb Office archives."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_OFFICE_ARCHIVE_MEMBERS:
                raise AttachmentParseError("Office 文件包含过多压缩成员")
            expanded = 0
            for info in members:
                normalized = posixpath.normpath(info.filename.replace("\\", "/"))
                if (
                    normalized.startswith("../")
                    or normalized == ".."
                    or normalized.startswith("/")
                    or ":" in normalized.split("/", 1)[0]
                ):
                    raise AttachmentParseError("Office 文件包含不安全的压缩路径")
                if info.flag_bits & 0x1:
                    raise AttachmentParseError("暂不支持加密的 Office 文件")
                if info.file_size > MAX_OFFICE_MEMBER_BYTES:
                    raise AttachmentParseError("Office 文件中的单个成员过大")
                expanded += info.file_size
                if expanded > MAX_OFFICE_EXPANDED_BYTES:
                    raise AttachmentParseError("Office 文件解压后体积过大")
                if info.file_size and info.compress_size == 0:
                    raise AttachmentParseError("Office 文件压缩信息异常")
                if info.compress_size:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_OFFICE_COMPRESSION_RATIO:
                        raise AttachmentParseError("Office 文件压缩比异常")
    except zipfile.BadZipFile as exc:
        raise AttachmentParseError("Office 文件结构损坏") from exc


def docx_document(data: bytes) -> DocxDocument:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    validate_office_archive(data)
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise AttachmentParseError(f"DOCX 解析失败: {exc}") from exc

    blocks: list[str] = []
    header_seen: set[str] = set()
    footer_seen: set[str] = set()

    for section_index, section in enumerate(document.sections, start=1):
        for label, container in (
            ("页眉", section.header),
            ("首页页眉", section.first_page_header),
            ("偶数页页眉", section.even_page_header),
        ):
            _append_container_blocks(
                blocks,
                container,
                label=f"[第 {section_index} 节{label}]",
                seen=header_seen,
            )

    body_paragraphs = 0
    body_tables = 0
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if text:
                body_paragraphs += 1
                blocks.append(text)
        elif isinstance(item, Table):
            table_blocks = _table_blocks(item, label=f"[正文表格 {body_tables + 1}]")
            if table_blocks:
                body_tables += 1
                blocks.extend(table_blocks)

    for section_index, section in enumerate(document.sections, start=1):
        for label, container in (
            ("页脚", section.footer),
            ("首页页脚", section.first_page_footer),
            ("偶数页页脚", section.even_page_footer),
        ):
            _append_container_blocks(
                blocks,
                container,
                label=f"[第 {section_index} 节{label}]",
                seen=footer_seen,
            )

    comment_count = 0
    comments = getattr(document, "comments", ())
    for comment in comments:
        text = (getattr(comment, "text", "") or "").strip()
        if not text:
            continue
        comment_count += 1
        comment_id = getattr(comment, "comment_id", comment_count)
        author = (getattr(comment, "author", "") or "未知").strip()
        timestamp = getattr(comment, "timestamp", None)
        timestamp_text = f"，时间 {timestamp.isoformat()}" if timestamp else ""
        blocks.append(
            f"[批注 {comment_id}，作者 {author}{timestamp_text}]\n{text}"
        )

    images = tuple(extract_docx_images(data))
    return DocxDocument(
        blocks=tuple(blocks),
        metadata={
            "total_blocks": len(blocks),
            "sections": len(document.sections),
            "body_paragraphs": body_paragraphs,
            "body_tables": body_tables,
            "comments": comment_count,
            "embedded_image_count": len(images),
            "image_positioning": "document_level_only",
        },
        images=images,
    )


def _append_container_blocks(
    output: list[str],
    container: Any,
    *,
    label: str,
    seen: set[str],
) -> None:
    local: list[str] = []
    for item in container.iter_inner_content():
        text = ""
        if hasattr(item, "rows"):
            table_rows = _table_blocks(item, label="")
            text = "\n".join(table_rows)
        else:
            text = (getattr(item, "text", "") or "").strip()
        if text:
            local.append(text)
    if not local:
        return
    body = "\n".join(local)
    fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    output.append(f"{label}\n{body}")


def _table_blocks(table: Any, *, label: str) -> list[str]:
    rows: list[str] = []
    for row in table.rows:
        values = [
            " ".join((cell.text or "").split()).replace("|", "\\|")
            for cell in row.cells
        ]
        if any(values):
            rows.append("| " + " | ".join(values) + " |")
    if not rows:
        return []
    if label:
        return [label, *rows]
    return rows


def extract_docx_images(data: bytes) -> list[tuple[str, bytes]]:
    validate_office_archive(data)
    images: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(
            info.filename
            for info in archive.infolist()
            if info.filename.startswith("word/media/") and not info.is_dir()
        )
        for name in names:
            if len(images) >= MAX_DOCX_INLINE_IMAGES:
                break
            payload = archive.read(name)
            if _looks_like_raster_image(name, payload):
                images.append((name.rsplit("/", 1)[-1], payload))
    return images


def xlsx_workbook(data: bytes) -> XlsxWorkbook:
    from openpyxl import load_workbook

    validate_office_archive(data)
    try:
        workbook = load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        raise AttachmentParseError(f"XLSX 解析失败: {exc}") from exc

    sheets: list[XlsxSheet] = []
    cell_count = 0
    try:
        for name in workbook.sheetnames:
            worksheet = workbook[name]
            reset_dimensions = getattr(worksheet, "reset_dimensions", None)
            if callable(reset_dimensions):
                reset_dimensions()
            rows: list[str] = []
            headers: tuple[str, ...] = ()
            columns = 0
            for row_index, row in enumerate(worksheet.iter_rows(values_only=True)):
                cell_count += len(row)
                if cell_count > MAX_XLSX_CELLS:
                    raise AttachmentParseError("XLSX 单次解析的单元格数量过多")
                values = tuple(_cell_text(value) for value in row)
                columns = max(columns, len(values))
                if row_index == 0:
                    headers = values
                rows.append(",".join(value.replace("\n", " ") for value in values))
            sheets.append(
                XlsxSheet(
                    name=name,
                    state=str(worksheet.sheet_state),
                    rows=tuple(rows),
                    columns=columns,
                    headers=headers,
                )
            )
    finally:
        workbook.close()
    return XlsxWorkbook(sheets=tuple(sheets))


def select_xlsx_sheet(workbook: XlsxWorkbook, requested: str | None) -> XlsxSheet:
    if requested:
        lowered = requested.casefold()
        for sheet in workbook.sheets:
            if sheet.name.casefold() == lowered:
                return sheet
        available = "、".join(sheet.name for sheet in workbook.sheets) or "无"
        raise AttachmentParseError(
            f"未找到工作表“{requested}”；可用工作表：{available}"
        )
    for sheet in workbook.sheets:
        if sheet.state == "visible":
            return sheet
    if workbook.sheets:
        return workbook.sheets[0]
    raise AttachmentParseError("XLSX 不包含工作表")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    elif isinstance(value, (datetime, date, time)):
        text = value.isoformat()
    else:
        text = str(value)
    if len(text) > MAX_CELL_CHARS:
        return text[: MAX_CELL_CHARS - 1] + "…"
    return text


def process_image(
    data: bytes,
    *,
    filename: str,
    mime: str,
) -> tuple[bytes, str, str]:
    from PIL import Image

    max_dim = int(OPTION.agent.image_max_dimension)
    max_bytes = int(OPTION.agent.image_max_bytes)
    quality = int(OPTION.agent.image_jpeg_quality)

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert(
                "RGBA" if mime in ("image/png", "image/gif", "image/webp") else "RGB"
            )
            width, height = image.size
            longest = max(width, height)
            if longest > max_dim:
                scale = max_dim / longest
                image = image.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.Resampling.LANCZOS,
                )

            has_alpha = (
                image.mode == "RGBA"
                and image.getchannel("A").getextrema()[0] < 255
            )
            output = io.BytesIO()
            if has_alpha:
                image.save(output, format="PNG", optimize=True)
                output_mime = "image/png"
                output_name = _swap_ext(filename, ".png")
            else:
                image.convert("RGB").save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
                output_mime = "image/jpeg"
                output_name = _swap_ext(filename, ".jpg")
    except Exception as exc:
        raise AttachmentParseError(f"图片解析失败: {exc}") from exc

    processed = output.getvalue()
    if len(processed) > max_bytes:
        raise AttachmentParseError(
            f"图片压缩后仍超过 {max_bytes} 字节，请使用更小的文件"
        )
    return processed, output_mime, output_name


def _swap_ext(filename: str, extension: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[0] + extension
    return filename + extension


def _looks_like_raster_image(name: str, data: bytes) -> bool:
    lowered = name.lower()
    if lowered.endswith((".emf", ".wmf", ".svg")):
        return False
    return (
        data.startswith(b"\x89PNG")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def image_mime(data: bytes, name: str = "") -> str:
    lowered = name.lower()
    if data.startswith(b"\x89PNG") or lowered.endswith(".png"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") or lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")) or lowered.endswith(".gif"):
        return "image/gif"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _is_json_document(*, name: str | None, mime: str | None) -> bool:
    normalized = (mime or "").split(";", 1)[0].strip().lower()
    if normalized in {"application/json", "text/json"}:
        return True
    filename = (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    return filename.endswith(".json")


def _pretty_json_or_raw(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, (dict, list)):
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
