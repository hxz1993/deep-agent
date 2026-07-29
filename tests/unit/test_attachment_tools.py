from __future__ import annotations

import io
import unittest
from unittest.mock import AsyncMock, patch

from docx import Document
from openpyxl import Workbook
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.attachments import build_attachment_capability
from deep_agent.schemas.attachment import AttachmentRef


def _context(
    *refs: AttachmentRef,
    modalities: tuple[str, ...] = (),
) -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(
            session_id="attachment-test",
            input_modalities=modalities,
            attachment_by_id={ref.id: ref for ref in refs},
        ),
        model=TestModel(),
        usage=RunUsage(),
    )


class AttachmentToolTests(unittest.IsolatedAsyncioTestCase):
    async def _tool(self, name: str):
        capability = build_attachment_capability()
        tools = await capability.toolset.get_tools(_context())
        return tools[name]

    async def test_capability_is_opt_in_with_three_tools(self) -> None:
        capability = build_attachment_capability()
        self.assertFalse(capability.default_enabled)
        self.assertEqual(
            {item.name for item in capability.tools},
            {"read_text_attachment", "read_docx", "read_xlsx"},
        )

    async def test_unknown_file_id_is_rejected_before_download(self) -> None:
        tool = await self._tool("read_text_attachment")
        with patch(
            "deep_agent.capabilities.attachments.builtin._download_file",
            new_callable=AsyncMock,
        ) as download:
            with self.assertRaises(ModelRetry):
                await tool.call_func({"file_id": "outside-session"}, _context())
            download.assert_not_awaited()

    async def test_text_default_and_followup_use_offset_limit(self) -> None:
        ref = AttachmentRef(id="text-1", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await self._tool("read_text_attachment")
        with patch(
            "deep_agent.capabilities.attachments.builtin._download_file",
            new_callable=AsyncMock,
            return_value=b"one\ntwo\nthree\nfour",
        ) as download:
            first = await tool.call_func(
                {"file_id": ref.id, "limit": 2},
                context,
            )
            second = await tool.call_func(
                {"file_id": ref.id, "offset": first.next_offset, "limit": 2},
                context,
            )
        self.assertEqual(first.content, "one\ntwo")
        self.assertEqual(first.next_offset, 3)
        self.assertTrue(first.has_more)
        self.assertEqual(second.content, "three\nfour")
        self.assertFalse(second.has_more)
        download.assert_awaited_once()

    async def test_text_limit_is_clamped_and_invalid_range_retries(self) -> None:
        ref = AttachmentRef(id="text-2", name="note.txt", mime="text/plain")
        tool = await self._tool("read_text_attachment")
        context = _context(ref)
        with patch(
            "deep_agent.capabilities.attachments.builtin._download_file",
            new_callable=AsyncMock,
            return_value=b"hello",
        ):
            result = await tool.call_func(
                {"file_id": ref.id, "limit": 99_999},
                context,
            )
            self.assertEqual(result.limit, 2_000)
            with self.assertRaises(ModelRetry):
                await tool.call_func(
                    {"file_id": ref.id, "offset": 0},
                    context,
                )

    async def test_docx_reads_blocks_and_only_returns_images_on_first_page(self) -> None:
        from PIL import Image

        document = Document()
        document.add_paragraph("正文")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "A"
        table.cell(0, 1).text = "B"
        image = Image.new("RGB", (8, 8), "red")
        image_output = io.BytesIO()
        image.save(image_output, format="PNG")
        document.add_paragraph().add_run().add_picture(
            io.BytesIO(image_output.getvalue())
        )
        output = io.BytesIO()
        document.save(output)
        ref = AttachmentRef(
            id="docx-1",
            name="example.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        tool = await self._tool("read_docx")
        context = _context(ref, modalities=("text", "image"))
        with patch(
            "deep_agent.capabilities.attachments.builtin._download_file",
            new_callable=AsyncMock,
            return_value=output.getvalue(),
        ) as download:
            first = await tool.call_func(
                {"file_id": ref.id, "limit": 1},
                context,
            )
            second = await tool.call_func(
                {"file_id": ref.id, "offset": 2, "limit": 10},
                context,
            )
        self.assertIsInstance(first, ToolReturn)
        assert isinstance(first, ToolReturn)
        self.assertTrue(first.return_value.metadata["embedded_images_included"])
        self.assertFalse(second.metadata["embedded_images_included"])
        self.assertIn("| A | B |", second.content)
        download.assert_awaited_once()

    async def test_xlsx_defaults_to_first_visible_sheet_and_paginates(self) -> None:
        workbook = Workbook()
        workbook.active.title = "隐藏"
        workbook.active.sheet_state = "hidden"
        visible = workbook.create_sheet("DATA")
        visible.append(["name", "value"])
        visible.append(["a", 1])
        visible.append(["b", 2])
        output = io.BytesIO()
        workbook.save(output)
        ref = AttachmentRef(
            id="xlsx-1",
            name="data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        tool = await self._tool("read_xlsx")
        context = _context(ref)
        with patch(
            "deep_agent.capabilities.attachments.builtin._download_file",
            new_callable=AsyncMock,
            return_value=output.getvalue(),
        ) as download:
            first = await tool.call_func(
                {"file_id": ref.id, "limit": 2},
                context,
            )
            second = await tool.call_func(
                {
                    "file_id": ref.id,
                    "sheet": "data",
                    "offset": first.next_offset,
                    "limit": 2,
                },
                context,
            )
        self.assertEqual(first.metadata["selected_sheet"], "DATA")
        self.assertEqual(first.content, "name,value\na,1")
        self.assertEqual(second.content, "b,2")
        download.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
