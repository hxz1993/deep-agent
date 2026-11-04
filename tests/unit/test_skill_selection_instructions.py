from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.skills import Skills

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.skills.instructions import SKILL_SELECTION_INSTRUCTIONS
from deep_agent.files.toolset import build_file_toolset
from deep_agent.skills.package import inspect_skill_directory
from deep_agent.skills.runtime import ResolvedSkill
from deep_agent.skills.tools import build_skill_resource_toolset


class SkillSelectionInstructionTests(unittest.IsolatedAsyncioTestCase):
    async def test_loaded_skill_source_and_helper_are_visible_without_execution_permission(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-source-") as temporary:
            library = Path(temporary)
            folder = library / "sample-skill"
            (folder / "scripts/helpers").mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Inspect a report script\n---\n"
                "Inspect scripts/report.py and its helper before explaining the report.\n",
                encoding="utf-8",
            )
            (folder / "scripts/report.py").write_text(
                "from .helpers.client import query\n# ENTRY_SOURCE_MARKER\n"
                "raise RuntimeError('source must never execute during reading')\n", encoding="utf-8",
            )
            (folder / "scripts/helpers/client.py").write_text(
                "def query():\n    return 'HELPER_SOURCE_MARKER'\n", encoding="utf-8",
            )
            (folder / "scripts/query.sql").write_text("SELECT 'SQL_SOURCE_MARKER';\n", encoding="utf-8")
            (folder / "scripts/client.js").write_text("export const source = 'JS_SOURCE_MARKER';\n", encoding="utf-8")
            parsed = inspect_skill_directory(folder, content_hash="fixture")
            item = ResolvedSkill(code="sample-skill", source="upload", package_root=folder,
                                 library_root=library, resource_index=parsed.resource_index)
            step = 0

            def respond(messages, info):
                nonlocal step
                self.assertNotIn("execute_skill_script", {tool.name for tool in info.function_tools})
                seen = str(messages)
                sequence = [
                    ("load_capability", {"id": "sample-skill"}),
                    ("list_skill_resources", {"skill_code": "sample-skill"}),
                    ("read_file", {"resource": {"type": "skill", "skill_code": "sample-skill", "path": "scripts/report.py"}}),
                    ("read_file", {"resource": {"type": "skill", "skill_code": "sample-skill", "path": "scripts/helpers/client.py"}}),
                    ("read_file", {"resource": {"type": "skill", "skill_code": "sample-skill", "path": "scripts/query.sql"}}),
                    ("read_file", {"resource": {"type": "skill", "skill_code": "sample-skill", "path": "scripts/client.js"}}),
                ]
                if step == 0:
                    self.assertNotIn("ENTRY_SOURCE_MARKER", seen)
                    self.assertNotIn("HELPER_SOURCE_MARKER", seen)
                if step == 2:
                    self.assertIn("scripts/helpers/client.py", seen)
                    self.assertIn("scripts_readable", seen)
                if step == 3:
                    self.assertIn("ENTRY_SOURCE_MARKER", seen)
                if step == 4:
                    self.assertIn("HELPER_SOURCE_MARKER", seen)
                if step == 5:
                    self.assertIn("SQL_SOURCE_MARKER", seen)
                if step == 6:
                    self.assertIn("JS_SOURCE_MARKER", seen)
                    return ModelResponse(parts=[TextPart("Read entry and helper source without executing them.")])
                name, args = sequence[step]
                step += 1
                return ModelResponse(parts=[ToolCallPart(name, args, f"source-{step}")])

            with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
                agent = build_agent(
                    AgentSpec(name="test", model="test", instructions="BASE", capabilities=[]),
                    ToolPackageRegistry(), skill_capability=Skills(str(library)),
                    skill_resource_toolset=build_skill_resource_toolset({item.code: item}),
                    file_toolset=build_file_toolset({item.code: item}),
                )
            result = await agent.run("Inspect the report script", deps=AgentDeps(session_id="source-test"))
            self.assertEqual(step, 6)
            self.assertIn("without executing", result.output)

    async def test_guidance_is_conditional_and_does_not_eagerly_load_body(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-selection-") as root:
            folder = Path(root) / "sample-skill"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Sample task handling\n---\n"
                "PRIVATE_BODY_MARKER\n", encoding="utf-8",
            )
            for enabled in (False, True):
                seen = []

                def respond(messages, info):
                    seen.append(info.instructions or "")
                    self.assertNotIn("PRIVATE_BODY_MARKER", str(messages))
                    return ModelResponse(parts=[TextPart("done")])

                with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
                    agent = build_agent(
                        AgentSpec(name="test", model="test", instructions="BASE", capabilities=[]),
                        ToolPackageRegistry(),
                        skill_capability=Skills(root) if enabled else None,
                    )
                await agent.run("hello", deps=AgentDeps(session_id="test"))
                self.assertEqual(seen[0].count(SKILL_SELECTION_INSTRUCTIONS), int(enabled))
                self.assertNotIn("PRIVATE_BODY_MARKER", seen[0])

    async def test_guidance_survives_tool_roundtrip_and_history_reopen(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-selection-") as root:
            folder = Path(root) / "sample-skill"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Sample task handling\n---\n"
                "PRIVATE_BODY_MARKER\n", encoding="utf-8",
            )
            seen = []

            def respond(messages, info):
                seen.append(info.instructions or "")
                if len(seen) == 1:
                    return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "sample-skill"}, "load1")])
                self.assertIn("PRIVATE_BODY_MARKER", str(messages) + (info.instructions or ""))
                return ModelResponse(parts=[TextPart("done")])

            with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
                agent = build_agent(
                    AgentSpec(name="test", model="test", instructions="BASE", capabilities=[]),
                    ToolPackageRegistry(), skill_capability=Skills(root),
                )
            first = await agent.run("sample task", deps=AgentDeps(session_id="test"))
            await agent.run("continue", message_history=first.all_messages(), deps=AgentDeps(session_id="test"))
            self.assertEqual(len(seen), 3)
            self.assertTrue(all(value.count(SKILL_SELECTION_INSTRUCTIONS) == 1 for value in seen))
