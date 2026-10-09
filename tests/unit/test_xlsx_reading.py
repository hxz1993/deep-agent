from __future__ import annotations

import csv
import io
import random
import unittest
import zipfile
from datetime import date
from unittest.mock import patch
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Font

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError, xlsx_sheet_metadata, xlsx_workbook,
)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def save(workbook: Workbook) -> bytes:
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def with_cached_values(raw: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            data = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                caches = {"A1": (None, "0.015"), "B1": (None, "#REF!"), "C1": ("str", "")}
                for cell in root.iter(NS + "c"):
                    if cell.attrib["r"] in caches:
                        cell_type, value = caches[cell.attrib["r"]]
                        if cell_type is not None:
                            cell.set("t", cell_type)
                        if value == "#REF!":
                            cell.set("t", "e")
                        node = cell.find(NS + "v")
                        if node is None:
                            node = ET.SubElement(cell, NS + "v")
                        node.text = value
                data = ET.tostring(root)
            target.writestr(entry, data)
    return output.getvalue()


class XlsxReadingTests(unittest.TestCase):
    def test_optional_row_and_cell_coordinates_follow_source_order(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet["A1"] = "first"
        sheet["B1"].font = Font(bold=True)
        sheet["C1"] = "after-styled-blank"
        sheet["C4"] = "explicit-column"
        sheet["D4"] = "implicit-column"
        sheet["A5"] = "implicit-row"
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(save(workbook))) as source, zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(data)
                    for row in root.iter(NS + "row"):
                        if row.get("r") in {"1", "5"}:
                            row.attrib.pop("r")
                        for cell in row:
                            if cell.get("r") != "C4":
                                cell.attrib.pop("r", None)
                    data = ET.tostring(root)
                target.writestr(entry, data)
        result = xlsx_workbook(output.getvalue()).sheets[0]
        self.assertEqual(result.rows, (
            "first,,after-styled-blank", "", "",
            ",,explicit-column,implicit-column", "implicit-row",
        ))
        self.assertEqual(result.columns, 4)

    def test_style_inflated_dimension_and_sparse_row_positions(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet["A1"] = "one"
        sheet["C3"] = "three"
        sheet["XFD1048576"].font = Font(bold=True)
        raw = save(workbook)
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_CELLS", 2):
            result = xlsx_workbook(raw).sheets[0]
        self.assertEqual(result.rows, ("one", "", ",,three"))
        self.assertEqual(result.columns, 3)
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_CELLS", 1):
            with self.assertRaisesRegex(AttachmentParseError, "单元格数量过多"):
                xlsx_workbook(raw)

    def test_incorrect_dimensions_do_not_hide_cells(self) -> None:
        workbook = Workbook()
        workbook.active.append(["first", "second"])
        raw = save(workbook)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(data)
                    root.find(NS + "dimension").set("ref", "A1:A1")
                    data = ET.tostring(root)
                target.writestr(entry, data)
        self.assertEqual(xlsx_workbook(output.getvalue()).sheets[0].rows, ("first,second",))

    def test_csv_preserves_types_separators_and_cell_newlines(self) -> None:
        workbook = Workbook()
        values = ["000123", "NA", "#REF!", "a,b", 'say "hi"', "a|b", "line1\nline2", "中😀", True]
        workbook.active.append(values)
        result = xlsx_workbook(save(workbook)).sheets[0]
        decoded = next(csv.reader(io.StringIO(result.rows[0], newline="")))
        self.assertEqual(decoded, [*values[:-1], "TRUE"])
        self.assertEqual(result.headers, tuple(decoded))

    def test_common_display_formats_and_explicit_fallback(self) -> None:
        workbook = Workbook()
        entries = [(0.015, "0.0%"), (82.1, "0.00"), (1234.5, "#,##0.00"),
                   (123, "000000"), (0.1257, "0.0%"), (1.23, '0.00"元"'),
                   (date(2026, 10, 9), "yyyy-mm-dd"), ("000123", "000000")]
        for column, (value, fmt) in enumerate(entries, 1):
            cell = workbook.active.cell(1, column, value)
            cell.number_format = fmt
        result = xlsx_workbook(save(workbook))
        values = next(csv.reader(io.StringIO(result.sheets[0].rows[0])))
        self.assertEqual(values[:5], ["1.5%", "82.10", "1,234.50", "000123", "12.6% [原值: 0.1257]"])
        self.assertEqual(values[5], '1.23 [F1]')
        self.assertTrue(values[6].startswith("2026-10-09T00:00:00"))
        self.assertEqual(values[7], "000123")
        self.assertEqual(result.sheets[0].unsupported_number_format_count, 2)
        metadata = xlsx_sheet_metadata(result, result.sheets[0])
        self.assertEqual(metadata["row_format"], "csv")
        self.assertEqual(metadata["unsupported_number_format_count"], 2)
        self.assertEqual(metadata["unsupported_number_formats"]["F1"], '0.00"元"')

    def test_formula_caches_and_merge_structure_are_independent(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["=1.5/100", "=missing", '=IF(TRUE,"",1)', "=A3+1"])
        sheet["A1"].number_format = "0.0%"
        sheet["A3"] = "Merged header"
        sheet.merge_cells("A3:C3")
        result = xlsx_workbook(with_cached_values(save(workbook)))
        selected = result.sheets[0]
        self.assertEqual(selected.rows[0], "1.5%,#REF!,,")
        self.assertEqual(selected.formula_count, 4)
        self.assertEqual(selected.missing_formula_cache_count, 1)
        self.assertEqual(selected.merged_ranges, ("A3:C3",))
        self.assertEqual(selected.rows[2], "Merged header")
        self.assertEqual(xlsx_sheet_metadata(result, selected)["formula_mode"], "cached_values_only")

    def test_rendered_safety_budget_includes_quoting_and_blank_source_rows(self) -> None:
        workbook = Workbook()
        workbook.active["A1"] = "a,b"
        workbook.active["A3"] = "z"
        raw = save(workbook)
        # '"a,b"\n\nz' = 8 rendered characters.
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 8):
            self.assertEqual(xlsx_workbook(raw).sheets[0].rows, ('"a,b"', "", "z"))
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 7):
            with self.assertRaisesRegex(AttachmentParseError, "字符安全上限"):
                xlsx_workbook(raw)

    def test_many_short_rows_do_not_count_unused_style_cells(self) -> None:
        workbook = Workbook()
        rng = random.Random(1)
        for i in range(1, 201):
            workbook.active.cell(i, 1, f"{i}-{rng.random()}")
            workbook.active.cell(i, 16384).font = Font(bold=True)
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_CELLS", 200):
            result = xlsx_workbook(save(workbook)).sheets[0]
        self.assertEqual(len(result.rows), 200)
        self.assertEqual(result.columns, 1)


if __name__ == "__main__":
    unittest.main()
