"""Stable, conservative OCR evidence and a bounded model layout projection.

Provider coordinates describe their rendering plane, not necessarily the source
PDF. This module does not register images or infer a missing physical page.
Native provider dictionaries never cross the evidence export boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import math
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from deep_agent.agent.context.estimator import TokenEstimator

from .adaptation import ADAPTATION_VERSION, AdaptedDocument, DocumentAdaptationCancelled, adapt_document
from .models import DocumentPage, ParsedDocument
from .native_content import _identifier_character

SCHEMA_VERSION = "document-evidence-v1"
LAYOUT_VERSION = "document-layout-v1"

_URL = re.compile(r"(?:https?://|file://|data:)[^\s<>\"')\]]+", re.IGNORECASE)
_QUOTED_DATA = re.compile(r"([\"\'])(data:[^\"\']*)\1", re.IGNORECASE)
_MARKDOWN_DATA = re.compile(r"\(\s*data:[^)]*\)", re.IGNORECASE)
_MACHINE_PATH = re.compile(r"(?<![\w:/])(?:/(?:Users|home|tmp|var|private|opt|mnt)/|[A-Za-z]:[\\/])[^\s<>\"')\]]+")
_SIGNED_KEYS = frozenset({"signature", "sig", "token", "access_token", "auth", "authorization", "credential", "expires", "expiry", "key-pair-id", "policy"})


def _check_cancel(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise DocumentAdaptationCancelled("Document evidence generation was cancelled")


def _safe_text(value: str) -> str:
    """Remove transport artifacts while retaining ordinary document markup/text."""
    def replace(match: re.Match[str]) -> str:
        url = match[0]
        try:
            # HTML attributes commonly escape query separators as &amp;. Decode
            # only for classification; ordinary links retain exact source text.
            parsed = urlsplit(unescape(url))
            if parsed.scheme.lower() == "data":
                return "[内嵌数据已省略]"
            if parsed.scheme.lower() == "file":
                return "[运行路径已省略]"
            keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
            if parsed.username or parsed.password or any(key in _SIGNED_KEYS or key.startswith(("x-amz-", "x-goog-", "x-oss-")) for key in keys):
                return "[临时下载地址已省略]"
        except ValueError:
            return "[无效地址已省略]"
        return url
    value = _QUOTED_DATA.sub(lambda match: match[1] + "[内嵌数据已省略]" + match[1], value)
    value = _MARKDOWN_DATA.sub("([内嵌数据已省略])", value)
    return _MACHINE_PATH.sub("[运行路径已省略]", _URL.sub(replace, value))


def _number(value: Any) -> int | float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value


def _dimension(value: Any) -> int | float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _native_blocks(page: DocumentPage, provider: str) -> list[tuple[int, dict[str, Any]]]:
    if provider == "paddle":
        pruned = page.native.get("prunedResult")
        blocks = pruned.get("parsing_res_list") if isinstance(pruned, dict) else None
    elif provider == "glm":
        blocks = page.native.get("blocks")
    else:
        blocks = None
    return [(i, block) for i, block in enumerate(blocks or [], 1) if isinstance(block, dict)] if isinstance(blocks, list) else []


def _page_geometry(parsed: ParsedDocument, page: DocumentPage) -> dict[str, Any]:
    width = height = None
    coordinate_space = "unknown"
    original_mapping = "unverified"
    preprocessing: dict[str, Any] = {}
    reported_sizes: list[list[int | float]] = []
    if parsed.provider == "paddle":
        pruned = page.native.get("prunedResult")
        if isinstance(pruned, dict):
            width, height = _dimension(pruned.get("width")), _dimension(pruned.get("height"))
            coordinate_space = "paddle_processed_page_pixels"
            preprocessing_source = pruned.get("doc_preprocessor_res")
            if isinstance(preprocessing_source, dict):
                settings = preprocessing_source.get("model_settings")
                if isinstance(settings, dict):
                    for key in ("use_doc_orientation_classify", "use_doc_unwarping"):
                        if type(settings.get(key)) is bool:
                            preprocessing[key] = settings[key]
                angle = _number(preprocessing_source.get("angle"))
                if angle is not None:
                    preprocessing["angle"] = angle
                if preprocessing.get("use_doc_unwarping") is True or angle not in (None, 0):
                    original_mapping = "unavailable"
    elif parsed.provider == "glm":
        coordinate_space = "glm_page_pixels"
        data_info = parsed.native.get("data_info")
        page_info = data_info.get("pages") if isinstance(data_info, dict) else None
        # A reliable physical number selects dimensions. With unknown numbering,
        # the provider's output ordinal does not establish an original page.
        index = page.page - 1 if type(page.page) is int and page.page > 0 else None
        if isinstance(page_info, list) and index is not None and index < len(page_info) and isinstance(page_info[index], dict):
            width, height = _dimension(page_info[index].get("width")), _dimension(page_info[index].get("height"))
        sizes = {(_dimension(b.get("width")), _dimension(b.get("height"))) for _, b in _native_blocks(page, parsed.provider)}
        sizes = {(w, h) for w, h in sizes if w is not None and h is not None}
        if len(sizes) > 1 or (width is not None and height is not None and sizes and (width, height) not in sizes):
            # Contradictory provider page dimensions cannot establish which
            # rendering plane the bounding boxes use. Never rescale by guessing.
            reported_sizes = [list(pair) for pair in sorted(sizes | ({(width, height)} if width is not None and height is not None else set()))]
            width = height = None
            original_mapping = "conflicting_page_size"
        elif (width is None or height is None) and len(sizes) == 1:
            w, h = next(iter(sizes))
            if (width is not None and width != w) or (height is not None and height != h):
                reported_sizes = [[w, h]]
                width = height = None
                original_mapping = "conflicting_page_size"
            else:
                width, height = width or w, height or h
    if type(page.page) is not int or page.page < 1:
        original_mapping = "unknown_page"
    return {"width": width, "height": height, "coordinate_space": coordinate_space,
            "original_page_mapping": original_mapping, "preprocessing": preprocessing,
            **({"reported_page_sizes": reported_sizes} if reported_sizes else {})}


def _location(block: dict[str, Any], provider: str, geometry: dict[str, Any], page: int | None) -> tuple[tuple[int | float, ...] | None, str]:
    raw = block.get("block_bbox" if provider == "paddle" else "bbox_2d")
    if raw is None:
        return None, "missing_bbox"
    if not isinstance(raw, (list, tuple)) or len(raw) != 4 or any(_number(v) is None for v in raw):
        return None, "invalid_bbox"
    bbox = tuple(raw)
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 < x0 or y1 < y0:
        return bbox, "invalid_bbox"
    if page is None:
        return bbox, "unknown_page"
    width, height = geometry["width"], geometry["height"]
    if width is None or height is None:
        return bbox, "missing_page_size"
    if x1 > width or y1 > height:
        return bbox, "outside_page"
    if x0 == x1 or y0 == y1:
        return bbox, "empty_bbox"
    return bbox, "available"


def _find_span(text: str, candidates: list[str], used: list[tuple[int, int]]) -> tuple[int, int] | None:
    # Repeated blocks allocate distinct occurrences; a short identifier cannot
    # acquire the location of a different identifier containing that substring.
    for candidate in dict.fromkeys(candidates):
        if not candidate or not candidate.strip():
            continue
        offset = 0
        while True:
            start = text.find(candidate, offset)
            if start < 0:
                break
            end = start + len(candidate)
            offset = end
            if start and _identifier_character(candidate[0]) and _identifier_character(text[start - 1]):
                continue
            if end < len(text) and _identifier_character(candidate[-1]) and _identifier_character(text[end]):
                continue
            if any(start < b and end > a for a, b in used):
                continue
            return start, end
    return None


@dataclass(frozen=True)
class EvidenceBlock:
    block_id: str
    ordinal: int
    label: str
    native_content: str
    bbox: tuple[int | float, ...] | None
    location_status: str
    char_start: int
    char_end: int
    mapping_status: str
    polygon_points: tuple[tuple[int | float, int | float], ...] | None = None

    def metadata(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "ordinal": self.ordinal, "label": self.label,
                "bbox": list(self.bbox) if self.bbox is not None else None,
                "location_status": self.location_status, "char_start": self.char_start,
                "char_end": self.char_end, "mapping_status": self.mapping_status}


@dataclass(frozen=True)
class EvidencePage:
    page: int | None
    ordinal: int
    content: str
    native_content: str
    geometry: dict[str, Any]
    blocks: tuple[EvidenceBlock, ...]
    unmapped_spans: tuple[tuple[int, int], ...]
    format: str = "markdown"

    def fragment_layout(self, start: int, end: int) -> dict[str, Any]:
        if start < 0 or end < start or end > len(self.content):
            raise ValueError("Invalid evidence fragment range")
        blocks = []
        for block in self.blocks:
            if block.char_start < end and block.char_end > start:
                item = block.metadata()
                item.pop("ordinal")
                if item["mapping_status"] == "exact":
                    item.pop("mapping_status")
                if start > block.char_start or end < block.char_end:
                    item.update(fragment_start=max(start, block.char_start), fragment_end=min(end, block.char_end))
                blocks.append(item)
        return {"version": LAYOUT_VERSION, "page_ordinal": self.ordinal, **self.geometry,
                "blocks": blocks,
                "unmapped_spans": [{"char_start": max(start, a), "char_end": min(end, b)}
                                   for a, b in self.unmapped_spans if a < end and b > start]}

    def export_dict(self) -> dict[str, Any]:
        return {"page_number": self.page, "page_ordinal": self.ordinal, "format": self.format,
                "content": self.content, "native_content": self.native_content,
                **self.geometry,
                "blocks": [{**block.metadata(), "native_content": block.native_content,
                            "polygon_points": [list(point) for point in block.polygon_points] if block.polygon_points is not None else None}
                           for block in self.blocks],
                "unmapped_spans": [{"char_start": a, "char_end": b} for a, b in self.unmapped_spans]}


@dataclass(frozen=True)
class DocumentEvidence:
    document_id: str
    provider: str
    model: str
    status: str
    pages: tuple[EvidencePage, ...]
    warnings: tuple[str, ...]
    stats: dict[str, int | str]

    def export_dict(self, resource: dict[str, Any], source_hash: str, summary: dict[str, Any], *, source_storage: str, status: str | None = None) -> dict[str, Any]:
        """Complete evidence, never the current read window or arbitrary native JSON."""
        if source_storage not in {"platform", "local", "skill"}:
            raise ValueError("Unknown source storage")
        if hasattr(resource, "model_dump"):
            resource = resource.model_dump(mode="json")
        kind = resource.get("type")
        allowed = ("type", "file_id") if kind == "attachment" else ("type", "skill_code", "path") if kind == "skill" else ()
        if not allowed:
            raise ValueError("Evidence source must be an authorized file resource")
        source = {key: _safe_text(resource[key]) for key in allowed if isinstance(resource.get(key), str)}
        safe_summary = {key: summary.get(key) for key in ("source_pages", "parsed_pages", "page_numbers_known", "missing_pages", "coverage") if key in summary}
        completeness = status or self.status
        if self.status == "partial" or summary.get("coverage") == "partial":
            completeness = "partial"
        if completeness not in {"complete", "partial"}:
            raise ValueError("Unknown evidence completeness")
        return {"schema_version": SCHEMA_VERSION, "adaptation_version": ADAPTATION_VERSION,
                "layout_version": LAYOUT_VERSION, "document_id": self.document_id,
                "provider": self.provider, "model": self.model, "status": completeness,
                "source": {"storage": source_storage, "resource": source, "sha256": source_hash},
                "summary": safe_summary, "warnings": list(self.warnings),
                "pages": [page.export_dict() for page in self.pages]}


def build_document_evidence(parsed: ParsedDocument, readable: AdaptedDocument, document_id: str,
                            estimator: TokenEstimator, *, cancel_event: threading.Event | None = None) -> DocumentEvidence:
    """Build once before pagination; all offsets refer to this fixed projection."""
    if len(parsed.pages) != len(readable.pages):
        raise ValueError("Readable and native document page counts differ")
    started = time.monotonic()
    pages: list[EvidencePage] = []
    appended = matched = native_tokens = layout_tokens = 0
    adapted_blocks: dict[str, str] = {}
    for ordinal, (native, adapted) in enumerate(zip(parsed.pages, readable.pages, strict=True), 1):
        _check_cancel(cancel_event)
        if native.page != adapted.page:
            raise ValueError("Readable and native document page order differs")
        content = _safe_text(adapted.content)
        geometry = _page_geometry(parsed, native)
        blocks: list[EvidenceBlock] = []
        used: list[tuple[int, int]] = []
        for block_ordinal, block in _native_blocks(native, parsed.provider):
            _check_cancel(cancel_event)
            raw = block.get("block_content" if parsed.provider == "paddle" else "content")
            text = _safe_text(raw) if isinstance(raw, str) else ""
            label = block.get("block_label" if parsed.provider == "paddle" else "label")
            label = label if isinstance(label, str) and re.fullmatch(r"[\w.-]{1,64}", label) else "unknown"
            if text not in adapted_blocks:
                view = adapt_document(ParsedDocument(parsed.provider, parsed.model, [DocumentPage(native.page, text)], parsed.source_pages),
                                      estimator, cancel_event=cancel_event).pages[0].content
                adapted_blocks[text] = view
            view = adapted_blocks[text]
            span = _find_span(content, [view, view.strip(), text, text.strip()], used)
            if span is not None:
                mapping_status = "exact"
                matched += 1
            elif text.strip():
                # A context-dependent Markdown conversion may not have an exact
                # match. Preserve the existing page and add only that block; do
                # not guess a span or strip unrelated text to force equality.
                prefix = "\n\n【独立布局块：正文位置无法可靠对应】\n\n"
                start = len(content) + len(prefix)
                content += prefix + view
                span = (start, len(content))
                mapping_status = "appended"
                appended += 1
            else:
                # Give every image/empty native block a real position in the
                # fixed layout view. A thousand zero-length spans at position 0
                # would force all metadata into one indivisible budget envelope.
                # This marker is explicitly generated, never provider OCR text.
                content += "\n\n"
                start = len(content)
                content += "【布局块：供应商未返回文字】"
                span = (start, len(content))
                mapping_status = "no_text"
            used.append(span)
            bbox, location_status = _location(block, parsed.provider, geometry, native.page)
            polygon = block.get("block_polygon_points") if parsed.provider == "paddle" else None
            polygon = (tuple(tuple(point) for point in polygon)
                       if isinstance(polygon, list) and len(polygon) >= 3
                       and all(isinstance(point, (list, tuple)) and len(point) == 2
                               and all(_number(value) is not None for value in point) for point in polygon) else None)
            blocks.append(EvidenceBlock(f"p{ordinal}:b{block_ordinal}", block_ordinal, label, text,
                                        bbox, location_status, *span, mapping_status, polygon))
        gaps: list[tuple[int, int]] = []
        cursor = 0
        for start, end in sorted(used):
            if start > cursor and content[cursor:start].strip():
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < len(content) and content[cursor:].strip():
            gaps.append((cursor, len(content)))
        pages.append(EvidencePage(native.page, ordinal, content, _safe_text(native.content), geometry, tuple(blocks), tuple(gaps)))
        native_tokens += estimator.text(native.content)
        layout_tokens += estimator.text(content)
    _check_cancel(cancel_event)
    return DocumentEvidence(document_id, parsed.provider, parsed.model, parsed.status, tuple(pages),
                            tuple(_safe_text(warning) for warning in parsed.warnings),
                            {"version": LAYOUT_VERSION, "elapsed_ms": round((time.monotonic() - started) * 1000),
                             "block_count": sum(len(page.blocks) for page in pages), "matched_blocks": matched,
                             "appended_blocks": appended, "native_token_count": native_tokens,
                             "layout_token_count": layout_tokens})
