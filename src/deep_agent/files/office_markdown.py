"""Local, explicitly selected MarkItDown converters; no plugins or LLM calls."""
from __future__ import annotations

import io
import re
from typing import Any


def docx_body(data: bytes) -> tuple[tuple[str, ...], dict[str, Any]]:
    from markitdown import StreamInfo
    from markitdown.converters import DocxConverter

    # Headers/footers and attributed comments are supplemented by our native
    # reader. The converter supplies body headings, links, lists and footnotes.
    converted = DocxConverter().convert(
        io.BytesIO(data), StreamInfo(extension=".docx"), keep_data_uris=False,
        escape_misc=True,
    )
    content = re.sub(
        r"(?<!\\)!\[([^\]]*)\]\(data:[^)]*\)",
        lambda match: "[内嵌图片" + ("：" + match[1] if match[1] else "") + "；未识别图片内容]",
        converted.markdown,
    )
    return _blocks(content), {"engine": "markitdown", "engine_version": "0.1.8"}


def pptx_body(data: bytes) -> tuple[tuple[str, ...], dict[str, Any]]:
    from markitdown import StreamInfo
    from markitdown.converters import PptxConverter
    from pptx import Presentation

    from deep_agent.agent.attachment_extractors import validate_office_archive

    validate_office_archive(data)
    presentation = Presentation(io.BytesIO(data))
    converted = PptxConverter().convert(
        io.BytesIO(data), StreamInfo(extension=".pptx"), keep_data_uris=False,
        escape_misc=True,
    )
    # Keep slide numbers in the readable content. Offsets remain logical blocks,
    # rather than pretending they are physical presentation pages.
    content = re.sub(r"<!-- Slide number: (\d+) -->", r"[幻灯片 \1]", converted.markdown)
    # The converter synthesizes shape-name JPG links, but does not expose
    # those files as authorized resources. Retain alt text without fake paths.
    content = re.sub(
        r"(?<!\\)!\[([^\]]*)\]\([^)]*\)",
        lambda match: "[内嵌图片" + ("：" + match[1] if match[1] else "")
        + "；未识别图片内容，非可读取资源]",
        content,
    )
    images = sum(
        1 for part in presentation.part.package.iter_parts()
        if str(part.content_type).startswith("image/") and str(part.partname).startswith("/ppt/media/")
    )
    return _blocks(content), {
        "engine": "markitdown", "engine_version": "0.1.8",
        "slides": len(presentation.slides), "embedded_image_parts": images,
        "warnings": [
            "读取文本、表格、支持的图表数据及备注；不保证原始布局或合并关系。",
            "图片引用不是OCR结果，未识别图片内文字；本工具不调用视觉模型。",
        ],
    }


def _blocks(content: str) -> tuple[str, ...]:
    # Conversion happens once before pagination. Never reconvert partial text:
    # char offsets refer to this fixed representation throughout the Run.
    return tuple(block for block in re.split(r"\n\s*\n", content.strip()) if block)
