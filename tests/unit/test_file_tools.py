from __future__ import annotations

import asyncio
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from docx import Document
from openpyxl import Workbook
from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.attachments import AttachmentDownloadError
from deep_agent.agent.deps import AgentDeps
from deep_agent.files import build_file_toolset
from deep_agent.files import toolset as file_tools
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.skills.runtime import ResolvedSkill


def _context(*refs: AttachmentRef) -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(
            session_id="file-test",
            attachment_by_id={ref.id: ref for ref in refs},
        ),
        model=TestModel(),
        usage=RunUsage(),
    )


def _entry(path: str, *, category: str = "asset", mime: str = "application/octet-stream") -> dict:
    return {
        "path": path,
        "category": category,
        "mime": mime,
        "size": 1,
        "content_hash": "",
        "readable": category != "script",
    }


def _resolved_skill(root: Path, entries: list[dict]) -> ResolvedSkill:
    for entry in entries:
        target = root / str(entry["path"])
        if target.is_file():
            import hashlib

            entry["content_hash"] = hashlib.sha256(target.read_bytes()).hexdigest()
            entry["size"] = target.stat().st_size
    return ResolvedSkill(
        code="demo",
        source="upload",
        package_root=root,
        library_root=root.parent,
        resource_index=entries,
    )


async def _tool(resources: dict[str, ResolvedSkill] | None = None, ctx=None):
    context = ctx or _context()
    tools = await build_file_toolset(resources).get_tools(context)
    return tools.get("read_file")


class UnifiedFileToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_page_defaults_and_caps_are_doubled(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock,
                   return_value=("x\n" * 4_100).encode()):
            default = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, context)
            maximum = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, "limit": 9_000}, context)
        self.assertEqual((default.limit, default.returned_count), (1_000, 1_000))
        self.assertEqual((maximum.limit, maximum.returned_count), (4_000, 4_000))
        self.assertEqual((default.next_offset, default.next_char_offset), (1_001, 0))

    async def test_text_long_line_resumes_without_suffix_loss_or_repeat_download(self) -> None:
        original = "文🙂" * 55_001
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        parser = Mock(wraps=file_tools.text_document)
        pages = []
        args = {"resource": {"type": "attachment", "file_id": ref.id}}
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock,
                   return_value=original.encode()) as download, patch("deep_agent.files.toolset.text_document", parser):
            for _ in range(4):
                result = await tool.call_func(args, context)
                pages.append(result.content)
                self.assertLessEqual(len(result.content), 50_000)
                if not result.has_more:
                    break
                self.assertEqual(result.next_offset, 1)
                args.update(offset=result.next_offset, char_offset=result.next_char_offset)
            else:
                self.fail("read_file did not reach EOF")
        self.assertEqual("".join(pages), original)
        self.assertEqual(result.char_offset, 100_000)
        self.assertIsNone(result.next_char_offset)
        download.assert_awaited_once()
        parser.assert_called_once()

    async def test_invalid_character_cursor_is_a_model_retry(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=b"abc"):
            for cursor in (-1, 3, 4, None):
                with self.subTest(cursor=cursor), self.assertRaises(ModelRetry):
                    await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, "char_offset": cursor}, context)

    async def test_office_page_defaults_and_caps_are_doubled(self) -> None:
        document = Document()
        for _ in range(1_050):
            document.add_paragraph("x")
        docx_bytes = io.BytesIO()
        document.save(docx_bytes)
        workbook = Workbook()
        for _ in range(1_050):
            workbook.active.append(["x"])
        xlsx_bytes = io.BytesIO()
        workbook.save(xlsx_bytes)
        for suffix, raw, mime in (("docx", docx_bytes.getvalue(), file_tools._DOCX_MIME),
                                  ("xlsx", xlsx_bytes.getvalue(), file_tools._XLSX_MIME)):
            with self.subTest(format=suffix):
                ref = AttachmentRef(id=suffix, name=f"example.{suffix}", mime=mime)
                context = _context(ref)
                tool = await _tool(ctx=context)
                with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=raw):
                    default = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, context)
                    maximum = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}, "limit": 9_000}, context)
                self.assertEqual((default.limit, default.returned_count), (200, 200))
                self.assertEqual((maximum.limit, maximum.returned_count), (1_000, 1_000))

    async def test_docx_long_paragraph_resumes_without_losing_suffix(self) -> None:
        original = "正文" * 25_001
        document = Document()
        document.add_paragraph(original)
        raw = io.BytesIO()
        document.save(raw)
        ref = AttachmentRef(id="docx", name="example.docx", mime=file_tools._DOCX_MIME)
        context = _context(ref)
        tool = await _tool(ctx=context)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=raw.getvalue()):
            first = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, context)
            last = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id},
                                         "offset": first.next_offset, "char_offset": first.next_char_offset}, context)
        self.assertEqual(first.next_offset, 1)
        self.assertEqual(first.next_char_offset, 50_000)
        self.assertEqual(first.content + last.content, original)
        self.assertFalse(last.has_more)

    async def test_xlsx_long_row_resumes_without_losing_suffix(self) -> None:
        workbook = Workbook()
        randomizer = random.Random(0)
        workbook.active.append([
            "".join(randomizer.choices("abcdefghijklmnopqrstuvwxyz", k=3_000))
            for _ in range(20)
        ])
        raw = io.BytesIO()
        workbook.save(raw)
        expected = file_tools.xlsx_workbook(raw.getvalue()).sheets[0].rows[0]
        ref = AttachmentRef(id="xlsx", name="example.xlsx", mime=file_tools._XLSX_MIME)
        context = _context(ref)
        tool = await _tool(ctx=context)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=raw.getvalue()):
            first = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id}}, context)
            last = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id},
                                         "offset": first.next_offset, "char_offset": first.next_char_offset}, context)
        self.assertEqual((first.next_offset, first.next_char_offset), (1, 50_000))
        self.assertEqual(first.content + last.content, expected)
        self.assertFalse(last.has_more)
        for page in (first, last):
            self.assertLessEqual(len(json.dumps(page.metadata, ensure_ascii=False)), 8_000)
            self.assertTrue(page.metadata["sheets"][0]["headers_truncated"])
            self.assertTrue(all(len(value) <= 128 for value in page.metadata["sheets"][0]["headers"]))

    async def test_xlsx_long_cells_are_read_completely_with_bounded_metadata(self) -> None:
        randomizer = random.Random(1)
        cells = ["".join(randomizer.choices("abcdefghijklmnopqrstuvwxyz", k=30_000)) for _ in range(3)]
        workbook = Workbook()
        workbook.active.append(cells)
        raw = io.BytesIO()
        workbook.save(raw)
        ref = AttachmentRef(id="xlsx", name="long-cells.xlsx", mime=file_tools._XLSX_MIME)
        context = _context(ref)
        tool = await _tool(ctx=context)
        args = {"resource": {"type": "attachment", "file_id": ref.id}}
        chunks = []
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock,
                   return_value=raw.getvalue()) as download:
            for _ in range(3):
                page = await tool.call_func(args, context)
                chunks.append(page.content)
                self.assertLessEqual(len(page.content), 50_000)
                self.assertLessEqual(len(json.dumps(page.metadata, ensure_ascii=False)), 8_000)
                self.assertTrue(page.metadata["sheets"][0]["headers_truncated"])
                if not page.has_more:
                    break
                args.update(offset=page.next_offset, char_offset=page.next_char_offset)
            else:
                self.fail("Long XLSX first row did not finish")
        self.assertEqual("".join(chunks), ",".join(cells))
        download.assert_awaited_once()

    async def test_xlsx_sheet_directory_is_bounded_without_hiding_named_read(self) -> None:
        workbook = Workbook()
        for index in range(40):
            sheet = workbook.active if index == 0 else workbook.create_sheet()
            sheet.title = f"sheet-{index}"
            sheet.append([f"header-{column}" for column in range(50)])
        raw = io.BytesIO()
        workbook.save(raw)
        ref = AttachmentRef(id="xlsx", name="many-sheets.xlsx", mime=file_tools._XLSX_MIME)
        context = _context(ref)
        tool = await _tool(ctx=context)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock,
                   return_value=raw.getvalue()):
            page = await tool.call_func({"resource": {"type": "attachment", "file_id": ref.id},
                                         "sheet": "sheet-39"}, context)
        self.assertEqual(page.metadata["selected_sheet"], "sheet-39")
        self.assertEqual(page.metadata["sheet_count"], 40)
        self.assertTrue(page.metadata["sheets_truncated"])
        self.assertLessEqual(len(page.metadata["sheets"]), 32)
        self.assertLessEqual(len(json.dumps(page.metadata, ensure_ascii=False)), 8_000)
        self.assertEqual(page.content, ",".join(f"header-{column}" for column in range(50)))
        self.assertTrue(all(len(sheet["headers"]) <= 32 for sheet in page.metadata["sheets"]))

    async def test_skill_long_line_supports_character_continuation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-file-cursor-") as tmpdir:
            root = Path(tmpdir)
            target = root / "references/guide.md"
            target.parent.mkdir()
            original = "参考正文" * 13_000
            target.write_text(original, encoding="utf-8")
            item = _resolved_skill(root, [_entry("references/guide.md", category="reference")])
            context = _context()
            tool = await _tool({"demo": item}, ctx=context)
            resource = {"type": "skill", "skill_code": "demo", "path": "references/guide.md"}
            first = await tool.call_func({"resource": resource}, context)
            last = await tool.call_func({"resource": resource, "offset": first.next_offset,
                                         "char_offset": first.next_char_offset}, context)
            self.assertEqual(first.content + last.content, original)
            self.assertFalse(last.has_more)

    async def test_hidden_without_readable_source_and_visible_for_attachment(self) -> None:
        self.assertIsNone(await _tool())
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        tool = await _tool(ctx=_context(ref))
        self.assertIsNotNone(tool)

    async def test_unknown_attachment_is_rejected_before_download(self) -> None:
        ref = AttachmentRef(id="known", name="note.txt", mime="text/plain")
        tool = await _tool(ctx=_context(ref))
        assert tool is not None
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
        ) as download:
            with self.assertRaises(ModelRetry):
                await tool.call_func(
                    {"resource": {"type": "attachment", "file_id": "other"}},
                    _context(ref),
                )
        download.assert_not_awaited()

    async def test_resource_union_rejects_mixed_or_incomplete_parameters(self) -> None:
        ref = AttachmentRef(id="known", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        invalid_resources = (
            {"type": "attachment", "file_id": "known", "skill_code": "demo"},
            {"type": "skill", "skill_code": "demo"},
            {"type": "unknown", "file_id": "known"},
        )
        for resource in invalid_resources:
            with self.subTest(resource=resource), self.assertRaises(ModelRetry):
                await tool.call_func({"resource": resource}, context)

    async def test_attachment_text_pages_download_and_parse_once_per_run(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        parser = Mock(wraps=file_tools.text_document)
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            return_value=b"one\ntwo\nthree\nfour",
        ) as download, patch("deep_agent.files.toolset.text_document", parser):
            first, second = await asyncio.gather(
                tool.call_func(
                    {"resource": {"type": "attachment", "file_id": ref.id}, "limit": 2},
                    context,
                ),
                tool.call_func(
                    {
                        "resource": {"type": "attachment", "file_id": ref.id},
                        "offset": 3,
                        "limit": 2,
                    },
                    context,
                ),
            )
        self.assertEqual(first.content, "one\ntwo")
        self.assertEqual(second.content, "three\nfour")
        self.assertEqual(first.unit, "line")
        download.assert_awaited_once()
        parser.assert_called_once()

    async def test_log_attachment_with_generic_mime_is_read_as_text(self) -> None:
        ref = AttachmentRef(
            id="log",
            name="service.log",
            mime="application/octet-stream",
        )
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            return_value=b"INFO started\nWARN slow",
        ):
            result = await tool.call_func(
                {"resource": {"type": "attachment", "file_id": ref.id}},
                context,
            )
        self.assertEqual(result.format, "text")
        self.assertEqual(result.content, "INFO started\nWARN slow")

    async def test_new_run_has_a_new_file_cache(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            return_value=b"one",
        ) as download:
            for _ in range(2):
                context = _context(ref)
                tool = await _tool(ctx=context)
                assert tool is not None
                await tool.call_func(
                    {"resource": {"type": "attachment", "file_id": ref.id}},
                    context,
                )
        self.assertEqual(download.await_count, 2)

    async def test_failed_attachment_download_is_terminal_and_not_cached(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            side_effect=AttachmentDownloadError("offline"),
        ) as download:
            for _ in range(2):
                with self.assertRaises(ToolFailed):
                    await tool.call_func(
                        {"resource": {"type": "attachment", "file_id": ref.id}},
                        context,
                    )
        self.assertEqual(download.await_count, 2)
        self.assertFalse(context.deps.file_bytes)
        self.assertFalse(context.deps.file_load_tasks)

    async def test_cancelled_load_releases_single_flight_state(self) -> None:
        ref = AttachmentRef(id="text", name="note.txt", mime="text/plain")
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        started = asyncio.Event()

        async def blocked_download(*_args, **_kwargs) -> bytes:
            started.set()
            await asyncio.Event().wait()
            return b"unreachable"

        with patch("deep_agent.files.toolset._download_file", side_effect=blocked_download):
            call = asyncio.create_task(
                tool.call_func(
                    {"resource": {"type": "attachment", "file_id": ref.id}},
                    context,
                )
            )
            await started.wait()
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call

        self.assertFalse(context.deps.file_bytes)
        self.assertFalse(context.deps.file_load_tasks)
        self.assertFalse(context.deps.file_parse_tasks)

    async def test_docx_returns_text_blocks_only(self) -> None:
        document = Document()
        document.add_paragraph("正文")
        raw_docx = io.BytesIO()
        document.save(raw_docx)
        ref = AttachmentRef(
            id="docx",
            name="example.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            return_value=raw_docx.getvalue(),
        ):
            result = await tool.call_func(
                {"resource": {"type": "attachment", "file_id": ref.id}},
                context,
            )
        self.assertEqual(result.content, "正文")
        self.assertNotIn("embedded_images", result.metadata)

    async def test_xlsx_defaults_to_first_visible_sheet_and_paginates(self) -> None:
        workbook = Workbook()
        workbook.active.title = "隐藏"
        workbook.active.sheet_state = "hidden"
        visible = workbook.create_sheet("DATA")
        visible.append(["name", "value"])
        visible.append(["a", 1])
        visible.append(["b", 2])
        raw = io.BytesIO()
        workbook.save(raw)
        ref = AttachmentRef(
            id="xlsx",
            name="data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        context = _context(ref)
        tool = await _tool(ctx=context)
        assert tool is not None
        with patch(
            "deep_agent.files.toolset._download_file",
            new_callable=AsyncMock,
            return_value=raw.getvalue(),
        ) as download:
            first = await tool.call_func(
                {"resource": {"type": "attachment", "file_id": ref.id}, "limit": 2},
                context,
            )
            second = await tool.call_func(
                {
                    "resource": {"type": "attachment", "file_id": ref.id},
                    "sheet": "data",
                    "offset": first.next_offset,
                    "limit": 2,
                },
                context,
            )
        self.assertEqual(first.content, "name,value\na,1")
        self.assertEqual(second.content, "b,2")
        download.assert_awaited_once()

    async def test_skill_pages_read_and_parse_local_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "references/guide.md"
            target.parent.mkdir()
            target.write_text("one\ntwo\nthree", encoding="utf-8")
            item = _resolved_skill(root, [_entry("references/guide.md", category="reference")])
            context = _context()
            tool = await _tool({"demo": item}, ctx=context)
            assert tool is not None
            parser = Mock(wraps=file_tools.text_document)
            real_read_bytes = Path.read_bytes
            read_bytes = Mock(side_effect=lambda value: real_read_bytes(value))
            with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes), patch(
                "deep_agent.files.toolset.text_document", parser
            ):
                first = await tool.call_func(
                    {
                        "resource": {
                            "type": "skill",
                            "skill_code": "demo",
                            "path": "references/guide.md",
                        },
                        "limit": 1,
                    },
                    context,
                )
                second = await tool.call_func(
                    {
                        "resource": {
                            "type": "skill",
                            "skill_code": "demo",
                            "path": "references/guide.md",
                        },
                        "offset": first.next_offset,
                        "limit": 1,
                    },
                    context,
                )
            self.assertEqual((first.content, second.content), ("one", "two"))
            self.assertEqual(read_bytes.call_count, 1)
            parser.assert_called_once()

    async def test_skill_permission_script_and_hash_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            script = root / "scripts/run.py"
            script.parent.mkdir()
            script.write_text("print('x')", encoding="utf-8")
            item = _resolved_skill(root, [_entry("scripts/run.py", category="script")])
            context = _context()
            tools = await build_file_toolset({"demo": item}).get_tools(context)
            self.assertEqual(tools, {})

            readable = root / "references/guide.md"
            readable.parent.mkdir()
            readable.write_text("guide", encoding="utf-8")
            entry = _entry("references/guide.md", category="reference")
            item = _resolved_skill(root, [entry, _entry("scripts/run.py", category="script")])
            tool = await _tool({"demo": item}, ctx=context)
            assert tool is not None
            with self.assertRaises(ModelRetry):
                await tool.call_func(
                    {
                        "resource": {
                            "type": "skill",
                            "skill_code": "demo",
                            "path": "scripts/run.py",
                        }
                    },
                    context,
                )
            entry["content_hash"] = "0" * 64
            with self.assertRaises(ToolFailed):
                await tool.call_func(
                    {
                        "resource": {
                            "type": "skill",
                            "skill_code": "demo",
                            "path": "references/guide.md",
                        }
                    },
                    _context(),
                )

    async def test_image_never_injects_read_file(self) -> None:
        ref = AttachmentRef(id="image", name="a.png", mime="image/png")
        self.assertIsNone(await _tool(ctx=_context(ref)))


if __name__ == "__main__":
    unittest.main()
