from __future__ import annotations

import io
import random
import unittest
import zipfile
from unittest.mock import patch
from xml.etree import ElementTree

from docx import Document
from openpyxl import Workbook

from deep_agent.agent.attachment_extractors import (
    AttachmentParseError,
    decode_text_document,
    docx_document,
    paginate_items,
    select_xlsx_sheet,
    text_document,
    validate_office_archive,
    xlsx_workbook,
)


class AttachmentExtractorTests(unittest.TestCase):
    def test_xlsx_long_cells_and_rows_are_preserved_across_pages(self) -> None:
        randomizer = random.Random(0)
        cells = ["".join(randomizer.choices("abcdefghijklmnopqrstuvwxyz", k=30_000)) for _ in range(2)]
        workbook = Workbook()
        workbook.active.append(cells)
        raw = io.BytesIO()
        workbook.save(raw)
        sheet = xlsx_workbook(raw.getvalue()).sheets[0]
        original = ",".join(cells)
        self.assertEqual(sheet.rows[0], original)
        self.assertEqual(sheet.headers, tuple(cells))
        first = paginate_items(sheet.rows, total_count=1, offset=1, limit=1)
        last = paginate_items(sheet.rows, total_count=1, offset=first.next_offset,
                              limit=1, char_offset=first.next_char_offset)
        self.assertEqual(first.content + last.content, original)
        self.assertEqual(first.next_char_offset, 50_000)
        self.assertFalse(last.has_more)

    def test_xlsx_text_budget_includes_separators_and_all_sheets(self) -> None:
        workbook = Workbook()
        workbook.active.append(["abc", "de"])
        workbook.active.append(["f"])
        workbook.create_sheet("second").append(["gh"])
        raw = io.BytesIO()
        workbook.save(raw)
        # "abc,de\nf" (8) + "gh" (2) = 10 rendered characters.
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 10):
            parsed = xlsx_workbook(raw.getvalue())
            self.assertEqual(parsed.sheets[1].rows, ("gh",))
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 9):
            with self.assertRaisesRegex(AttachmentParseError, "字符安全上限"):
                xlsx_workbook(raw.getvalue())

    def test_xlsx_repeated_shared_strings_count_towards_expansion_budget(self) -> None:
        workbook = Workbook()
        workbook.active.append(["shared", "shared", "shared"])
        raw = io.BytesIO()
        workbook.save(raw)
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        payload = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as source, zipfile.ZipFile(payload, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    root = ElementTree.fromstring(data)
                    for cell in root.iter(f"{{{namespace}}}c"):
                        cell.set("t", "s")
                        for child in list(cell):
                            cell.remove(child)
                        ElementTree.SubElement(cell, f"{{{namespace}}}v").text = "0"
                    data = ElementTree.tostring(root)
                elif name == "[Content_Types].xml":
                    root = ElementTree.fromstring(data)
                    ElementTree.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
                        "PartName": "/xl/sharedStrings.xml",
                        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
                    })
                    data = ElementTree.tostring(root)
                target.writestr(name, data)
            target.writestr("xl/sharedStrings.xml", (
                f'<sst xmlns="{namespace}" count="3" uniqueCount="1"><si><t>shared</t></si></sst>'
            ))
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 20):
            self.assertEqual(xlsx_workbook(payload.getvalue()).sheets[0].rows, ("shared,shared,shared",))
        with patch("deep_agent.agent.attachment_extractors.MAX_XLSX_TEXT_CHARS", 10):
            with self.assertRaisesRegex(AttachmentParseError, "字符安全上限"):
                xlsx_workbook(payload.getvalue())

    def test_text_decoding_supports_utf16_gb18030_and_json(self) -> None:
        utf16, encoding = decode_text_document("中文".encode("utf-16"))
        self.assertEqual(utf16, "中文")
        self.assertEqual(encoding, "utf-16")

        gbk, encoding = decode_text_document("中文".encode("gb18030"))
        self.assertEqual(gbk, "中文")
        self.assertEqual(encoding, "gb18030")

        lines, metadata = text_document(
            b'{"name":"demo","items":[1,2]}',
            name="demo.json",
            mime="application/json",
        )
        self.assertGreater(len(lines), 1)
        self.assertTrue(metadata["json_parse_ok"])
        self.assertIn('"name": "demo"', "\n".join(lines))

    def test_pagination_caps_long_item_and_reports_more(self) -> None:
        page = paginate_items(
            ["a" * 100, "second"],
            total_count=2,
            offset=1,
            limit=2,
            max_chars=40,
        )
        self.assertEqual(page.returned_count, 1)
        self.assertEqual(page.truncated_by, "item_size")
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_offset, 1)
        self.assertEqual(page.next_char_offset, 40)
        self.assertEqual(page.content, "a" * 40)

    def test_long_final_item_continues_until_every_character_is_read(self) -> None:
        original = "中文🙂" * 40_000
        fragments = []
        char_offset = 0
        for _ in range(4):
            page = paginate_items(
                [original], total_count=1, offset=1, limit=1, char_offset=char_offset,
            )
            fragments.append(page.content)
            self.assertLessEqual(len(page.content), 50_000)
            if not page.has_more:
                self.assertIsNone(page.next_offset)
                self.assertIsNone(page.next_char_offset)
                break
            self.assertEqual(page.next_offset, 1)
            self.assertGreater(page.next_char_offset, char_offset)
            char_offset = page.next_char_offset
        else:
            self.fail("Continuation did not terminate")
        self.assertEqual("".join(fragments), original)

    def test_nonfitting_next_item_is_deferred_whole(self) -> None:
        page = paginate_items(
            ["first", "second"], total_count=2, offset=1, limit=2, max_chars=10,
        )
        self.assertEqual(page.content, "first")
        self.assertEqual(page.returned_count, 1)
        self.assertEqual((page.next_offset, page.next_char_offset), (2, 0))
        self.assertEqual(page.truncated_by, "output_size")

    def test_continuation_can_finish_item_and_read_following_items(self) -> None:
        page = paginate_items(
            ["abcdef", "second"], total_count=2, offset=1, limit=2,
            char_offset=4, max_chars=10,
        )
        self.assertEqual(page.content, "ef\nsecond")
        self.assertEqual(page.returned_count, 2)
        self.assertFalse(page.has_more)

    def test_exact_character_cap_advances_to_next_item(self) -> None:
        page = paginate_items(
            ["abc", ""], total_count=2, offset=1, limit=2, max_chars=3,
        )
        self.assertEqual(page.content, "abc")
        self.assertEqual((page.next_offset, page.next_char_offset), (2, 0))
        last = paginate_items([""], total_count=2, offset=2, limit=2, max_chars=3)
        self.assertEqual(last.returned_count, 1)
        self.assertFalse(last.has_more)

    def test_empty_document_and_beyond_end_keep_empty_page_compatibility(self) -> None:
        for total, offset in ((0, 1), (2, 3)):
            page = paginate_items([], total_count=total, offset=offset, limit=2)
            self.assertEqual(page.content, "")
            self.assertFalse(page.has_more)
            self.assertIsNone(page.next_char_offset)

    def test_invalid_character_cursor_is_rejected(self) -> None:
        for items, char_offset in ((["abc"], -1), (["abc"], 3), (["abc"], 4),
                                   ([""], 1), ([], 1), (["abc"], None),
                                   (["abc"], 1.5), (["abc"], True)):
            with self.subTest(items=items, char_offset=char_offset):
                with self.assertRaises(AttachmentParseError):
                    paginate_items(items, total_count=len(items), offset=1, limit=2,
                                   char_offset=char_offset)

    def test_docx_preserves_headers_tables_footers_and_comments(self) -> None:
        document = Document()
        document.sections[0].header.paragraphs[0].text = "页眉内容"
        document.add_paragraph("正文第一段")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "列A"
        table.cell(0, 1).text = "列B"
        document.sections[0].footer.paragraphs[0].text = "页脚内容"
        paragraph = document.add_paragraph("被批注文本")
        document.add_comment(paragraph.runs, text="批注内容", author="测试者")
        output = io.BytesIO()
        document.save(output)

        parsed = docx_document(output.getvalue())
        combined = "\n".join(parsed.blocks)
        self.assertLess(combined.index("页眉内容"), combined.index("正文第一段"))
        self.assertLess(combined.index("正文第一段"), combined.index("页脚内容"))
        self.assertLess(combined.index("页脚内容"), combined.index("批注内容"))
        self.assertIn("| 列A | 列B |", combined)
        self.assertEqual(parsed.metadata["comments"], 1)
        self.assertEqual(parsed.metadata["body_tables"], 1)

    def test_xlsx_selects_first_visible_and_case_insensitive_sheet(self) -> None:
        workbook = Workbook()
        workbook.active.title = "隐藏"
        workbook.active.sheet_state = "hidden"
        visible = workbook.create_sheet("数据")
        visible.append(["名称", "值"])
        visible.append(["甲", 1])
        output = io.BytesIO()
        workbook.save(output)

        parsed = xlsx_workbook(output.getvalue())
        selected = select_xlsx_sheet(parsed, None)
        self.assertEqual(selected.name, "数据")
        self.assertEqual(select_xlsx_sheet(parsed, "数据").rows[1], "甲,1")

    def test_office_archive_rejects_path_traversal(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../evil.xml", "<x/>")
        with self.assertRaises(AttachmentParseError):
            validate_office_archive(output.getvalue())


if __name__ == "__main__":
    unittest.main()
