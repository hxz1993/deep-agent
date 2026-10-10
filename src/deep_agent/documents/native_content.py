"""Retain layout text omitted from a provider's Markdown reading view.

This is a conservative evidence projection, not OCR or business correction.
Original Markdown is never removed. Unrepresented native blocks are appended
in their supplied order, with their source positions explicitly identified.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
import threading

from .models import DocumentPage

_TEXT_HTML = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "col", "colgroup",
    "p", "div", "span", "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup",
    "a", "img", "br", "hr", "wbr", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "font", "section", "article", "figure", "figcaption", "dl", "dt", "dd",
})


def _identifier_character(value: str) -> bool:
    return bool(value) and value.isascii() and (value.isalnum() or value in "_.-")


def _occurrences(text: str, content: str) -> int:
    """A short ID embedded in a different longer ID is not evidence of coverage."""
    count, offset = 0, 0
    while content:
        start = text.find(content, offset)
        if start < 0:
            break
        end = start + len(content)
        left = start > 0 and _identifier_character(content[0]) and _identifier_character(text[start - 1])
        right = end < len(text) and _identifier_character(content[-1]) and _identifier_character(text[end])
        if not left and not right:
            count += 1
        offset = end
    return count


class _VisibleText(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.feed(source)
        self.close()

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag not in _TEXT_HTML:
            self.parts.append(self.get_starttag_text())
        elif tag in {"br", "hr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag not in _TEXT_HTML:
            self.parts.append(f"</{tag}>")
        elif tag in {"p", "div", "tr", "td", "th", "li"}:
            self.parts.append("\n")


def _comparison_text(text: str) -> str:
    # Comparison only: never write this lossy view back to the document. Do not
    # remove digits, punctuation, case or intra-word whitespace to infer equality.
    try:
        value = "".join(_VisibleText(text).parts)
    except (ValueError, AssertionError):
        value = text
    return re.sub(r"\s+", " ", value).strip()


def supplement_native_blocks(
    page: DocumentPage, provider: str, *, cancel_event: threading.Event | None = None,
) -> tuple[str, int]:
    if provider == "paddle":
        pruned = page.native.get("prunedResult")
        blocks = pruned.get("parsing_res_list") if isinstance(pruned, dict) else None
        content_key, label_key = "block_content", "block_label"
    elif provider == "glm":
        blocks = page.native.get("blocks")
        content_key, label_key = "content", "label"
    else:
        return page.content, 0
    if not isinstance(blocks, list):
        return page.content, 0

    visible = _comparison_text(page.content)
    used: dict[str, int] = {}
    capacity: dict[str, int] = {}
    extra: list[str] = []
    for position, block in enumerate(blocks, 1):
        if cancel_event is not None and cancel_event.is_set():
            # The caller owns the cancellation exception and cleanup.
            return page.content, 0
        if not isinstance(block, dict):
            continue
        content = block.get(content_key)
        if not isinstance(content, str) or not content.strip():
            continue
        key = _comparison_text(content)
        # Exact native markup or visible text can establish representation.
        # Count occurrences so two equal native blocks are not silently merged.
        if key:
            if key not in capacity:
                capacity[key] = max(_occurrences(page.content, content), _occurrences(visible, key))
            used[key] = used.get(key, 0) + 1
            if used[key] <= capacity[key]:
                continue
        elif content in page.content:
            continue
        label = block.get(label_key)
        label = label if isinstance(label, str) and re.fullmatch(r"[\w.-]{1,64}", label) else "unknown"
        extra.append(f"【原始布局块 {position} · {label}】\n\n{content}")
    if not extra:
        return page.content, 0
    return (page.content + "\n\n### 原始布局补充（供应商正文未呈现；按原始块顺序）\n\n"
            + "\n\n".join(extra)), len(extra)
