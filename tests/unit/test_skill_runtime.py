from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.deps import _build_runner_from_spec
from deep_agent.capabilities import build_registry
from deep_agent.agent.context.compaction import _safe_boundaries
from deep_agent.agent.history import has_unprocessed_tool_calls, is_tool_retry
from deep_agent.skills.runtime import (
    SkillRuntimeLibrary,
    resolve_skills_for_run,
    validate_skill_codes,
)
from deep_agent.files import build_file_toolset
from deep_agent.schemas.agent import AgentCreate
from deep_agent.skills.tools import build_skill_resource_toolset
from tests.helpers.sqlite_stores import make_sqlite_stores


class SkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.runtime = SkillRuntimeLibrary(
            Path(self.enterContext(tempfile.TemporaryDirectory(prefix="skill-runtime-test-TMP_to_delete-")))
        )
        model = (await self.stores.llm_model.list_all())[0]
        self.db_agent = await self.stores.agent.create_unique(
            AgentCreate(name="skill-runtime-test", model_id=model.id, prompt="test")
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_harness_progressively_loads_skill_body(self) -> None:
        resolved = await resolve_skills_for_run(
            self.stores,
            ["deepagent-diagnostics", "deepfos-readonly-analysis"],
            runtime=self.runtime,
        )
        seen_instructions: list[str] = []
        seen_messages: list[str] = []
        calls = 0

        def respond(messages, info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            seen_instructions.append(info.instructions or "")
            seen_messages.append(str(messages))
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "load_capability",
                            {"id": "deepfos-readonly-analysis"},
                            "load-skill-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="queried")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            instructions="Base instructions.",
            capabilities=[resolved.capability],
        )
        runner = AgentRunner(
            agent,
            self.stores.coordinator,
            UsageLimits(request_limit=5),
            skill_runtime_context=resolved.as_context(),
        )
        deps = AgentDeps(
            session_id="session", user="skill-user", agent_id=self.db_agent.id
        )
        with patch(
            "deep_agent.agent.title_generator.generate_title",
            new=AsyncMock(return_value=None),
        ):
            result = await runner.run("session", "只读查询成本中心成员", deps)

        self.assertEqual(result["output"], "queried")
        self.assertIn("deepfos-readonly-analysis", seen_instructions[0])
        self.assertNotIn("deepagent-diagnostics", seen_instructions[0])
        self.assertNotIn("先判断请求会不会写入", seen_instructions[0])
        self.assertIn("先判断请求会不会写入", seen_messages[1])
        call_parts = [
            part
            for message in await self.stores.coordinator.get_history(
                "session", agent_id=self.db_agent.id
            )
            for part in message.parts
            if isinstance(part, (ToolCallPart, ToolReturnPart))
            and part.tool_name == "load_capability"
        ]
        self.assertEqual(len(call_parts), 2)
        history = await self.stores.coordinator.get_history(
            "session", agent_id=self.db_agent.id
        )
        self.assertFalse(has_unprocessed_tool_calls(history))
        boundaries = _safe_boundaries(history)
        self.assertEqual(boundaries[-1], len(history))
        self.assertLess(len(boundaries), len(history) + 1)
        self.assertEqual(result["context"]["skills"]["loaded"], ["deepfos-readonly-analysis"])
        self.assertEqual(result["context"]["skills"]["unavailable"], [])

    async def test_skill_resources_are_authorized_and_paginated(self) -> None:
        resolved = await resolve_skills_for_run(
            self.stores,
            ["file-reading-verification"],
            runtime=self.runtime,
        )
        resource_toolset = build_skill_resource_toolset(resolved.resources)
        file_toolset = build_file_toolset(resolved.resources)
        self.assertIsNotNone(resource_toolset)
        responses = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal responses
            responses += 1
            if responses == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "list_skill_resources",
                            {"skill_code": "file-reading-verification"},
                            "list-1",
                        )
                    ]
                )
            if responses == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_file",
                            {
                                "resource": {
                                    "type": "skill",
                                    "skill_code": "file-reading-verification",
                                    "path": "references/spreadsheet-verification.md",
                                },
                                "offset": 1,
                                "limit": 32,
                            },
                            "read-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            toolsets=[resource_toolset, file_toolset],
        )
        result = await agent.run("Read the checklist", deps=AgentDeps(session_id="session"))
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual([part.tool_name for part in returns], ["list_skill_resources", "read_file"])
        self.assertNotIn("scripts/", str(returns[0].content))
        self.assertIn("scripts_readable", str(returns[0].content))
        self.assertIn("content_hash", str(returns[1].content))

    async def test_unselected_and_duplicate_skills_are_rejected(self) -> None:
        self.assertEqual(await validate_skill_codes(self.stores, ["missing"]), ["missing"])
        self.assertEqual(
            await validate_skill_codes(
                self.stores,
                ["deepfos-readonly-analysis", "deepfos-readonly-analysis"],
            ),
            ["deepfos-readonly-analysis"],
        )

    async def test_runner_lists_empty_resources_for_a_loaded_skill(self) -> None:
        code = "deepfos-readonly-analysis"
        other = "file-reading-verification"
        calls = iter((
            ToolCallPart("load_capability", {"id": code}, "load"),
            ToolCallPart("list_skill_resources", {"skill_code": code}, "empty"),
            ToolCallPart("list_skill_resources", {"skill_code": other}, "unselected"),
            TextPart("done"),
        ))

        def respond(_messages, info: AgentInfo) -> ModelResponse:
            names = {tool.name for tool in info.function_tools}
            self.assertIn("list_skill_resources", names)
            self.assertNotIn("read_file", names)
            return ModelResponse(parts=[next(calls)])

        spec = AgentSpec(name="empty skill", model="test", instructions="Check resources.", skills=[code])
        ctx = SimpleNamespace(
            stores=self.stores, store=self.stores.coordinator, registry=build_registry(),
            skill_runtime=self.runtime, sandbox_backend=None,
        )
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
            runner = await _build_runner_from_spec(spec, ctx)
        result = await runner.agent.run("List Skill resources.", deps=AgentDeps(session_id="empty-skill"))
        parts = [part for message in result.new_messages() for part in message.parts]
        self.assertTrue(any(isinstance(part, ToolReturnPart) and part.tool_call_id == "load" for part in parts))
        listed = next(part for part in parts if isinstance(part, ToolReturnPart) and part.tool_call_id == "empty")
        self.assertEqual(listed.content, {
            "skill_code": code, "resources": [], "script_count": 0, "scripts_readable": False,
        })
        refused = next(part for part in parts if is_tool_retry(part) and part.tool_call_id == "unselected")
        self.assertIn("is not associated with this Agent", str(refused.content))

    async def test_file_verification_skill_is_explicit_and_grants_no_scripts(self) -> None:
        unselected = await resolve_skills_for_run(
            self.stores, [], runtime=self.runtime
        )
        self.assertEqual(unselected.available_codes, [])
        self.assertIsNone(unselected.capability)
        selected = await resolve_skills_for_run(
            self.stores, ["file-reading-verification"],
            runtime=self.runtime,
        )
        self.assertEqual(selected.available_codes, ["file-reading-verification"])
        self.assertEqual(selected.scripts, {})
        self.assertEqual(selected.as_context()["loaded"], [])
        resources = selected.resources["file-reading-verification"].resource_index
        reference = next(
            item for item in resources
            if item["path"] == "references/spreadsheet-verification.md"
        )
        self.assertTrue(reference["readable"])

    async def test_selected_file_verification_reference_is_readable(self) -> None:
        selected = await resolve_skills_for_run(
            self.stores, ["file-reading-verification"],
            runtime=self.runtime,
        )
        calls = 0
        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart(
                    "read_file",
                    {"resource": {"type": "skill", "skill_code": "file-reading-verification",
                                  "path": "references/spreadsheet-verification.md"}},
                    "read-reference",
                )])
            return ModelResponse(parts=[TextPart(content="done")])
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps,
                      toolsets=[build_file_toolset(selected.resources)])
        result = await agent.run("Read the reference", deps=AgentDeps(session_id="reference-session"))
        returned = next(part for message in result.all_messages() for part in message.parts
                        if isinstance(part, ToolReturnPart))
        self.assertEqual(returned.tool_name, "read_file")
        self.assertFalse(returned.content.has_more)
        self.assertEqual(returned.content.resource.skill_code, "file-reading-verification")

    async def test_unavailable_skill_diagnostic_does_not_block_plain_answer(self) -> None:
        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(content="plain answer")])

        runner = AgentRunner(
            Agent(FunctionModel(respond), deps_type=AgentDeps),
            self.stores.coordinator,
            UsageLimits(request_limit=2),
            skill_runtime_context={
                "configured": ["broken-skill"],
                "available": [],
                "loaded": [],
                "unavailable": [
                    {"code": "broken-skill", "reason": "download_failed"}
                ],
            },
        )
        response = await runner.run(
            "plain-session",
            "hello",
            AgentDeps(
                session_id="plain-session",
                user="skill-user",
                agent_id=self.db_agent.id,
            ),
        )

        self.assertEqual(response["output"], "plain answer")
        self.assertEqual(
            response["context"]["skills"]["unavailable"],
            [{"code": "broken-skill", "reason": "download_failed"}],
        )


if __name__ == "__main__":
    unittest.main()
