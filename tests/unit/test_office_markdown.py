from __future__ import annotations

import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from unittest.mock import AsyncMock, patch

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pptx import Presentation
from pptx.util import Inches

from deep_agent.agent.attachment_extractors import docx_document
from deep_agent.files.office_markdown import pptx_body
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit.test_file_tools import _context, _tool
from deep_agent.agent.context.estimator import TokenEstimator


def rich_word() -> bytes:
    document = Document()
    document.sections[0].header.paragraphs[0].text = "HEADER-00123"
    document.sections[0].footer.paragraphs[0].text = "FOOTER-EVIDENCE"
    document.add_heading("LEASE-TITLE", 1)
    paragraph = document.add_paragraph("BODY-9500.25")
    document.add_comment(paragraph.runs, text="COMMENT-EVIDENCE", author="Fixture")
    document.add_paragraph("FIRST-ITEM", style="List Number")
    document.add_paragraph("SECOND-ITEM", style="List Number")
    paragraph = document.add_paragraph()
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), paragraph.part.relate_to(
        "https://example.invalid/contract?id=00123", RT.HYPERLINK, is_external=True,
    ))
    run, text = OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "LINK-EVIDENCE"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    paragraph = document.add_paragraph("Footnote reference ")
    reference = OxmlElement("w:footnoteReference")
    reference.set(qn("w:id"), "1")
    paragraph.add_run()._r.append(reference)
    output = io.BytesIO()
    document.save(output)
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    rels = ET.fromstring(parts["word/_rels/document.xml.rels"])
    ET.SubElement(rels, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                  Id="rIdFixtureNote", Type=RT.FOOTNOTES, Target="footnotes.xml")
    parts["word/_rels/document.xml.rels"] = ET.tostring(rels)
    content_types = ET.fromstring(parts["[Content_Types].xml"])
    ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                  PartName="/word/footnotes.xml",
                  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml")
    parts["[Content_Types].xml"] = ET.tostring(content_types)
    parts["word/footnotes.xml"] = b'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>FOOTNOTE-EVIDENCE</w:t></w:r></w:p></w:footnote></w:footnotes>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in parts.items():
            archive.writestr(name, raw)
    return output.getvalue()


def presentation_bytes(long: bool = False) -> bytes:
    presentation = Presentation()
    for index in range(2):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = f"SLIDE-{index + 1}"
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
        box.text_frame.text = "中😀-END" * 12_000 if long else f"BODY-{index + 1}"
        slide.notes_slide.notes_text_frame.text = f"NOTES-{index + 1}"
        table = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(5), Inches(1)).table
        for row, values in zip(table.rows, (("编号", "金额"), ("000123", "9500.25"))):
            for cell, value in zip(row.cells, values):
                cell.text = value
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


class OfficeMarkdownTests(unittest.TestCase):
    def test_word_preserves_both_markdown_body_and_native_supplements(self):
        # No network is needed for external hyperlink conversion.
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            result = docx_document(rich_word())
        body = "\n".join(result.blocks)
        for value in ("HEADER-00123", "FOOTER-EVIDENCE", "LEASE-TITLE", "BODY-9500.25",
                      "COMMENT-EVIDENCE", "FIRST-ITEM", "SECOND-ITEM", "LINK-EVIDENCE",
                      "https://example.invalid/contract?id=00123", "FOOTNOTE-EVIDENCE"):
            self.assertIn(value, body)
        self.assertLess(body.index("HEADER-00123"), body.index("LEASE-TITLE"))
        self.assertLess(body.index("BODY-9500.25"), body.index("FOOTER-EVIDENCE"))
        self.assertLess(body.index("FOOTER-EVIDENCE"), body.index("COMMENT-EVIDENCE"))
        self.assertEqual(result.metadata["engine"], "markitdown")
        self.assertEqual(result.metadata["comments"], 1)

    def test_pptx_keeps_slide_numbers_text_tables_notes_without_llm(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            blocks, metadata = pptx_body(presentation_bytes())
        body = "\n".join(blocks)
        for value in ("[幻灯片 1]", "[幻灯片 2]", "BODY-1", "BODY-2", "NOTES-1", "NOTES-2", "000123", "9500.25"):
            self.assertIn(value, body)
        self.assertEqual(metadata["slides"], 2)
        self.assertNotIn("base64,", body)

    def test_pptx_images_keep_alt_without_inventing_readable_paths(self):
        from PIL import Image

        presentation = Presentation()
        stream = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
        for _ in range(2):
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            stream.seek(0)
            shape = slide.shapes.add_picture(stream, Inches(1), Inches(1))
            shape.name = "SamePicture"
            shape._element._nvXxPr.cNvPr.set("descr", "现场照片说明")
        data = io.BytesIO()
        presentation.save(data)
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            blocks, metadata = pptx_body(data.getvalue())
        content = "\n".join(blocks)
        self.assertEqual(content.count("现场照片说明"), 2)
        self.assertEqual(content.count("非可读取资源"), 2)
        self.assertNotIn("SamePicture.jpg", content)
        self.assertNotIn("data:", content)
        self.assertEqual(metadata["embedded_image_parts"], 1)


class OfficeToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_pptx_actual_tool_continues_fixed_content_and_caches(self):
        data = presentation_bytes(long=True)
        original = "\n".join(pptx_body(data)[0])
        for window in (128_000, 256_000):
            ref = AttachmentRef(id="slides", name="slides.pptx", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation")
            context = _context(ref)
            tool = await _tool(ctx=context, context_window_tokens=window)
            args = {"resource": {"type": "attachment", "file_id": ref.id}}
            chunks, prior = [], None
            with patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=data)) as download:
                for _ in range(30):
                    result = await tool.call_func(args, context)
                    self.assertLessEqual(TokenEstimator.conservative().text(result.model_dump(mode="json")), 48_000 if window >= 256_000 else 24_000)
                    chunks.append(("" if prior and prior.next_char_offset else "\n") + result.content)
                    if not result.has_more:
                        break
                    self.assertNotEqual((result.next_offset, result.next_char_offset), (args.get("offset"), args.get("char_offset")))
                    args.update(offset=result.next_offset, char_offset=result.next_char_offset)
                    prior = result
                else:
                    self.fail("PPTX continuation did not reach EOF")
                download.assert_awaited_once()
            self.assertEqual("".join(chunks)[1:], original)
            self.assertEqual(result.format, "pptx")
