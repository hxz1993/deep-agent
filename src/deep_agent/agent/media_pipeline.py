from __future__ import annotations

from dataclasses import dataclass

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    process_image,
    validate_office_archive,
)
from deep_agent.options import OPTION
from deep_agent.files.upload_settings import UploadSettings, MAX_ATTACHMENT_BYTES

_DEFAULT_ALLOWED_MIMES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/html",
        "text/xml",
        "application/xml",
        "application/json",
        "text/json",
        "audio/mpeg",
        "audio/amr",
        "audio/wav",
        "video/mp4",
    }
)

_GENERIC_LOG_MIMES = frozenset(
    {
        "",
        "application/octet-stream",
        "application/x-log",
        "text/x-log",
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


def allowed_mimes(additional_mimes: frozenset[str] = frozenset()) -> frozenset[str]:
    raw = OPTION.agent.upload_allowed_mimes
    if not raw:
        return _DEFAULT_ALLOWED_MIMES | additional_mimes
    return frozenset(part.strip().lower() for part in str(raw).split(",") if part.strip())


def max_upload_bytes() -> int:
    # Read safety ceiling for already-authorized files, independent of later policy changes.
    return MAX_ATTACHMENT_BYTES


def is_log_text_attachment(*, filename: str, mime: str | None) -> bool:
    normalized = (mime or "").split(";", 1)[0].strip().lower()
    return filename.lower().endswith(".log") and normalized in _GENERIC_LOG_MIMES


def _normalized_upload_mime(*, mime: str, filename: str) -> str:
    normalized = mime.split(";", 1)[0].strip().lower()
    if is_log_text_attachment(filename=filename, mime=normalized):
        return "text/plain"
    return normalized


def validate_upload(*, data: bytes, mime: str, filename: str, additional_mimes: frozenset[str] = frozenset(), upload_settings: UploadSettings = UploadSettings()) -> None:
    if not data:
        raise MediaPipelineError("Empty file.")
    if len(data) > upload_settings.max_bytes:
        raise MediaPipelineError(f"File exceeds {upload_settings.max_size_mb}MB limit.")
    normalized = _normalized_upload_mime(mime=mime, filename=filename)
    if normalized not in allowed_mimes(additional_mimes):
        raise MediaPipelineError(f"Unsupported MIME type: {mime}；当前上传策略不允许此文件类型。")
    if normalized in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        try:
            validate_office_archive(data)
        except AttachmentParseError as exc:
            raise MediaPipelineError(str(exc)) from exc


def process_upload(*, data: bytes, mime: str, filename: str, additional_mimes: frozenset[str] = frozenset(), upload_settings: UploadSettings = UploadSettings()) -> ProcessedUpload:
    validate_upload(data=data, mime=mime, filename=filename, additional_mimes=additional_mimes, upload_settings=upload_settings)
    normalized = _normalized_upload_mime(mime=mime, filename=filename)
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
