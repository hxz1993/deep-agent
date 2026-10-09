"""Source-backed cross-format contract for the local reader, without OCR/network."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturnPart

from deep_agent.agent.attachment_extractors import docx_document, text_document, xlsx_workbook
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.files import toolset as file_tools
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit.test_file_tools import _context, _tool
from tests.unit.test_office_markdown import presentation_bytes
from deep_agent.files.office_markdown import pptx_body


FORMATS = (
    ("plain.txt", "text/plain", "编号000123\n金额9500.25\n中😀\n\nEND"),
    ("trace.log", "application/octet-stream", "2026-10-10 | INFO | 编号=000123\n错误=未核验"),
    ("cells.csv", "text/csv", '编号,金额,说明\n000123,9500.25,"中😀,引用"\n000124,,END'),
    ("facts.json", "application/json", '{"编号":"000123","金额":9500.25,"未知":null,"emoji":"中😀"}'),
    ("guide.md", "text/markdown", '# 标题\n\n**编号** `000123`\n[证据](https://example.invalid)\n| 金额 |\n|---|\n|9500.25|'),
    ("source.html", "text/html", '<article><h1>编号000123</h1><table><tr><td>9500.25</td><td></td></tr></table></article>'),
    ("source.xml", "application/xml", '<?xml version="1.0"?><contract id="000123"><amount>9500.25</amount><empty/></contract>'),
)


def saved(document) -> bytes:
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def table_workbook() -> bytes:
    workbook = Workbook()
    first = workbook.active
    first.title = "隐藏源"
    first.sheet_state = "hidden"
    first.append(["隐藏000123", "不应默认读取"])
    visible = workbook.create_sheet("BOM")
    visible.append(["编号", "金额", "说明"])
    visible.append(["000123", 9500.25, '中😀,逗号\n原始换行'])
    visible.append(["合并来源", None, None])
    visible.merge_cells("A3:C3")
    workbook.create_sheet("尾表").append(["END"])
    return saved(workbook)


class FileFormatRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def read(self, name, mime, data, **args):
        ref = AttachmentRef(id="source", name=name, mime=mime)
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)):
            return await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, **args}, ctx)

    async def test_all_declared_text_formats_keep_source_information(self):
        for name, mime, source in FORMATS:
            with self.subTest(name=name):
                result = await self.read(name, mime, source.encode())
                self.assertEqual(result.format, "text")
                self.assertEqual(result.unit, "line")
                self.assertFalse(result.has_more)
                if name.endswith(".json"):
                    self.assertEqual(json.loads(result.content), json.loads(source))
                    self.assertTrue(result.metadata["json_parse_ok"])
                else:
                    self.assertEqual(result.content, source)
                self.assertEqual(result.total_count, len(result.content.splitlines()))

    async def test_text_encoding_and_line_range_do_not_reinterpret_symbols(self):
        source = "编号000123\r\n\r\n金额9500.25\r\n末尾"
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            with self.subTest(encoding=encoding):
                result = await self.read("source.txt", "text/plain", source.encode(encoding), offset=2, limit=2)
                self.assertEqual(result.content, "\n金额9500.25")
                self.assertEqual((result.offset, result.returned_count, result.total_count), (2, 2, 4))
                self.assertEqual((result.next_offset, result.next_char_offset), (4, 0))

    async def test_invalid_json_is_returned_as_source_not_repaired(self):
        raw = '{"编号":"000123", "金额":}'
        result = await self.read("facts.json", "application/json", raw.encode())
        self.assertEqual(result.content, raw)
        self.assertFalse(result.metadata["json_parse_ok"])

    async def test_explicit_sheet_default_hidden_order_and_source_range(self):
        raw = table_workbook()
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        default = await self.read("source.xlsx", mime, raw)
        hidden = await self.read("source.xlsx", mime, raw, sheet="隐藏源")
        chosen = await self.read("source.xlsx", mime, raw, sheet="BOM", offset=2, limit=1)
        self.assertIn("000123", default.content)
        self.assertNotIn("隐藏000123", default.content)
        self.assertIn("隐藏000123", hidden.content)
        decoded = next(csv.reader(io.StringIO(chosen.content, newline="")))
        self.assertEqual(decoded, ["000123", "9500.25", '中😀,逗号\n原始换行'])
        self.assertEqual((chosen.offset, chosen.returned_count, chosen.total_count), (2, 1, 3))
        self.assertEqual(default.metadata["merged_ranges"], ["A3:C3"])
        self.assertEqual(chosen.metadata["merged_ranges"], [])

    async def test_style_and_real_wide_cells_keep_dynamic_row_widths(self):
        workbook = Workbook()
        workbook.active["A1"] = "FIRST"
        workbook.active["C3"] = "THIRD"
        workbook.active["XFD5"] = "RIGHT"
        for row in (1, 2, 3, 20):
            workbook.active.cell(row, 16384).font = Font(bold=True)
        calls = []
        original = ReadOnlyWorksheet.iter_rows
        def traced(worksheet, *args, **kwargs):
            calls.append(dict(kwargs))
            return original(worksheet, *args, **kwargs)
        with patch.object(ReadOnlyWorksheet, "iter_rows", traced):
            result = await self.read("styled.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", saved(workbook))
        self.assertEqual(result.content, "FIRST\n\n,,THIRD\n\n" + "," * 16383 + "RIGHT")
        self.assertEqual(result.total_count, 5)
        self.assertEqual(result.metadata["sheets"][0]["columns"], 16384)
        self.assertEqual(calls, [{"max_row": 5}])

    async def test_unknown_sheet_is_not_silently_replaced(self):
        with self.assertRaises(ModelRetry):
            await self.read("source.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", table_workbook(), sheet="不存在")

    async def test_sheet_parameter_does_not_change_non_spreadsheet_reading(self):
        with self.assertRaises(ModelRetry):
            await self.read("source.txt", "text/plain", b"source", sheet="BOM")

    async def test_long_content_reconstructs_all_formats_under_both_wire_budgets(self):
        long = "".join(f"源字符中😀|={index / 17:.8f}," for index in range(14000))
        word = Document()
        word.add_paragraph("START")
        word.add_paragraph(long)
        word.add_paragraph("END")
        workbook = Workbook()
        # Excel truncates cells beyond 32767; use legal cell values and a wide
        # row to exercise one source item exceeding the Token budget.
        workbook.active.append(["START"])
        workbook.active.append(["".join(f"中😀{index:05d}" for index in range(4000)) for _ in range(4)])
        workbook.active.append(["END"])
        slides = presentation_bytes(long=True)
        cases = [
            ("source.md", "text/markdown", ("START\n" + long + "\n\nEND").encode(), None),
            ("source.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", saved(word), lambda data: docx_document(data).blocks),
            ("source.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", saved(workbook), lambda data: xlsx_workbook(data).sheets[0].rows),
            ("source.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", slides, lambda data: pptx_body(data)[0]),
        ]
        estimator = TokenEstimator.conservative()
        for name, mime, data, parser in cases:
            expected = "\n".join(parser(data) if parser else text_document(data, name=name, mime=mime)[0])
            for window, budget in ((128000, 24000), (256000, 48000)):
                with self.subTest(name=name, window=window):
                    ref = AttachmentRef(id="long", name=name, mime=mime)
                    ctx = _context(ref)
                    tool = await _tool(ctx=ctx, context_window_tokens=window)
                    arguments = {"resource": {"type": "attachment", "file_id": ref.id}}
                    chunks, previous, cursors = [], None, set()
                    with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)) as download:
                        for _ in range(100):
                            page = await tool.call_func(arguments, ctx)
                            envelope = page.model_dump(mode="json")
                            self.assertLessEqual(estimator.text(envelope), budget)
                            self.assertLessEqual(estimator.text(ToolReturnPart("read_file", envelope).model_response_str()), budget)
                            chunks.append(("" if previous is None or previous.next_char_offset else "\n") + page.content)
                            if not page.has_more:
                                break
                            cursor = (page.next_offset, page.next_char_offset)
                            self.assertNotIn(cursor, cursors)
                            cursors.add(cursor)
                            arguments.update(offset=page.next_offset, char_offset=page.next_char_offset)
                            previous = page
                        else:
                            self.fail("Continuation loop")
                        download.assert_awaited_once()
                    self.assertEqual("".join(chunks), expected)

    async def test_docx_complex_table_keeps_source_words_and_declares_structure_limit(self):
        document = Document()
        outer = document.add_table(rows=3, cols=3)
        outer.cell(0, 0).text = "MERGED-HEADER"
        outer.cell(0, 0).merge(outer.cell(0, 2))
        outer.cell(1, 0).text = "OUTER-BEFORE"
        inner = outer.cell(1, 0).add_table(rows=2, cols=2)
        values = (("INNER-A", "INNER-B"), ("INNER-C", "INNER-D"))
        for row, source in zip(inner.rows, values):
            for cell, value in zip(row.cells, source):
                cell.text = value
        outer.cell(1, 0).add_paragraph("OUTER-AFTER")
        outer.cell(1, 1).text = "LINE-ONE\nLINE-TWO|PIPE"
        outer.cell(2, 0).text = "END"
        result = await self.read("tables.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", saved(document))
        for value in ("MERGED-HEADER", "OUTER-BEFORE", "INNER-A", "INNER-B", "INNER-C", "INNER-D", "OUTER-AFTER", "LINE-ONE", "LINE-TWO", "END"):
            self.assertIn(value, result.content)
        self.assertLess(result.content.index("OUTER-BEFORE"), result.content.index("INNER-A"))
        self.assertLess(result.content.index("INNER-D"), result.content.index("OUTER-AFTER"))
        self.assertTrue(any("合并" in warning for warning in result.metadata["warnings"]))
        # Presence/order is the contract. Markdown nested-table geometry and
        # original within-cell line breaks are deliberately not claimed exact.

    async def test_source_authorization_is_rechecked_even_after_cache_hit(self):
        ref = AttachmentRef(id="allowed", name="source.md", mime="text/markdown")
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        arguments = {"resource": {"type": "attachment", "file_id": ref.id}}
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=b"SOURCE")) as download:
            first = await tool.call_func(arguments, ctx)
            self.assertEqual(first.content, "SOURCE")
            del ctx.deps.attachment_by_id[ref.id]
            with self.assertRaises(ModelRetry):
                await tool.call_func(arguments, ctx)
            download.assert_awaited_once()

    async def test_concurrent_reads_share_download_and_parse_but_not_cursor(self):
        ref = AttachmentRef(id="source", name="source.md", mime="text/markdown")
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        release = asyncio.Event()
        async def load(*args, **kwargs):
            await release.wait()
            return b"FIRST\nSECOND\nTHIRD"
        download = AsyncMock(side_effect=load)
        parser = Mock(wraps=file_tools.text_document)
        with patch("deep_agent.files.toolset._download_file", download), patch("deep_agent.files.toolset.text_document", parser):
            tasks = [asyncio.create_task(tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, "offset": offset, "limit": 1}, ctx)) for offset in (1, 3)]
            await asyncio.sleep(0)
            release.set()
            first, third = await asyncio.gather(*tasks)
        self.assertEqual((first.content, third.content), ("FIRST", "THIRD"))
        download.assert_awaited_once()
        parser.assert_called_once()

    async def test_cancelled_download_is_removed_and_next_read_retries(self):
        ref = AttachmentRef(id="source", name="source.txt", mime="text/plain")
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set()
            await asyncio.Future()
        arguments = {"resource": {"type": "attachment", "file_id": ref.id}}
        with patch("deep_agent.files.toolset._download_file", AsyncMock(side_effect=blocked)):
            task = asyncio.create_task(tool.call_func(arguments, ctx))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(ctx.deps.file_load_tasks)
        self.assertFalse(ctx.deps.file_bytes)
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=b"RETRY")):
            self.assertEqual((await tool.call_func(arguments, ctx)).content, "RETRY")


if __name__ == "__main__":
    unittest.main()
