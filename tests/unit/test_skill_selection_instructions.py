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


class SkillSelectionInstructionTests(unittest.IsolatedAsyncioTestCase):
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
