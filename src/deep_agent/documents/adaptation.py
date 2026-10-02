"""Deterministic, loss-conscious model reading views of cached OCR documents.

This module never repairs OCR, merges rows, fetches images, or changes physical
page boundaries. Native documents remain the evidence; these pages are a stable
reading projection whose character offsets are assigned by the caller.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from html import escape, unescape
from html.parser import HTMLParser
import re
import threading
import time

from deep_agent.agent.context.estimator import TokenEstimator

from .models import DocumentPage, ParsedDocument

ADAPTATION_VERSION = "document-adaptation-v1"


class DocumentAdaptationCancelled(RuntimeError):
    """The owning Run cancelled its local adaptation worker."""


@dataclass(frozen=True)
class AdaptationStats:
    version: str
    source_characters: int
    adapted_characters: int
    table_count: int
    markdown_table_count: int
    compact_html_table_count: int
    fallback_count: int
    image_placeholder_count: int
    native_token_count: int
    adapted_token_count: int
    elapsed_ms: int


@dataclass(frozen=True)
class AdaptedDocument:
    pages: list[DocumentPage]
    stats: AdaptationStats


@dataclass
class _Counts:
    table_count: int = 0
    markdown_table_count: int = 0
    compact_html_table_count: int = 0
    fallback_count: int = 0
    image_placeholder_count: int = 0


@dataclass
class _Text:
    start: int
    end: int
    fallback: bool = False


@dataclass
class _Element:
    tag: str
    attrs: list[tuple[str, str | None]]
    start: int
    start_end: int
    end: int
    children: list[_Text | _Element] = field(default_factory=list)
    invalid: bool = False


_VOID = frozenset({"br", "hr", "img", "col", "wbr"})
_KNOWN = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "col", "colgroup",
    "p", "div", "span", "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup",
    "a", "img", "br", "hr", "wbr", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "font", "section", "article", "figure", "figcaption", "dl", "dt", "dd",
})
# Remove only known presentation attributes; unknown attributes may carry meaning.
_PRESENTATION = frozenset({
    "style", "class", "width", "height", "border", "bgcolor", "align", "valign",
    "cellpadding", "cellspacing", "color", "face",
})
_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)")
_BACKTICKS = re.compile(r"`+")
_CELL_MARKDOWN_LITERAL = re.compile(r"([\\`*_{}\[\]()|~!$#+])")
_START_TAG_TOKEN = re.compile(r'''<[A-Za-z][A-Za-z0-9:_-]*(?:[^"'<>]|"[^"]*"|'[^']*')*>''')
_MD_DATA_IMAGE = re.compile(
    r'(?<!\\)!\[((?:\\.|[^\]\\])*)\]\(\s*<?data:image/[^\s)>]+>?(?:\s+"(?:\\.|[^"])*")?\s*\)',
    re.IGNORECASE,
)


def _check_cancel(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise DocumentAdaptationCancelled("Document adaptation was cancelled")


def _code_spans(text: str, event: threading.Event | None = None) -> list[tuple[int, int]]:
    """Protect fenced blocks and exact-run inline backticks without sentinels."""
    fences: list[tuple[int, int]] = []
    position = 0
    opened: tuple[int, str, int] | None = None
    for line in text.splitlines(keepends=True):
        _check_cancel(event)
        if opened is None:
            match = _FENCE_OPEN.match(line)
            if match and not (match[1][0] == "`" and "`" in match[2]):
                opened = (position, match[1][0], len(match[1]))
        elif re.fullmatch(r"[ \t]{0,3}" + re.escape(opened[1]) + "{" + str(opened[2]) + r",}[ \t]*(?:\r?\n)?", line):
            fences.append((opened[0], position + len(line)))
            opened = None
        position += len(line)
    if opened is not None:
        fences.append((opened[0], len(text)))

    spans = list(fences)
    starts = [start for start, _ in fences]
    runs = []
    for match in _BACKTICKS.finditer(text):
        _check_cancel(event)
        index = bisect_left(starts, match.start() + 1) - 1
        if index < 0 or match.start() >= fences[index][1]:
            runs.append((match.start(), match.end()))
    next_by_length: dict[int, int] = {}
    matches: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        length = runs[index][1] - runs[index][0]
        if length in next_by_length:
            matches[index] = next_by_length[length]
        next_by_length[length] = index
    index = 0
    while index < len(runs):
        closing = matches.get(index)
        # Code spans must not consume a fenced block between their delimiters.
        boundary = bisect_left(starts, runs[index][1])
        if closing is not None and (boundary == len(fences) or fences[boundary][0] >= runs[closing][1]):
            spans.append((runs[index][0], runs[closing][1]))
            index = closing + 1
        else:
            index += 1
    return sorted(spans)


def _mask_code(text: str, spans: list[tuple[int, int]]) -> str:
    parts = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        # Equal-length masking retains HTMLParser's source positions. Original
        # bytes/code points are restored from source slices, never sentinel keys.
        parts.append(text[start:end].replace("<", "\x01").replace("&", "\x02"))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _marked_declarations(text: str) -> list[tuple[int, int]]:
    """Mask rejected declaration syntax without consuming an unclosed suffix.

    Opening and closing scans are independent so many incomplete openers do not
    repeatedly search the full remaining document. An unclosed opener needs only
    its '<' hidden from HTMLParser; the following prose/tables stay available.
    """
    tags = [(match.start(), match.end()) for match in _START_TAG_TOKEN.finditer(text)]
    tag_starts = [start for start, _ in tags]
    closings = [match.start() for match in re.finditer(r"\]>", text)]
    greater_than = [match.start() for match in re.finditer(">", text)]
    output = []
    for match in re.finditer(r"<!\[", text):
        start = match.start()
        if output and start < output[-1][1]:
            continue
        tag_index = bisect_left(tag_starts, start + 1) - 1
        if tag_index >= 0 and start < tags[tag_index][1]:
            continue  # This is literal attribute text in a normal opening tag.
        close_index = bisect_left(closings, match.end())
        end = match.end()
        if close_index < len(closings):
            closing = closings[close_index]
            first_greater = bisect_left(greater_than, match.end())
            # An earlier bare '>' makes this unknown opener malformed; do not
            # borrow an unrelated later ]> from subsequent prose/attributes.
            if text.startswith("<![CDATA[", start) or greater_than[first_greater] == closing + 1:
                end = closing + 2
        output.append((start, end))
    return output


class _PageParser(HTMLParser):
    def __init__(self, source: str, event: threading.Event | None):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.event = event
        self.lines = [0, *(match.end() for match in re.finditer("\n", source))]
        self.root = _Element("", [], 0, 0, len(source))
        self.stack = [self.root]
        self.images: list[_Element] = []
        self.table_count = 0
        self.declarations: list[tuple[int, int]] = []
        self.declaration_starts: list[int] = []
        self.tag_spans: list[tuple[int, int]] = []

    def _position(self) -> int:
        _check_cancel(self.event)
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self._position()
        raw = self.get_starttag_text()
        self.tag_spans.append((start, start + len(raw)))
        node = _Element(tag, attrs, start, start + len(raw), start + len(raw))
        node.invalid = len(self.stack) >= 128 or len({name for name, _ in attrs}) != len(attrs)
        self.stack[-1].children.append(node)
        if tag == "table":
            self.table_count += 1
        if tag == "img":
            self.images.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        start = self._position()
        end = self.source.find(">", start) + 1
        if end <= start:
            end = len(self.source)
        self.tag_spans.append((start, end))
        match = next((index for index in range(len(self.stack) - 1, 0, -1)
                      if self.stack[index].tag == tag), None)
        if match is None:
            self.stack[-1].children.append(_Text(start, end, True))
            return
        if match != len(self.stack) - 1:
            for node in self.stack[match:]:
                node.invalid = True
        for node in self.stack[match:]:
            node.end = end
        del self.stack[match:]

    def handle_data(self, data):
        start = self._position()
        end = start + len(data)
        first = max(0, bisect_left(self.declaration_starts, start + 1) - 1)
        declarations = []
        while first < len(self.declarations):
            left, right = self.declarations[first]
            if left >= end:
                break
            if right > start:
                declarations.append((left, right))
            first += 1
        cursor = start
        for left, right in declarations:
            if cursor < left:
                self.stack[-1].children.append(_Text(cursor, left))
            self.stack[-1].children.append(_Text(max(cursor, left), min(end, right), True))
            cursor = min(end, right)
        if cursor < end:
            # close() delivers a truncated opening tag as data. Preserve it as
            # evidence and count that local failure; masked code never matches.
            tail = data[cursor - start:]
            incomplete = bool(re.search(r"<[A-Za-z][^>]*\Z", tail)
                              or (tail == "<" and re.match(r"<[A-Za-z][^>]*\Z", self.source[cursor:])))
            self.stack[-1].children.append(_Text(cursor, end, incomplete))

    def handle_entityref(self, name):
        self._reference(name, 1)

    def handle_charref(self, name):
        self._reference(name, 2)

    def _reference(self, name: str, prefix: int):
        start = self._position()
        end = start + prefix + len(name)
        if self.source[end:end + 1] == ";":
            end += 1
        self.stack[-1].children.append(_Text(start, end))

    def handle_comment(self, data):
        start = self._position()
        end = self.source.find("-->", start)
        end = len(self.source) if end < 0 else end + 3
        self.tag_spans.append((start, end))
        self.stack[-1].children.append(_Text(start, end, True))

    def handle_decl(self, decl):
        self._declaration()

    def unknown_decl(self, data):
        self._declaration()

    def handle_pi(self, data):
        self._declaration()

    def _declaration(self):
        start = self._position()
        end = self.source.find(">", start)
        self.stack[-1].children.append(_Text(start, len(self.source) if end < 0 else end + 1, True))

    def finish(self):
        self.close()
        for node in self.stack[1:]:
            node.invalid = True
            node.end = len(self.source)


class _Renderer:
    def __init__(self, parser: _PageParser, spans, estimator, counts, event):
        self.parser = parser
        self.source = parser.source
        self.code_spans = spans
        self.code_starts = [start for start, _ in spans]
        # Markdown image-looking strings inside HTML attributes/comments are
        # literal metadata, not an actual image destination.
        self.spans = []
        for start, end in sorted([*spans, *parser.tag_spans]):
            if self.spans and start <= self.spans[-1][1]:
                self.spans[-1] = (self.spans[-1][0], max(end, self.spans[-1][1]))
            else:
                self.spans.append((start, end))
        self.span_starts = [start for start, _ in self.spans]
        self.estimator = estimator
        self.counts = counts
        self.event = event
        self.redactions = []
        for image in parser.images:
            attrs = dict(image.attrs)
            if (attrs.get("src") or "").lstrip().lower().startswith("data:"):
                self.redactions.append((image.start, image.end, self._placeholder(attrs.get("alt") or "")))
        self.redaction_starts = [start for start, _, _ in self.redactions]

    @staticmethod
    def _placeholder(alt: str) -> str:
        return "[图片引用未返回；内嵌图片仅保留在原始缓存" + ("；alt: " + alt if alt else "") + "]"

    def _markdown_images(self, value: str) -> str:
        def placeholder(match):
            self.counts.image_placeholder_count += 1
            return self._placeholder(match[1])
        return _MD_DATA_IMAGE.sub(placeholder, value)

    def _text(self, start: int, end: int) -> str:
        # Only recognized Markdown image destinations are replaced; bare URLs
        # and code spans are copied exactly, including any data: text in code.
        pieces = []
        cursor = start
        index = max(0, bisect_left(self.span_starts, start + 1) - 1)
        while index < len(self.spans):
            left, right = self.spans[index]
            if left >= end:
                break
            if right > cursor:
                pieces.append(self._markdown_images(self.source[cursor:max(cursor, left)]))
                pieces.append(self.source[max(cursor, left):min(end, right)])
                cursor = min(end, right)
            index += 1
        pieces.append(self._markdown_images(self.source[cursor:end]))
        return "".join(pieces)

    def _raw(self, start: int, end: int) -> str:
        pieces = []
        cursor = start
        index = bisect_left(self.redaction_starts, start)
        while index < len(self.redactions):
            left, right, placeholder = self.redactions[index]
            if left >= end:
                break
            if right <= end:
                pieces.append(self._text(cursor, left))
                pieces.append(placeholder)
                self.counts.image_placeholder_count += 1
                cursor = right
            index += 1
        pieces.append(self._text(cursor, end))
        return "".join(pieces)

    def render(self, node: _Text | _Element, *, compact: bool = False) -> str:
        _check_cancel(self.event)
        if isinstance(node, _Text):
            if node.fallback:
                self.counts.fallback_count += 1
            return self._text(node.start, node.end)
        if node.invalid or node.tag not in _KNOWN:
            self.counts.fallback_count += 1
            return self._raw(node.start, node.end)
        if node.tag == "table":
            if not compact:
                markdown = self._table_markdown(node)
                if markdown is not None:
                    # A speculative render must not inflate image/fallback counts.
                    saved = replace(self.counts)
                    html = self.render(node, compact=True)
                    for name in _Counts.__dataclass_fields__:
                        setattr(self.counts, name, getattr(saved, name))
                    if self.estimator.text(markdown) <= self.estimator.text(html):
                        self.counts.markdown_table_count += 1
                        return markdown
            self.counts.compact_html_table_count += 1
            compact = True  # Nested tables stay structurally nested in HTML.
        attrs = [(name, value) for name, value in node.attrs if name not in _PRESENTATION]
        if node.tag == "img" and (dict(attrs).get("src") or "").lstrip().lower().startswith("data:"):
            self.counts.image_placeholder_count += 1
            return self._placeholder(dict(attrs).get("alt") or "")
        attributes = "".join(" " + name + ("" if value is None else '="' + escape(value, quote=True) + '"')
                             for name, value in attrs)
        start = "<" + node.tag + attributes + ">"
        if node.tag in _VOID:
            return start
        children = (child for child in node.children
                    if not (node.tag in {"table", "thead", "tbody", "tfoot", "tr", "colgroup"}
                            and isinstance(child, _Text) and not child.fallback
                            and not self.source[child.start:child.end].strip()))
        return start + "".join(self.render(child, compact=compact) for child in children) + "</" + node.tag + ">"

    def _contains_code(self, node: _Element) -> bool:
        index = max(0, bisect_left(self.code_starts, node.start + 1) - 1)
        return any(left < node.end and right > node.start for left, right in self.code_spans[index:index + 2])

    def _literal_edges(self, node: _Text | _Element) -> tuple[str, str]:
        """Read only boundary text; do not let source tag wrappers hide spaces."""
        if isinstance(node, _Text):
            value = unescape(self.source[node.start:node.end])
            return value[:1], value[-1:]
        if node.tag == "br":
            return "\n", "\n"
        first = last = ""
        for child in node.children:
            left, right = self._literal_edges(child)
            if not first:
                first = left
            if right:
                last = right
        return first, last

    def _cell(self, node: _Text | _Element) -> str | None:
        if isinstance(node, _Text):
            if node.fallback:
                return None
            value = unescape(self.source[node.start:node.end])
            # HTML Text is literal. Decoding &lt;button&gt; must not introduce
            # a real tag, and *x*, _x_, `x`, or [x](y) must not acquire Markdown
            # roles. Keep entities one decode deep, including literal &lt; text
            # originally written as &amp;lt;. Actual breaks are handled separately;
            # cells with explicit emphasis retain their native HTML structure.
            value = escape(value, quote=False)
            value = _CELL_MARKDOWN_LITERAL.sub(r"\\\1", value)
            return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        # CommonMark emphasis delimiters depend on neighboring characters and
        # nesting. Adjacent strong/em nodes can merge into different delimiter
        # runs even when their literal child Text is escaped correctly. Preserve
        # the native HTML roles instead of guessing separators or rewriting text.
        if node.invalid or node.tag not in {"td", "th", "span", "font", "a", "br", "p", "div"}:
            return None
        # Preserve attributes with semantic meaning in HTML instead of discarding
        # links/titles/IDs or pretending a richer cell is just plain text.
        if any(name not in _PRESENTATION for name, _ in node.attrs):
            return None
        children = [self._cell(child) for child in node.children]
        if any(child is None for child in children):
            return None
        value = "".join(children)
        if node.tag in {"td", "th"}:
            first, last = self._literal_edges(node)
            # GFM trims a cell's outer whitespace even when our Markdown source
            # retains it. Edge newlines/entities and spans around literal spaces
            # likewise require HTML to preserve their original reading semantics.
            if first.isspace() or last.isspace():
                return None
        if node.tag == "br":
            return "<br>"
        if node.tag in {"p", "div"}:
            return value + "<br>"
        return value

    def _table_markdown(self, table: _Element) -> str | None:
        if table.invalid or self._contains_code(table):
            return None
        if any(name not in _PRESENTATION for name, _ in table.attrs):
            return None
        rows: list[tuple[list[_Element], bool]] = []
        before, after = [], []

        def collect(node: _Element, header=False) -> bool:
            for child in node.children:
                _check_cancel(self.event)
                if isinstance(child, _Text):
                    if child.fallback or self.source[child.start:child.end].strip():
                        return False
                elif child.invalid:
                    return False
                elif child.tag in {"thead", "tbody"}:
                    if any(name not in _PRESENTATION for name, _ in child.attrs) or not collect(child, header=child.tag == "thead"):
                        return False
                elif child.tag == "caption" and node is table:
                    value = self._cell(_Element("span", child.attrs, child.start, child.start_end, child.end, child.children))
                    if value is None:
                        return False
                    (after if rows else before).append(value)
                elif child.tag == "tr":
                    if any(name not in _PRESENTATION for name, _ in child.attrs):
                        return False
                    cells = []
                    for cell in child.children:
                        if isinstance(cell, _Text) and not cell.fallback and not self.source[cell.start:cell.end].strip():
                            continue
                        if not isinstance(cell, _Element) or cell.tag not in {"td", "th"} or cell.invalid:
                            return False
                        cells.append(cell)
                    if not cells or after:
                        return False
                    rows.append((cells, header))
                else:
                    return False
            return True

        if not collect(table) or not rows:
            return None
        width = len(rows[0][0])
        if any(len(cells) != width for cells, _ in rows):
            return None
        header = all(cell.tag == "th" for cell in rows[0][0])
        # A thead is not permission to upgrade its td cells to th. Markdown
        # assigns one role to the entire header row, so mixed roles stay HTML.
        if rows[0][1] and not header:
            return None
        # Row headers and additional header groups retain their explicit HTML.
        if any(in_header or any(cell.tag == "th" for cell in cells) for cells, in_header in rows[1:]):
            return None
        if not header and any(cell.tag == "th" for cell in rows[0][0]):
            return None
        values = [[self._cell(cell) for cell in cells] for cells, _ in rows]
        if any(value is None for row in values for value in row):
            return None
        output = ["|" + "|".join(values[0] if header else [""] * width) + "|",
                  "|" + "|".join(["---"] * width) + "|"]
        output.extend("|" + "|".join(row) + "|" for row in values[1:] if header)
        if not header:
            output.extend("|" + "|".join(row) + "|" for row in values)
        # Tables must begin on their own Markdown lines even when the native
        # HTML was directly adjacent to preceding/following prose.
        return "\n\n" + "\n\n".join([*before, "\n".join(output), *after]) + "\n\n"


def adapt_document(
    parsed: ParsedDocument, estimator: TokenEstimator, *, cancel_event: threading.Event | None = None,
) -> AdaptedDocument:
    """Build stable whole-page views; cancellation never mutates native input."""
    started = time.monotonic()
    counts = _Counts()
    pages = []
    native_tokens = adapted_tokens = 0
    for page in parsed.pages:
        _check_cancel(cancel_event)
        spans = _code_spans(page.content, cancel_event)
        parser = _PageParser(page.content, cancel_event)
        masked = _mask_code(page.content, spans)
        masked = re.sub(r"\\+<", lambda match: match[0][:-1] + ("\x01" if (len(match[0]) - 1) % 2 else "<"), masked)
        # HTMLParser rejects unsupported marked declarations. Preserve these
        # locally as literal source instead of failing an otherwise valid page.
        parser.declarations = _marked_declarations(masked)
        parser.declaration_starts = [start for start, _ in parser.declarations]
        parser.feed(_mask_code(masked, parser.declarations))
        parser.finish()
        counts.table_count += parser.table_count
        renderer = _Renderer(parser, spans, estimator, counts, cancel_event)
        content = "".join(renderer.render(child) for child in parser.root.children)
        pages.append(replace(page, content=content, format="markdown"))
        native_tokens += estimator.text(page.content)
        _check_cancel(cancel_event)
        adapted_tokens += estimator.text(content)
    _check_cancel(cancel_event)
    return AdaptedDocument(pages, AdaptationStats(
        version=ADAPTATION_VERSION,
        source_characters=sum(len(page.content) for page in parsed.pages),
        adapted_characters=sum(len(page.content) for page in pages),
        native_token_count=native_tokens,
        adapted_token_count=adapted_tokens,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        **{name: getattr(counts, name) for name in _Counts.__dataclass_fields__},
    ))
