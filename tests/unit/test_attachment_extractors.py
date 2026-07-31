from __future__ import annotations

import io
import unittest
import zipfile

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
        self.assertEqual(page.next_offset, 2)

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
