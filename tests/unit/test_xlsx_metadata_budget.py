from __future__ import annotations

import io
import re
import unittest
import zipfile
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from deep_agent.agent.attachment_extractors import xlsx_workbook
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit.test_file_tools import _context, _tool

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def format_workbook(*, one_row: bool = False) -> bytes:
    workbook = Workbook()
    for index in range(600):
        cell = workbook.active.cell(1 if one_row else index + 1, index + 1 if one_row else 1, 12.34)
        # Valid, individually short custom formats can still produce enormous
        # metadata when every definition is repeated on each return batch.
        cell.number_format = '0.00"' + "显示单位" * 35 + str(index) + '"'
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def many_merges_workbook() -> bytes:
    workbook = Workbook()
    workbook.active["A1"] = "merge-header"
    output = io.BytesIO()
    workbook.save(output)
    rewritten = io.BytesIO()
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as source, zipfile.ZipFile(rewritten, "w") as target:
        for entry in source.infolist():
            body = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(body)
                merges = ET.SubElement(root, namespace + "mergeCells", count="8192")
                for column in range(1, 16_384, 2):
                    ET.SubElement(merges, namespace + "mergeCell", ref=(
                        f"{get_column_letter(column)}1:{get_column_letter(column + 1)}1"
                    ))
                body = ET.tostring(root)
            # Stored ZIP isolates metadata volume from compression-ratio safety.
            target.writestr(entry.filename, body)
    return rewritten.getvalue()


class XlsxMetadataBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_many_formats_only_accompany_referenced_content_and_continue_losslessly(self) -> None:
        for one_row in (False, True):
            data = format_workbook(one_row=one_row)
            source = xlsx_workbook(data).sheets[0]
            original = "\n".join(source.rows)
            for window in (128_000, 256_000):
                with self.subTest(one_row=one_row, window=window):
                    ref = AttachmentRef(id="formats", name="formats.xlsx", mime=XLSX_MIME)
                    ctx = _context(ref)
                    tool = await _tool(ctx=ctx, context_window_tokens=window)
                    args = {"resource": {"type": "attachment", "file_id": ref.id}}
                    chunks, definitions, cursors = [], {}, set()
                    with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)) as download:
                        for batch in range(20):
                            result = await tool.call_func(args, ctx)
                            self.assertTrue(ReadingBudget(context_window_tokens=window).fits(result.model_dump(mode="json")))
                            prefix = "\n" if chunks and not args.get("char_offset") else ""
                            chunks.append(prefix + result.content)
                            definitions.update(result.metadata["unsupported_number_formats"])
                            self.assertGreater(result.returned_count, 0)
                            if not result.has_more:
                                break
                            cursor = (result.next_offset, result.next_char_offset)
                            self.assertNotIn(cursor, cursors)
                            cursors.add(cursor)
                            args.update(offset=result.next_offset, char_offset=result.next_char_offset)
                        else:
                            self.fail("Custom-format continuation did not finish")
                        download.assert_awaited_once()
                    self.assertGreater(batch, 0)
                    self.assertEqual("".join(chunks), original)
                    self.assertEqual(definitions, dict(source.unsupported_number_formats))

    async def test_explicit_single_row_does_not_return_other_format_definitions(self) -> None:
        data = format_workbook()
        ref = AttachmentRef(id="row", name="row.xlsx", mime=XLSX_MIME)
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)):
            result = await tool.call_func({
                "resource": {"type": "attachment", "file_id": ref.id}, "offset": 12, "limit": 1,
            }, ctx)
        self.assertEqual(set(result.metadata["unsupported_number_formats"]), {"F12"})
        self.assertEqual(result.content, "12.34 [F12]")

    async def test_many_merges_are_an_explicit_bounded_preview_not_a_body_read_failure(self) -> None:
        data = many_merges_workbook()
        for window in (128_000, 256_000):
            with self.subTest(window=window):
                ref = AttachmentRef(id="merges", name="merges.xlsx", mime=XLSX_MIME)
                ctx = _context(ref)
                tool = await _tool(ctx=ctx, context_window_tokens=window)
                with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)):
                    result = await tool.call_func({
                        "resource": {"type": "attachment", "file_id": ref.id}, "limit": 1,
                    }, ctx)
                self.assertEqual(result.content, "merge-header")
                self.assertFalse(result.has_more)
                self.assertEqual(result.metadata["merged_range_count"], 8192)
                self.assertEqual(result.metadata["merged_ranges_in_batch_count"], 8192)
                self.assertTrue(result.metadata["merged_ranges_truncated"])
                self.assertEqual(len(result.metadata["merged_ranges"]), 128)
                self.assertTrue(ReadingBudget(context_window_tokens=window).fits(result.model_dump(mode="json")))

    async def test_small_metadata_and_split_format_marker_keep_the_definition(self) -> None:
        workbook = Workbook()
        workbook.active["A1"] = 12.34
        workbook.active["A1"].number_format = '0.00"元"'
        workbook.active.merge_cells("A1:B1")
        output = io.BytesIO()
        workbook.save(output)
        source = xlsx_workbook(output.getvalue()).sheets[0]
        marker = re.search(r"\[F1\]", source.rows[0])
        ref = AttachmentRef(id="small", name="small.xlsx", mime=XLSX_MIME)
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=output.getvalue())):
            for char_offset in (0, marker.start() + 2):
                result = await tool.call_func({
                    "resource": {"type": "attachment", "file_id": ref.id}, "char_offset": char_offset,
                }, ctx)
                self.assertEqual(result.content, source.rows[0][char_offset:])
                self.assertEqual(result.metadata["unsupported_number_formats"], {"F1": '0.00"元"'})
                self.assertEqual(result.metadata["merged_ranges"], ["A1:B1"])
                self.assertFalse(result.metadata["merged_ranges_truncated"])

    async def test_abnormally_long_format_is_an_explicit_preview_not_a_body_read_failure(self) -> None:
        workbook = Workbook()
        workbook.active["A1"] = 12.34
        format_code = '0.00"' + "".join(f"{index}显示单位" for index in range(5000)) + '"'
        workbook.active["A1"].number_format = format_code
        output = io.BytesIO()
        workbook.save(output)
        for window in (128_000, 256_000):
            with self.subTest(window=window):
                ref = AttachmentRef(id="long-format", name="long-format.xlsx", mime=XLSX_MIME)
                ctx = _context(ref)
                tool = await _tool(ctx=ctx, context_window_tokens=window)
                with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=output.getvalue())):
                    result = await tool.call_func({
                        "resource": {"type": "attachment", "file_id": ref.id},
                    }, ctx)
                self.assertEqual(result.content, "12.34 [F1]")
                self.assertFalse(result.has_more)
                self.assertTrue(result.metadata["unsupported_number_formats_truncated"])
                self.assertEqual(result.metadata["unsupported_number_formats"], {"F1": format_code[:1024]})
                self.assertEqual(result.metadata["unsupported_number_format_lengths"], {"F1": len(format_code)})
                self.assertTrue(ReadingBudget(context_window_tokens=window).fits(result.model_dump(mode="json")))


if __name__ == "__main__":
    unittest.main()
