"""Local PDF text-layer conversion with stable physical page boundaries.

No OCR, plugins or model clients are used. Page strings are converted once and
remain the source of Token pagination and character offsets throughout a Run.
"""
from __future__ import annotations

import io
from typing import Any

from deep_agent.agent.attachment_extractors import AttachmentParseError


def _validate_text_streams(reader: Any, single_limit: int, total_limit: int) -> None:
    """Bound streams consumed by text extraction, including invoked Forms/fonts.

    Image payloads are not decoded. Shared streams count once, while an active
    Form invocation cycle is rejected before the converter can recurse into it.
    """
    from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, StreamObject

    seen_streams: set[int] = set()
    seen_fonts: set[int] = set()
    expanded = 0

    def resolve(value: Any) -> Any:
        return value.get_object() if value is not None else None

    def check_stream(value: Any) -> Any:
        nonlocal expanded
        value = resolve(value)
        if isinstance(value, StreamObject) and id(value) not in seen_streams:
            size = len(value.get_data())
            expanded += size
            if size > single_limit or expanded > total_limit:
                raise AttachmentParseError("PDF 内容流超过解析安全上限")
            seen_streams.add(id(value))
        return value

    def check_fonts(resources: Any) -> None:
        fonts = resolve(resources.get("/Font")) or {}
        pending = list(fonts.values())
        while pending:
            font = resolve(pending.pop())
            if not isinstance(font, DictionaryObject) or id(font) in seen_fonts:
                continue
            seen_fonts.add(id(font))
            for name in ("/ToUnicode", "/Encoding"):
                check_stream(font.get(name))
            descriptor = resolve(font.get("/FontDescriptor")) or {}
            for name in ("/FontFile", "/FontFile2", "/FontFile3"):
                check_stream(descriptor.get(name))
            descendants = resolve(font.get("/DescendantFonts")) or []
            pending.extend(descendants)

    # Explicit traversal avoids Python recursion for deeply nested resources.
    visited_forms: set[tuple[int, int]] = set()
    active_forms: set[int] = set()
    for page in reader.pages:
        pending = [(page.get("/Contents"), resolve(page.get("/Resources")) or {}, None)]
        while pending:
            contents, resources, exit_form = pending.pop()
            if exit_form is not None:
                active_forms.discard(exit_form)
                continue
            check_fonts(resources)
            contents = resolve(contents)
            if contents is None:
                continue
            if isinstance(contents, StreamObject) and contents.get("/Subtype") == "/Form":
                key = (id(contents), id(resources))
                if key in visited_forms:
                    continue
                visited_forms.add(key)
                active_forms.add(id(contents))
            for stream in contents if isinstance(contents, ArrayObject) else [contents]:
                check_stream(stream)
            xobjects = resolve(resources.get("/XObject")) or {}
            children = []
            for operands, operator in ContentStream(contents, reader).operations:
                if operator != b"Do" or not operands:
                    continue
                form = resolve(xobjects.get(operands[0]))
                if not isinstance(form, StreamObject) or form.get("/Subtype") != "/Form":
                    continue
                if id(form) in active_forms:
                    raise AttachmentParseError("PDF 包含循环引用的 Form 内容流")
                form_resources = resolve(form.get("/Resources")) or resources
                key = (id(form), id(form_resources))
                if key in visited_forms:
                    continue
                children.append((form, form_resources, id(form)))
            # Finish one child before traversing siblings, preserving an actual
            # invocation stack rather than treating shared resources as cycles.
            for form, form_resources, form_id in reversed(children):
                pending.append((form, form_resources, form_id))
                pending.append((form, form_resources, None))


def pdf_document(data: bytes) -> tuple[tuple[str, ...], dict[str, Any]]:
    from markitdown import StreamInfo
    from markitdown.converters import PdfConverter
    from pypdf import PdfReader, PdfWriter
    from deep_agent.agent.attachment_extractors import (
        MAX_OFFICE_EXPANDED_BYTES, MAX_OFFICE_MEMBER_BYTES,
    )

    pages: list[str] = []
    empty_pages: list[int] = []
    total_chars = 0
    try:
        with PdfReader(io.BytesIO(data), strict=True) as reader:
            if reader.is_encrypted:
                raise AttachmentParseError("加密 PDF 不受支持；请提供已解密的原件")
            if not len(reader.pages):
                raise AttachmentParseError("PDF 没有可读取页面")
            _validate_text_streams(reader, MAX_OFFICE_MEMBER_BYTES, MAX_OFFICE_EXPANDED_BYTES)
            converter = PdfConverter()
            for number, source_page in enumerate(reader.pages, 1):
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
