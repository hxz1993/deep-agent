"""Sparse OOXML facts and a deliberately limited Excel display projection.

openpyxl still decodes cells, shared strings, dates and formula caches.  This
module supplies real content bounds and never evaluates formulas or guesses
unsupported Excel formats.
"""
from __future__ import annotations

import csv
import io
import math
import posixpath
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zipfile import ZipFile

from defusedxml.ElementTree import iterparse, fromstring
from openpyxl.utils.cell import coordinate_to_tuple

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass(frozen=True)
class SheetFacts:
    row_widths: dict[int, int]
    merged_ranges: tuple[str, ...]
    formula_count: int
    missing_formula_cache_count: int
    content_cells: int
    embedded_image_count: int


def _part(base: str, target: str) -> str:
    result = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                               else posixpath.join(base, target))
    if result.startswith("../") or result == "..":
        raise ValueError("XLSX 包含不安全的关联路径")
    return result


def _relationships(archive: ZipFile, filename: str) -> dict[str, str]:
    if filename not in archive.namelist():
        return {}
    root = fromstring(archive.read(filename))
    return {e.attrib["Id"]: e.attrib["Target"] for e in root
            if e.tag == _REL + "Relationship" and e.get("TargetMode") != "External"}


def workbook_facts(data: bytes, *, max_cells: int) -> dict[str, SheetFacts]:
    """Scan stored content, not the style-inflated rectangular used range."""
    facts: dict[str, SheetFacts] = {}
    content_count = 0
    with ZipFile(io.BytesIO(data)) as archive:
        sheet_links = _relationships(archive, "xl/_rels/workbook.xml.rels")
        workbook = fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.iter(_NS + "sheet"):
            name = sheet.attrib["name"]
            path = _part("xl", sheet_links[sheet.attrib[_DOC_REL + "id"]])
            widths: dict[int, int] = {}
            merges: list[str] = []
            formulas = missing = content = 0
            with archive.open(path) as source:
                for _, element in iterparse(source, events=("end",)):
                    if element.tag == _NS + "c":
                        formula = element.find(_NS + "f")
                        value = element.find(_NS + "v")
                        inline = element.find(_NS + "is")
                        # Blank styled cells have no source content. Formula
                        # cells count even when their cached value is absent.
                        if formula is not None or value is not None or inline is not None:
                            row, column = coordinate_to_tuple(element.attrib["r"])
                            if not (1 <= row <= 1_048_576 and 1 <= column <= 16_384):
                                raise ValueError("XLSX 单元格坐标超出格式范围")
                            widths[row] = max(widths.get(row, 0), column)
                            content += 1
                            content_count += 1
                            if content_count > max_cells:
                                raise ValueError("XLSX 单次解析的单元格数量过多")
                            if formula is not None:
                                formulas += 1
                                # Empty string formula caches are valid strings.
                                if (value is None or value.text is None) and element.get("t") != "str":
                                    missing += 1
                        element.clear()
                    elif element.tag == _NS + "mergeCell":
                        merges.append(element.attrib["ref"])
                        element.clear()
                    elif element.tag == _NS + "row":
                        element.clear()
            image_count = 0
            links_path = posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")
            links = _relationships(archive, links_path)
            for target in links.values():
                drawing = _part(posixpath.dirname(path), target)
                if "/drawings/" in drawing and drawing.endswith(".xml"):
                    root = fromstring(archive.read(drawing))
                    image_count += sum(1 for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "pic")
            facts[name] = SheetFacts(widths, tuple(merges), formulas, missing, content, image_count)
    return facts


_SIMPLE_NUMBER = re.compile(r"(?P<integer>0+|#,##0)(?:\.(?P<decimal>0+))?(?P<percent>%)?\Z")


def cell_display(cell: Any, raw_text: str, format_codes: dict[str, str]) -> tuple[str, bool]:
    """Render common numeric formats; retain value and format for all others.

    No colour, conditional, currency, fraction, scaling, scientific or locale
    custom format is interpreted.  Dates are decoded by openpyxl and retain
    their ISO value rather than silently imposing an Excel locale.
    """
    value = cell.value
    number_format = cell.number_format or "General"
    def fallback() -> tuple[str, bool]:
        key = format_codes.setdefault(number_format, f"F{len(format_codes) + 1}")
        return f"{raw_text} [{key}]", True
    if value is None or isinstance(value, (str, bool)) or number_format in {"General", "@"}:
        return raw_text, False
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return fallback()
    # Excel _x reserves character width and *x fills free display space; these
    # have no numeric meaning. Only normalize unquoted simple numeric formats,
    # never strip literals, sections, conditions or currency units.
    normalized = re.sub(r"[_*].", "", number_format) if '"' not in number_format and "\\" not in number_format else number_format
    match = _SIMPLE_NUMBER.fullmatch(normalized)
    if match is None:
        return fallback()
    decimals = len(match.group("decimal") or "")
    # An absurd precision is an unsupported format, not a huge allocation.
    if decimals > 20 or len(match.group("integer")) > 64:
        return fallback()
    source = Decimal(str(value))
    scaled = source * (100 if match.group("percent") else 1)
    quantum = Decimal(1).scaleb(-decimals)
    try:
        rounded = scaled.quantize(quantum, rounding=ROUND_HALF_UP)
    except ArithmeticError:
        return fallback()
    grouping = "," if match.group("integer") == "#,##0" else ""
    rendered = format(rounded, f"{grouping}.{decimals}f")
    if not grouping:
        sign = "-" if rendered.startswith("-") else ""
        integer, *fraction = rendered.lstrip("-").split(".")
        rendered = sign + integer.zfill(len(match.group("integer")))
        if fraction:
            rendered += "." + fraction[0]
    if match.group("percent"):
        rendered += "%"
    # Display rounding must not erase additional stored precision.
    if scaled != rounded:
        rendered += f" [原值: {raw_text}]"
    return rendered, False


def csv_row(values: list[str]) -> str:
    if not values:
        return ""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(values)
    return output.getvalue()[:-2]
