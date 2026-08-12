from __future__ import annotations

from dataclasses import dataclass

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    process_image,
    validate_office_archive,
)
from deep_agent.options import OPTION

_DEFAULT_ALLOWED_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/html",
        "text/xml",
        "application/xml",
        "application/json",
        "text/json",
        "audio/mpeg",
        "audio/wav",
        "video/mp4",
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
    if normalized in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        try:
            validate_office_archive(data)
        except AttachmentParseError as exc:
            raise MediaPipelineError(str(exc)) from exc


def process_upload(*, data: bytes, mime: str, filename: str) -> ProcessedUpload:
    validate_upload(data=data, mime=mime, filename=filename)
    normalized = mime.split(";", 1)[0].strip().lower()
    if normalized.startswith("image/"):
        try:
            processed, out_mime, out_name = process_image(
                data,
                filename=filename,
                mime=normalized,
            )
        except AttachmentParseError as exc:
            raise MediaPipelineError(str(exc)) from exc
        return ProcessedUpload(data=processed, filename=out_name, mime=out_mime)
    return ProcessedUpload(
        data=data,
        filename=filename,
        mime=normalized,
        extracted_text=None,
    )
