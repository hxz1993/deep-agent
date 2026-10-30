from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from docx import Document
from openpyxl import Workbook
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.resource_reader import (
    SkillResourceReadError,
    present_resource_entry,
    read_resource_page,
)
from deep_agent.skills.runtime import ResolvedSkill
from deep_agent.skills.tools import build_skill_resource_toolset


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("第一段")
    document.add_paragraph("第二段")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    workbook.active.title = "隐藏"
    workbook.active.sheet_state = "hidden"
    sheet = workbook.create_sheet("数据")
    sheet.append(["名称", "值"])
    sheet.append(["甲", 1])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _entry(path: str, *, category: str = "asset", readable: bool = False) -> dict:
    return {
        "path": path,
        "category": category,
        "mime": "application/octet-stream",
        "size": 1,
        "content_hash": "hash",
        "readable": readable,
    }


class SkillResourceReadingTests(unittest.IsolatedAsyncioTestCase):
    def test_console_script_preview_does_not_open_model_script_reading(self) -> None:
        entry = _entry("scripts/client.py", category="script")
        self.assertFalse(present_resource_entry(entry)["readable"])
        preview = present_resource_entry(entry, console_preview=True)
        self.assertTrue(preview["readable"])
        self.assertEqual(preview["read_format"], "text")
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-preview-") as tmpdir:
            path = Path(tmpdir) / "client.py"
            path.write_text("print('<unsafe>')\n", encoding="utf-8")
            with self.assertRaises(SkillResourceReadError):
                read_resource_page(entry, path)
            page = read_resource_page(entry, path, console_preview=True)
        self.assertEqual(page.content, "print('<unsafe>')")
        self.assertEqual(page.unit, "line")

    def test_console_does_not_treat_old_binary_readable_flags_as_text(self) -> None:
        for suffix in ("pdf", "pptx", "png", "svg"):
            with self.subTest(suffix=suffix):
                entry = _entry(f"assets/file.{suffix}", readable=True)
                preview = present_resource_entry(entry, console_preview=True)
                self.assertFalse(preview["readable"])
                self.assertIsNone(preview["read_format"])
                with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-preview-") as tmpdir:
                    path = Path(tmpdir) / f"file.{suffix}"
                    path.write_bytes(b"binary document")
                    with self.assertRaisesRegex(SkillResourceReadError, "not_readable"):
                        read_resource_page(entry, path, console_preview=True)

    def test_console_office_preview_uses_actual_type_inside_script_directory(self) -> None:
        for filename, raw, expected_format, expected_content in (
            ("scripts/guide.docx", _docx_bytes(), "docx", "第一段"),
            ("scripts/template.xlsx", _xlsx_bytes(), "xlsx", "名称,值\n甲,1"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-preview-") as tmpdir:
                entry = _entry(filename, category="script")
                self.assertFalse(present_resource_entry(entry)["readable"])
                self.assertEqual(present_resource_entry(entry, console_preview=True)["read_format"], expected_format)
                target = Path(tmpdir) / Path(filename).name
                target.write_bytes(raw)
                with self.assertRaises(SkillResourceReadError):
                    read_resource_page(entry, target)
                page = read_resource_page(entry, target, limit=2, console_preview=True)
                self.assertEqual(page.format, expected_format)
                self.assertTrue(page.content.startswith(expected_content))

    def test_env_resource_is_text_and_preserves_literal_configuration(self) -> None:
        for path in (".env", "assets/service.env"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmpdir:
                target = Path(tmpdir) / ".env"
                content = "# 配置\nDEMO_VALUE=example\nURL=https://example.com?a=1&b=2\n"
                target.write_text(content, encoding="utf-8")
                entry = _entry(path)
                self.assertEqual(present_resource_entry(entry)["read_format"], "text")
                page = read_resource_page(entry, target)
                self.assertEqual(page.content, content.rstrip("\n"))

    def test_long_text_resource_has_lossless_character_cursor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-cursor-") as tmpdir:
            target = Path(tmpdir) / "guide.md"
            original = "说明🙂" * 20_000
            target.write_text(original, encoding="utf-8")
            entry = _entry("guide.md", category="reference", readable=True)
            first = read_resource_page(entry, target)
            last = read_resource_page(entry, target, offset=first.next_offset,
                                      char_offset=first.next_char_offset)
        self.assertEqual((first.next_offset, first.next_char_offset), (1, 50_000))
        self.assertEqual(first.content + last.content, original)
        self.assertEqual(last.char_offset, 50_000)
        self.assertIsNone(last.next_char_offset)
        self.assertFalse(last.has_more)

    def test_office_character_cursor_is_forwarded_and_validated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-cursor-") as tmpdir:
            for filename, raw, expected in (("guide.docx", _docx_bytes(), "一段"),
                                            ("data.xlsx", _xlsx_bytes(), "称,值")):
                with self.subTest(filename=filename):
                    target = Path(tmpdir) / filename
                    target.write_bytes(raw)
                    page = read_resource_page(_entry(filename), target, limit=1, char_offset=1)
                    self.assertEqual(page.content, expected)
                    self.assertEqual(page.char_offset, 1)
                    self.assertEqual((page.next_offset, page.next_char_offset), (2, 0))
                    for char_offset in (-1, 50_001):
                        with self.assertRaises(SkillResourceReadError) as raised:
                            read_resource_page(_entry(filename), target, char_offset=char_offset)
                        self.assertTrue(raised.exception.correctable)

    def test_text_uses_one_based_line_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "guide.md"
            target.write_text("a\nb\nc\nd", encoding="utf-8")
            page = read_resource_page(
                _entry("guide.md", category="reference", readable=True),
                target,
                offset=2,
                limit=2,
            )
        self.assertEqual(page.format, "text")
        self.assertEqual(page.unit, "line")
        self.assertEqual(page.content, "b\nc")
        self.assertEqual(page.next_offset, 4)

    def test_docx_reuses_block_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "guide.docx"
            target.write_bytes(_docx_bytes())
            page = read_resource_page(_entry("guide.docx"), target, offset=1, limit=1)
        self.assertEqual(page.format, "docx")
        self.assertEqual(page.unit, "block")
        self.assertEqual(page.content, "第一段")
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_offset, 2)

    def test_old_xlsx_index_is_readable_without_reinstall(self) -> None:
        old_entry = _entry("assets/template.xlsx", readable=False)
        presented = present_resource_entry(old_entry)
        self.assertTrue(presented["readable"])
        self.assertEqual(presented["read_format"], "xlsx")

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "template.xlsx"
            target.write_bytes(_xlsx_bytes())
            page = read_resource_page(old_entry, target, offset=1, limit=2)
        self.assertEqual(page.content, "名称,值\n甲,1")
        self.assertEqual(page.metadata["selected_sheet"], "数据")
        self.assertEqual(page.metadata["formula_mode"], "cached_values_only")

    async def test_resource_list_exposes_script_path_but_read_rejects_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "references").mkdir()
            (root / "scripts").mkdir()
            (root / "references/guide.md").write_text("guide", encoding="utf-8")
            (root / "scripts/run.py").write_text("pass", encoding="utf-8")
            item = ResolvedSkill(
                code="demo",
                source="upload",
                package_root=root,
                library_root=root.parent,
                resource_index=[
                    _entry("references/guide.md", category="reference", readable=True),
                    _entry("scripts/run.py", category="script"),
                ],
            )
            toolset = build_skill_resource_toolset({"demo": item})
            assert toolset is not None
            ctx = RunContext(
                deps=AgentDeps(session_id="skill-resource", user="user"),
                model=TestModel(),
                usage=RunUsage(),
            )
            tools = await toolset.get_tools(ctx)
            listed = await tools["list_skill_resources"].call_func(
                {"skill_code": "demo"}, ctx
            )
            script = next(entry for entry in listed["resources"] if entry["category"] == "script")
            self.assertEqual(script["path"], "scripts/run.py")
            self.assertFalse(script["readable"])
            self.assertIsNone(script["read_format"])
            self.assertFalse(listed["scripts_readable"])
            self.assertEqual(set(tools), {"list_skill_resources"})

    async def test_resource_toolset_only_lists_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            item = ResolvedSkill(
                code="demo",
                source="upload",
                package_root=root,
                library_root=root.parent,
                resource_index=[_entry("references/missing.md", category="reference", readable=True)],
            )
            toolset = build_skill_resource_toolset({"demo": item})
            assert toolset is not None
            ctx = RunContext(
                deps=AgentDeps(session_id="skill-resource", user="user", context_state=MagicMock()),
                model=TestModel(),
                usage=RunUsage(),
            )
            tools = await toolset.get_tools(ctx)
            self.assertEqual(set(tools), {"list_skill_resources"})


if __name__ == "__main__":
    unittest.main()
