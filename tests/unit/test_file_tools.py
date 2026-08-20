from __future__ import annotations

import asyncio
import io
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
        enabled=True,
        package_root=root,
        library_root=root.parent,
        resource_index=entries,
    )


async def _tool(resources: dict[str, ResolvedSkill] | None = None, ctx=None):
    context = ctx or _context()
    tools = await build_file_toolset(resources).get_tools(context)
    return tools.get("read_file")


class UnifiedFileToolTests(unittest.IsolatedAsyncioTestCase):
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
