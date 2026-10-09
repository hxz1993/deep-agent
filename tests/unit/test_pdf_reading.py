from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, ArrayObject, NumberObject
from pydantic_ai import ModelRetry

from deep_agent.agent.attachment_extractors import AttachmentParseError
from deep_agent.files.pdf_reading import pdf_document
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit.test_file_tools import _context, _tool, _entry, _resolved_skill


def pdf_bytes(texts: tuple[str, ...] = ("PAGE-ONE 9500.25", "", "PAGE-THREE 000123"), *, password=None) -> bytes:
    output = io.BytesIO()
    with PdfWriter() as writer:
        for text in texts:
            page = writer.add_blank_page(width=600, height=800)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            if text:
                stream = DecodedStreamObject()
                escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                stream.set_data(f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("ascii"))
                page[NameObject("/Contents")] = stream
        if password is not None:
            writer.encrypt(password)
        writer.write(output)
    return output.getvalue()


class PdfConversionTests(unittest.TestCase):
    @staticmethod
    def form_pdf(*, text="FORM-TEXT", nested=False, cycle=False, font_mapping=None):
        """Real PDF fixture with reused Forms, without any external resources."""
        n = NameObject
        with PdfWriter() as writer:
            font = DictionaryObject({n("/Type"): n("/Font"), n("/Subtype"): n("/Type1"), n("/BaseFont"): n("/Helvetica")})
            if font_mapping is not None:
                mapping = DecodedStreamObject()
                mapping.set_data(font_mapping)
                font[n("/ToUnicode")] = writer._add_object(mapping)
            fonts = DictionaryObject({n("/Font"): DictionaryObject({n("/F1"): writer._add_object(font)})})
            inner = DecodedStreamObject()
            inner.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
            inner[n("/Type")] = n("/XObject")
            inner[n("/Subtype")] = n("/Form")
            inner[n("/BBox")] = ArrayObject([NumberObject(x) for x in (0, 0, 600, 800)])
            inner[n("/Resources")] = fonts
            inner_ref = writer._add_object(inner)
            if cycle:
                inner.set_data(b"/Inner Do")
                fonts[n("/XObject")] = DictionaryObject({n("/Inner"): inner_ref})
            form_ref = inner_ref
            if nested:
                outer = DecodedStreamObject()
                outer.set_data(b"/Inner Do")
                outer[n("/Type")] = n("/XObject")
                outer[n("/Subtype")] = n("/Form")
                outer[n("/BBox")] = inner[n("/BBox")]
                outer[n("/Resources")] = DictionaryObject({n("/XObject"): DictionaryObject({n("/Inner"): inner_ref})})
                form_ref = writer._add_object(outer)
            for _ in range(2):
                page = writer.add_blank_page(width=600, height=800)
                page[n("/Resources")] = DictionaryObject({n("/XObject"): DictionaryObject({n("/Form"): form_ref})})
                content = DecodedStreamObject()
                content.set_data(b"/Form Do")
                page[n("/Contents")] = writer._add_object(content)
            output = io.BytesIO()
            writer.write(output)
        return output.getvalue()

    def test_shared_nested_forms_preserve_text_and_count_expansion_once(self):
        with patch("deep_agent.agent.attachment_extractors.MAX_OFFICE_EXPANDED_BYTES", 90):
            pages, metadata = pdf_document(self.form_pdf(nested=True))
        self.assertEqual(len(pages), 2)
        self.assertTrue(all("FORM-TEXT" in page for page in pages))
        self.assertEqual(metadata["pages_without_text"], [])

    def test_nested_form_and_font_mapping_streams_obey_existing_limit(self):
        for data in (self.form_pdf(text="A" * 200, nested=True), self.form_pdf(font_mapping=b" " * 200)):
            with self.subTest(), patch("deep_agent.agent.attachment_extractors.MAX_OFFICE_MEMBER_BYTES", 100), self.assertRaisesRegex(AttachmentParseError, "安全上限"):
                pdf_document(data)

    def test_total_expansion_limit_includes_nested_forms(self):
        with patch("deep_agent.agent.attachment_extractors.MAX_OFFICE_EXPANDED_BYTES", 40), self.assertRaisesRegex(AttachmentParseError, "安全上限"):
            pdf_document(self.form_pdf(nested=True))

    def test_invoked_form_cycle_rejected_before_conversion(self):
        with self.assertRaisesRegex(AttachmentParseError, "循环引用"):
            pdf_document(self.form_pdf(cycle=True))

    def test_real_text_and_empty_pages_keep_physical_numbers_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            pages, metadata = pdf_document(pdf_bytes())
        self.assertEqual(len(pages), 3)
        self.assertIn("PAGE-ONE 9500.25", pages[0])
        self.assertIn("[PDF第2页]", pages[1])
        self.assertIn("未提取到文本，不代表原页无内容", pages[1])
        self.assertIn("PAGE-THREE 000123", pages[2])
        self.assertEqual(metadata["pages_without_text"], [2])
        self.assertEqual(metadata["physical_pages"], 3)

    def test_encrypted_damaged_and_empty_pdfs_are_explicit_errors(self):
        output = io.BytesIO()
        PdfWriter().write(output)
        for data, match in ((b"bad PDF", "PDF"), (pdf_bytes(password="private"), "加密"), (output.getvalue(), "没有")):
            with self.subTest(match=match), self.assertRaisesRegex(AttachmentParseError, match):
                pdf_document(data)

    def test_content_stream_safety_before_converter(self):
        with patch("deep_agent.agent.attachment_extractors.MAX_OFFICE_MEMBER_BYTES", 5), self.assertRaisesRegex(AttachmentParseError, "安全上限"):
            pdf_document(pdf_bytes())


class PdfToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_range_empty_page_and_authorization_rechecked_on_cache_hit(self):
        ref = AttachmentRef(id="pdf", name="contract.pdf", mime="application/pdf")
        ctx = _context(ref)
        tool = await _tool(ctx=ctx)
        with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=pdf_bytes())) as download:
            result = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, "offset": 2, "limit": 1}, ctx)
            self.assertEqual((result.format, result.unit, result.offset, result.returned_count, result.total_count), ("pdf", "page", 2, 1, 3))
            self.assertIn("[PDF第2页]", result.content)
            self.assertNotIn("PAGE-ONE", result.content)
            self.assertEqual(result.next_offset, 3)
            all_pages = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, ctx)
            self.assertEqual(all_pages.returned_count, 3)
            self.assertFalse(all_pages.has_more)
            download.assert_awaited_once()
            ctx.deps.attachment_by_id.clear()
            with self.assertRaises(ModelRetry):
                await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, ctx)

    async def test_long_unicode_page_continuation_reconstructs_fixed_content_and_fits_budget(self):
        source = ("[PDF第1页]\n" + "中😀金额000123|" * 18_000, "[PDF第2页]\nEND")
        for window in (128_000, 256_000, None):
            ref = AttachmentRef(id="long", name="long.pdf", mime="application/pdf")
            ctx = _context(ref)
            tool = await _tool(ctx=ctx, context_window_tokens=window)
            args = {"resource": {"type": "attachment", "file_id": ref.id}}
            chunks, prior, seen = [], None, set()
            with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=b"PDF")), patch("deep_agent.files.toolset.pdf_document", return_value=(source, {"physical_pages": 2})) as parse:
                for _ in range(40):
                    result = await tool.call_func(args, ctx)
                    self.assertTrue(ReadingBudget(context_window_tokens=window).fits(result.model_dump(mode="json")))
                    chunks.append(("" if prior and prior.next_char_offset else "\n") + result.content)
                    if not result.has_more:
                        break
                    cursor = (result.next_offset, result.next_char_offset)
                    self.assertNotIn(cursor, seen)
                    seen.add(cursor)
                    args.update(offset=cursor[0], char_offset=cursor[1])
                    prior = result
                else:
                    self.fail("PDF cursor failed to advance")
                parse.assert_called_once()
            self.assertEqual("".join(chunks)[1:], "\n".join(source))

    async def test_authorized_skill_pdf_read_and_hash_check(self):
        with tempfile.TemporaryDirectory(prefix="TMP to delete pdf-skill-") as folder:
            root = Path(folder)
            path = root / "references" / "example.pdf"
            path.parent.mkdir()
            path.write_bytes(pdf_bytes())
            resolved = _resolved_skill(root, [_entry("references/example.pdf", category="reference", mime="application/pdf")])
            ctx = _context()
            tool = await _tool({"demo": resolved}, ctx=ctx)
            result = await tool.call_func({"resource": {"type": "skill", "skill_code": "demo", "path": "references/example.pdf"}}, ctx)
            self.assertEqual(result.unit, "page")
            self.assertIn("PAGE-ONE", result.content)
