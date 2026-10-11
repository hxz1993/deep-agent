"""Portable evidence plus supplier coordinate-plane images, never reconstructed images."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import warnings
import zipfile
from typing import Any

import httpx2
from PIL import Image, UnidentifiedImageError

from deep_agent.agent.media_pipeline import max_upload_bytes
from . import providers
from .models import DocumentParsingError, ParsedDocument

IMAGE_TIMEOUT_SECONDS = 180.0
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000
MAX_BUNDLE_PAGES = 1000
_JSON_CHUNK = 16_384
_IMAGE_SOURCES = {
    ("paddle", "paddle_preprocessed_images"): ("processed_page", False, "paddle_processed_page_pixels"),
    ("glm", "glm_layout_visualization"): ("layout_visualization", True, "glm_page_pixels"),
}


def _too_large() -> DocumentParsingError:
    return DocumentParsingError("bundle_too_large", "证据包或未压缩JSON超过当前文件上传大小上限；解析正文仍可读取。")


def _json_chunks(value: Any):
    # iterencode alone can allocate an entire giant escaped string. Split string
    # leaves first, while delegating exact escaping to the standard JSON encoder.
    if isinstance(value, str):
        yield b'"'
        for start in range(0, len(value), _JSON_CHUNK):
            yield json.dumps(value[start:start + _JSON_CHUNK], ensure_ascii=False)[1:-1].encode("utf-8")
        yield b'"'
    elif isinstance(value, dict):
        yield b"{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield b","
            if not isinstance(key, str):
                raise ValueError("Evidence keys must be strings")
            yield from _json_chunks(key)
            yield b":"
            yield from _json_chunks(item)
        yield b"}"
    elif isinstance(value, (list, tuple)):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _json_chunks(item)
        yield b"]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _json_size(value: dict, maximum: int) -> int:
    total = 0
    for chunk in _json_chunks(value):
        total += len(chunk)
        if total > maximum:
            raise _too_large()
    return total


def _inspect_image(data: bytes) -> tuple[str, str, int, int, bool]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise DocumentParsingError("unsupported_image_format", "处理页图片格式不受支持。")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise DocumentParsingError("image_pixels_exceeded", "处理页图片像素超过安全限制。")
                if getattr(image, "n_frames", 1) != 1:
                    raise DocumentParsingError("animated_image", "处理页图片包含多个帧，无法确认坐标平面。")
                orientation = image.getexif().get(274, 1)
                image_format = image.format
            with Image.open(io.BytesIO(data)) as verification:
                verification.verify()
            # Verify JPEG compressed pixels too, not just its header. Decode is
            # bounded by the pixel limit and always runs outside the event loop.
            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
        extension, mime = ("jpg", "image/jpeg") if image_format == "JPEG" else ("png", "image/png")
        return extension, mime, width, height, orientation == 1
    except DocumentParsingError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise DocumentParsingError("image_pixels_exceeded", "处理页图片像素超过安全限制。") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise DocumentParsingError("invalid_image", "处理页图片内容损坏或并非支持的图片。") from exc


class _BoundedBuffer(io.BytesIO):
    def __init__(self, maximum: int):
        super().__init__()
        self.maximum = maximum

    def write(self, data):
        if self.tell() + len(data) > self.maximum:
            raise _too_large()
        return super().write(data)


def _zip(value: dict, images: list[tuple[str, bytes]], maximum: int) -> bytes:
    json_size = _json_size(value, maximum)
    if json_size + sum(len(data) for _, data in images) > maximum:
        raise _too_large()
    buffer = _BoundedBuffer(maximum)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo("evidence.json")
        info.compress_type = zipfile.ZIP_DEFLATED
        size = 0
        with archive.open(info, "w") as output:
            for chunk in _json_chunks(value):
                size += len(chunk)
                if size > maximum:
                    raise _too_large()
                output.write(chunk)
        for path, data in images:
            archive.writestr(zipfile.ZipInfo(path), data)
    return buffer.getvalue()


def _image_result(code: str, *, provided: bool = True) -> dict:
    return {"status": "failed" if provided else "not_provided", "reason_code": code,
            "coordinate_mapping": "unverified"}


async def _build_evidence_bundle(
    value: dict, parsed: ParsedDocument, *, context: dict | None = None, client=None,
) -> tuple[bytes, int, int]:
    """Create one bounded ZIP; image failures remain explicit in its JSON.

    ``value`` must be DocumentEvidence.export_dict's safe projection. Private
    native responses never enter the ZIP. Page pairing is by evidence ordinal,
    never physical page number (which may be unknown or repeated).
    """
    maximum = max_upload_bytes()
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list) or len(raw_pages) != len(parsed.pages):
        raise DocumentParsingError("bundle_page_mismatch", "证据页面与解析缓存不一致，未导出。")
    if len(raw_pages) > MAX_BUNDLE_PAGES:
        raise DocumentParsingError("bundle_page_limit", "证据包超过1000页安全条目上限；解析正文仍可读取。")
    baseline_size = await providers._cpu(_json_size, value, maximum)
    # Reserve manifest growth and ZIP directory overhead before fetching images.
    image_budget = max(0, maximum - baseline_size - len(raw_pages) * 1024 - 4096)
    payload = {**value, "pages": [dict(page) for page in raw_pages]}
    images: list[tuple[str, bytes]] = []
    spent = 0
    deadline = time.monotonic() + IMAGE_TIMEOUT_SECONDS
    missing: list[int] = []

    async def collect(http):
        nonlocal spent
        for ordinal, (page, native_page) in enumerate(zip(payload["pages"], parsed.pages), 1):
            started = time.monotonic()
            source = _IMAGE_SOURCES.get((parsed.provider, native_page.native.get("processed_image_source")))
            source_fields = {"role": source[0], "annotated": source[1]} if source else {}

            def log_image():
                info = page.get("image", {})
                providers._log(context, "document_image_download", provider_name=parsed.provider,
                               page_ordinal=ordinal, status=info.get("status", "cancelled"),
                               reason_code=info.get("reason_code"), byte_size=info.get("byte_size", 0),
                               elapsed_ms=int((time.monotonic() - started) * 1000))
            url = native_page.native.get("processed_image_url")
            if not isinstance(url, str) or not url:
                page["image"] = {**_image_result("not_provided", provided=False), **source_fields}
                missing.append(ordinal)
                log_image()
                continue
            if source is None:
                page["image"] = {**_image_result("unsupported_image_source"), **source_fields}
                missing.append(ordinal)
                log_image()
                continue
            remaining = deadline - time.monotonic()
            capacity = min(MAX_IMAGE_BYTES, image_budget - spent)
            if remaining <= 0 or capacity <= 0:
                page["image"] = {**_image_result("download_timeout" if remaining <= 0 else "download_budget_exceeded"), **source_fields}
                missing.append(ordinal)
                log_image()
                continue
            try:
                async with asyncio.timeout(remaining):
                    data = await providers._request(http, "GET", url, provider=parsed.provider,
                                                    context=context, signed=True, max_bytes=capacity)
                    # Bounded provider reads already enforce capacity; keep this
                    # check for injected clients and future transport changes.
                    if len(data) > capacity:
                        raise DocumentParsingError("result_too_large", "图片超过下载限制。")
                    spent += len(data)
                    extension, mime, width, height, oriented = await providers._cpu(_inspect_image, data)
                path = f"pages/page-{ordinal:04d}.{extension}"
                mapping, reason = "unverified", "coordinate_space_unverified"
                if not oriented:
                    reason = "exif_orientation"
                elif page.get("coordinate_space") == source[2]:
                    expected_width, expected_height = page.get("width"), page.get("height")
                    known = (type(expected_width) in (int, float) and expected_width > 0
                             and type(expected_height) in (int, float) and expected_height > 0)
                    if known:
                        mapping = "matched" if (width, height) == (expected_width, expected_height) else "dimension_mismatch"
                        reason = "dimension_mismatch" if mapping != "matched" else None
                    else:
                        reason = "page_dimensions_unknown"
                page["image"] = {**source_fields, "status": "saved", "path": path, "mime": mime,
                                 "byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                 "width": width, "height": height, "coordinate_mapping": mapping}
                if reason:
                    page["image"]["reason_code"] = reason
                images.append((path, data))
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                page["image"] = {**_image_result("download_timeout"), **source_fields}
                missing.append(ordinal)
            except DocumentParsingError as exc:
                # Bounded fixed taxonomy; upstream error strings never exported.
                allowed = {"unsafe_url", "artifact_access_denied", "result_too_large", "rate_limited",
                           "network_error", "service_error", "permission_denied", "invalid_image",
                           "image_pixels_exceeded", "unsupported_image_format", "animated_image"}
                code = exc.code if exc.code in allowed else "image_download_failed"
                page["image"] = {**_image_result(code), **source_fields}
                missing.append(ordinal)
                if code == "result_too_large":
                    # The transport discarded up to this request's capacity.
                    spent += capacity
            except Exception:
                page["image"] = {**_image_result("image_download_failed"), **source_fields}
                missing.append(ordinal)
            finally:
                log_image()

    urls_present = any(
        isinstance(page.native.get("processed_image_url"), str) and page.native["processed_image_url"]
        and (parsed.provider, page.native.get("processed_image_source")) in _IMAGE_SOURCES
        for page in parsed.pages
    )
    if client is not None or not urls_present:
        await collect(client)
    else:
        # No provider Key, platform credentials or shared authenticated cookies.
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(30, connect=15), follow_redirects=False) as owned:
            await collect(owned)
    payload["images"] = {"status": "complete" if images and not missing else "partial" if images else "unavailable",
                         "expected": len(raw_pages), "saved": len(images), "missing_page_ordinals": missing}
    data = await providers._cpu(_zip, payload, images, maximum)
    return data, len(images), len(missing)


async def build_evidence_bundle(
    value: dict, parsed: ParsedDocument, *, context: dict | None = None, client=None,
) -> bytes:
    """Export safe evidence and available native coordinate-plane images as one ZIP."""
    started = time.monotonic()
    status, saved, missing, size = "failed", 0, 0, 0
    try:
        data, saved, missing = await _build_evidence_bundle(value, parsed, context=context, client=client)
        status, size = "saved", len(data)
        return data
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        providers._log(context, "document_bundle_completed", provider_name=parsed.provider, status=status,
                       saved_images=saved, missing_images=missing, byte_size=size,
                       elapsed_ms=int((time.monotonic() - started) * 1000))
