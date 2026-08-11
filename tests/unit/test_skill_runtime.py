from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.context.compaction import _safe_boundaries
from deep_agent.agent.history import has_unprocessed_tool_calls
from deep_agent.skills.runtime import (
    SkillRuntimeLibrary,
    resolve_skills_for_run,
    validate_skill_codes,
)
from deep_agent.skills.tools import build_skill_resource_toolset
from tests.helpers.sqlite_stores import make_sqlite_stores


class SkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.runtime = SkillRuntimeLibrary(
            Path(tempfile.mkdtemp(prefix="skill-runtime-test-TMP_to_delete-"))
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_harness_progressively_loads_skill_body(self) -> None:
        resolved = await resolve_skills_for_run(
            self.stores,
            ["deepagent-diagnostics"],
            runtime=self.runtime,
            request_headers={},
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
                            {"id": "deepagent-diagnostics"},
                            "load-skill-1",
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content="diagnosed")])

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
        deps = AgentDeps(session_id="session")
        result = await runner.run("session", "Diagnose the session", deps)

        self.assertEqual(result["output"], "diagnosed")
        self.assertIn("deepagent-diagnostics", seen_instructions[0])
        self.assertNotIn("Identify the exact Agent", seen_instructions[0])
        self.assertIn("Identify the exact Agent", seen_messages[1])
        call_parts = [
            part
            for message in await self.stores.coordinator.get_history("session")
            for part in message.parts
            if isinstance(part, (ToolCallPart, ToolReturnPart))
            and part.tool_name == "load_capability"
        ]
        self.assertEqual(len(call_parts), 2)
        history = await self.stores.coordinator.get_history("session")
        self.assertFalse(has_unprocessed_tool_calls(history))
        boundaries = _safe_boundaries(history)
        self.assertEqual(boundaries[-1], len(history))
        self.assertLess(len(boundaries), len(history) + 1)
        self.assertEqual(result["context"]["skills"]["loaded"], ["deepagent-diagnostics"])

    async def test_skill_resources_are_authorized_and_paginated(self) -> None:
        resolved = await resolve_skills_for_run(
            self.stores,
            ["deepagent-diagnostics"],
            runtime=self.runtime,
            request_headers={},
        )
        toolset = build_skill_resource_toolset(resolved.resources)
        self.assertIsNotNone(toolset)
        responses = 0

        def respond(_messages, _info: AgentInfo) -> ModelResponse:
            nonlocal responses
            responses += 1
            if responses == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "list_skill_resources",
                            {"skill_code": "deepagent-diagnostics"},
                            "list-1",
                        )
                    ]
                )
            if responses == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_skill_resource",
                            {
                                "skill_code": "deepagent-diagnostics",
                                "path": "references/diagnostic-checklist.md",
                                "offset": 0,
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
            toolsets=[toolset],
        )
        result = await agent.run("Read the checklist", deps=AgentDeps(session_id="session"))
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual([part.tool_name for part in returns], ["list_skill_resources", "read_skill_resource"])
        self.assertNotIn("scripts/", str(returns[0].content))
        self.assertIn("scripts_executable", str(returns[0].content))
        self.assertIn("content_hash", str(returns[1].content))

    async def test_unselected_and_duplicate_skills_are_rejected(self) -> None:
        self.assertEqual(await validate_skill_codes(self.stores, ["missing"]), ["missing"])
        self.assertEqual(
            await validate_skill_codes(
                self.stores,
                ["deepagent-diagnostics", "deepagent-diagnostics"],
            ),
            ["deepagent-diagnostics"],
        )

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
            AgentDeps(session_id="plain-session"),
        )

        self.assertEqual(response["output"], "plain answer")
        self.assertEqual(
            response["context"]["skills"]["unavailable"],
            [{"code": "broken-skill", "reason": "download_failed"}],
        )


if __name__ == "__main__":
    unittest.main()
