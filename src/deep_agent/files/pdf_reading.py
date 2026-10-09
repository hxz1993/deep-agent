"""Local PDF text-layer conversion with stable physical page boundaries.

No OCR, plugins or model clients are used. Page strings are converted once and
remain the source of Token pagination and character offsets throughout a Run.
"""
from __future__ import annotations

import io
from typing import Any

from deep_agent.agent.attachment_extractors import AttachmentParseError


def pdf_document(data: bytes) -> tuple[tuple[str, ...], dict[str, Any]]:
    from markitdown import StreamInfo
    from markitdown.converters import PdfConverter
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject
    from deep_agent.agent.attachment_extractors import (
        MAX_OFFICE_EXPANDED_BYTES, MAX_OFFICE_MEMBER_BYTES,
    )

    pages: list[str] = []
    empty_pages: list[int] = []
    total_chars = 0
    expanded = 0
    try:
        with PdfReader(io.BytesIO(data), strict=True) as reader:
            if reader.is_encrypted:
                raise AttachmentParseError("加密 PDF 不受支持；请提供已解密的原件")
            if not len(reader.pages):
                raise AttachmentParseError("PDF 没有可读取页面")
            converter = PdfConverter()
            for number, source_page in enumerate(reader.pages, 1):
                # Validate only content streams consumed by the text converter;
                # image streams are not decoded or rendered. pypdf also has its
                # own bounded Flate decompressor for adversarial compressed data.
                contents = source_page.get("/Contents")
                contents = contents.get_object() if contents is not None else None
                streams = contents if isinstance(contents, ArrayObject) else [contents]
                for stream in streams:
                    if stream is None:
                        continue
                    size = len(stream.get_object().get_data())
                    expanded += size
                    if size > MAX_OFFICE_MEMBER_BYTES or expanded > MAX_OFFICE_EXPANDED_BYTES:
                        raise AttachmentParseError("PDF 内容流超过解析安全上限")
                # The public converter has no per-page API. A one-page PDF
                # ensures its form/plain-text heuristics cannot discard source
                # page boundaries, including blank or image-only pages.
                with PdfWriter() as writer:
                    writer.add_page(source_page)
                    single = io.BytesIO()
                    writer.write(single)
                    single.seek(0)
                    converted = converter.convert(single, StreamInfo(extension=".pdf"))
                body = converted.markdown.strip()
                if not body:
                    empty_pages.append(number)
                    body = "[未提取到文本，不代表原页无内容；可能为空白页或需要OCR]"
                value = f"[PDF第{number}页]\n{body}"
                total_chars += len(value)
                # Parsing safety limit, not a tool-return limit. Matches the
                # existing Office extraction bound and precedes Run caching.
                if total_chars > 8_000_000:
                    raise AttachmentParseError("PDF 提取文本超过 8000000 字符解析安全上限")
                pages.append(value)
    except AttachmentParseError:
        raise
    except Exception as exc:
        # Do not expose PDF content or exception internals through tool errors.
        raise AttachmentParseError("PDF 读取失败；请确认文件完整、未损坏且可解析") from exc
    warnings = [
        "仅读取PDF文本层及转换器可识别的表格；不执行OCR，不保证布局、手写、图片内文字或表格关系完整。",
        "页数与续读完成仅表示读取范围完成，不保证原件内容全部提取；关键字段须核对原页。",
    ]
    if empty_pages:
        warnings.append("存在未提取到文本的页面；未提取到不等于原件没有内容。")
    return tuple(pages), {
        "engine": "markitdown", "engine_version": "0.1.8",
        "physical_pages": len(pages), "pages_without_text": empty_pages,
        "warnings": warnings,
    }
