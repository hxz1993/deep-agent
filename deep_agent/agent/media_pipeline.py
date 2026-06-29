from __future__ import annotations

import io
from dataclasses import dataclass

from deep_agent.options import OPTION

_DEFAULT_ALLOWED_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)


class MediaPipelineError(ValueError):
    """Invalid or unsupported upload for the media pipeline."""


@dataclass(frozen=True)
class ProcessedUpload:
    data: bytes
    filename: str
    mime: str
    extracted_text: str | None = None


def allowed_mimes() -> frozenset[str]:
    raw = OPTION.agent.upload_allowed_mimes
    if not raw:
        return _DEFAULT_ALLOWED_MIMES
    return frozenset(part.strip().lower() for part in str(raw).split(",") if part.strip())


def max_upload_bytes() -> int:
    return int(OPTION.agent.upload_max_size_mb) * 1024 * 1024


def validate_upload(*, data: bytes, mime: str, filename: str) -> None:
    if not data:
        raise MediaPipelineError("Empty file.")
    if len(data) > max_upload_bytes():
        raise MediaPipelineError(f"File exceeds {OPTION.agent.upload_max_size_mb}MB limit.")
    normalized = mime.split(";", 1)[0].strip().lower()
    if normalized not in allowed_mimes():
        raise MediaPipelineError(f"Unsupported MIME type: {mime}")


def process_upload(*, data: bytes, mime: str, filename: str) -> ProcessedUpload:
    validate_upload(data=data, mime=mime, filename=filename)
    normalized = mime.split(";", 1)[0].strip().lower()
    if normalized.startswith("image/"):
        processed, out_mime, out_name = _process_image(data, filename=filename, mime=normalized)
        return ProcessedUpload(data=processed, filename=out_name, mime=out_mime)
    extracted = _extract_document_text(data, mime=normalized, filename=filename)
    return ProcessedUpload(
        data=data,
        filename=filename,
        mime=normalized,
        extracted_text=extracted,
    )


def _process_image(data: bytes, *, filename: str, mime: str) -> tuple[bytes, str, str]:
    from PIL import Image

    max_dim = int(OPTION.agent.image_max_dimension)
    max_bytes = int(OPTION.agent.image_max_bytes)
    quality = int(OPTION.agent.image_jpeg_quality)

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGBA" if mime in ("image/png", "image/gif", "image/webp") else "RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > max_dim:
            scale = max_dim / longest
            img = img.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)

        has_alpha = img.mode == "RGBA" and _has_visible_alpha(img)
        out = io.BytesIO()
        if has_alpha:
            img.save(out, format="PNG", optimize=True)
            out_mime = "image/png"
            out_name = _swap_ext(filename, ".png")
        else:
            rgb = img.convert("RGB")
            rgb.save(out, format="JPEG", quality=quality, optimize=True)
            out_mime = "image/jpeg"
            out_name = _swap_ext(filename, ".jpg")

    processed = out.getvalue()
    if len(processed) > max_bytes:
        raise MediaPipelineError(
            f"Image still exceeds {max_bytes} bytes after compression; use a smaller file."
        )
    return processed, out_mime, out_name


def _has_visible_alpha(img) -> bool:
    if img.mode != "RGBA":
        return False
    alpha = img.getchannel("A")
    return alpha.getextrema()[0] < 255


def _swap_ext(filename: str, ext: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[0] + ext
    return filename + ext


def _extract_document_text(data: bytes, *, mime: str, filename: str) -> str | None:
    if mime.startswith("image/"):
        return None
    limit = int(OPTION.agent.upload_extract_text_max_chars)
    prefix = f"[附件: {filename}]\n"

    if mime in ("text/plain", "text/csv"):
        text = data.decode("utf-8", errors="replace")
    elif mime == "application/pdf":
        text = _extract_pdf_text(data)
    elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        text = _extract_docx_text(data)
    elif mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        text = _extract_xlsx_text(data)
    else:
        return None

    text = text.strip()
    if not text:
        return prefix + "(未能提取文本内容)"
    body = text[:limit]
    return prefix + body


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def _extract_xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = wb.active
    rows: list[str] = []
    for idx, row in enumerate(sheet.iter_rows(values_only=True)):
        if idx >= 500:
            break
        cells = ["" if cell is None else str(cell) for cell in row]
        if any(cells):
            rows.append(",".join(cells))
    wb.close()
    return "\n".join(rows)
