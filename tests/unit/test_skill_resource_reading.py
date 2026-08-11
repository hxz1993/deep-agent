from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from docx import Document
from openpyxl import Workbook
from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.resource_reader import (
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
    def test_text_keeps_zero_based_character_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "guide.md"
            target.write_text("abcdef", encoding="utf-8")
            page = read_resource_page(
                _entry("guide.md", category="reference", readable=True),
                target,
                offset=2,
                limit=3,
            )
        self.assertEqual(page.format, "text")
        self.assertEqual(page.unit, "character")
        self.assertEqual(page.content, "cde")
        self.assertEqual(page.next_offset, 5)

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
                enabled=True,
                package_root=root,
                library_root=root.parent,
                resource_index=[
                    _entry("references/guide.md", category="reference", readable=True),
                    _entry("scripts/run.py", category="script"),
                ],
            )
            toolset = build_skill_resource_toolset(
                {"demo": item}, scripts_executable=True
            )
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
            self.assertTrue(script["executable"])
            with self.assertRaises(ModelRetry):
                await tools["read_skill_resource"].call_func(
                    {"skill_code": "demo", "path": "scripts/run.py"}, ctx
                )

    async def test_missing_indexed_file_is_terminal_tool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            item = ResolvedSkill(
                code="demo",
                source="upload",
                enabled=True,
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
            with self.assertRaises(ToolFailed):
                await tools["read_skill_resource"].call_func(
                    {"skill_code": "demo", "path": "references/missing.md"}, ctx
                )


if __name__ == "__main__":
    unittest.main()
