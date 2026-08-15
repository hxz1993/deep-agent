from __future__ import annotations

import json
import unittest

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness.planning import PlanItem, Planning

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.factory import build_agent
from deep_agent.agent.history import model_messages_to_history
from deep_agent.agent.planning import (
    PLANNING_TOOL_NAMES,
    build_planning_capability,
    current_plan_snapshot,
    prepare_plan_store,
)
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import ToolPackageRegistry
from tests.helpers.sqlite_stores import make_sqlite_stores


class PlanningCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_agent_gets_default_harness_planning(self) -> None:
        agent = build_agent(
            AgentSpec(
                name="planning",
                model="test",
                instructions="test",
                capabilities=[],
            ),
            ToolPackageRegistry(),
        )

        planning = [
            item
            for item in agent.root_capability.capabilities
            if isinstance(item, Planning)
        ]
        self.assertEqual(len(planning), 1)
        self.assertFalse(planning[0].enable_subtasks)
        self.assertTrue(planning[0].inject)
        self.assertIsNone(planning[0].tools)

    async def test_plan_store_is_fresh_for_each_run(self) -> None:
        deps = AgentDeps(session_id="same-session")
        first = prepare_plan_store(deps)
        await first.add_item(
            PlanItem(
                id="one",
                content="first",
                status="pending",
            )
        )
        self.assertEqual((await current_plan_snapshot(deps))["total"], 1)

        second = prepare_plan_store(deps)
        self.assertIsNot(first, second)
        self.assertEqual((await current_plan_snapshot(deps))["total"], 0)


class PlanningRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self.engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self.engine.dispose()

    async def test_stream_snapshots_follow_planning_results_and_persist(self) -> None:
        calls = 0

        async def respond(_messages: list[object], info: AgentInfo):
            nonlocal calls
            calls += 1
            self.assertEqual(
                set(PLANNING_TOOL_NAMES),
                {tool.name for tool in info.function_tools},
            )
            if calls == 1:
                yield {0: DeltaToolCall(
                    name="write_plan",
                    tool_call_id="plan-write",
                    json_args=json.dumps({
                        "items": [
                            {
                                "id": "step-1",
                                "content": "读取输入",
                                "active_form": "正在读取输入",
                                "status": "in_progress",
                            },
                            {
                                "id": "step-2",
                                "content": "生成结果",
                                "active_form": "正在生成结果",
                                "status": "pending",
                            },
                        ]
                    }, ensure_ascii=False),
                )}
                return
            if calls == 2:
                yield {0: DeltaToolCall(
                    name="update_task_statuses",
                    tool_call_id="plan-progress",
                    json_args=json.dumps({
                        "updates": [
                            {"task_id": "step-1", "status": "completed"},
                            {"task_id": "step-2", "status": "in_progress"},
                        ]
                    }),
                )}
                return
            if calls == 3:
                yield {0: DeltaToolCall(
                    name="update_task_status",
                    tool_call_id="plan-complete",
                    json_args=json.dumps({"task_id": "step-2", "status": "completed"}),
                )}
                return
            yield "任务完成"

        agent = Agent(
            FunctionModel(stream_function=respond),
            deps_type=AgentDeps,
            capabilities=[build_planning_capability()],
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="planning-stream")

        output = [
            event
            async for event in runner.run_stream(
                "planning-stream",
                "先制定计划，再完成两步任务",
                deps,
                persister=self.persister,
            )
        ]
        await self.persister.flush("planning-stream")

        planning_results = [
            event
            for event in output
            if event.get("type") == "tool_result"
            and event.get("source") == "planning"
        ]
        self.assertEqual(
            [(event["plan"]["completed"], event["plan"]["total"]) for event in planning_results],
            [(0, 2), (1, 2), (2, 2)],
        )
        self.assertEqual(output[-1]["type"], "final")

        history = model_messages_to_history(
            await self.store.get_history("planning-stream")
        )
        plan_parts = [
            part
            for message in history
            for part in message.parts
            if part.type == "tool_result" and part.source == "planning"
        ]
        self.assertEqual(plan_parts[-1].plan["completed"], 2)
        self.assertEqual(plan_parts[-1].plan["total"], 2)
        persisted_reminders = [
            part
            for message in await self.store.get_history("planning-stream")
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and "<plan-reminder>" in str(part.content)
        ]
        self.assertEqual(persisted_reminders, [])

    async def test_non_stream_snapshot_persists_and_next_run_starts_empty(self) -> None:
        def respond(messages: list[object], _info: AgentInfo) -> ModelResponse:
            current_prompt = str(messages[-1])
            has_current_plan_return = any(
                isinstance(part, ToolReturnPart) and part.tool_name == "write_plan"
                for message in messages[-2:]
                for part in getattr(message, "parts", [])
            )
            if "创建计划" in current_prompt and not has_current_plan_return:
                return ModelResponse(parts=[ToolCallPart(
                    tool_name="write_plan",
                    tool_call_id="non-stream-plan",
                    args={"items": [{
                        "id": "only-step",
                        "content": "完成任务",
                        "active_form": "正在完成任务",
                        "status": "completed",
                    }]},
                )])
            return ModelResponse(parts=[TextPart(content="ok")])

        agent = Agent(
            FunctionModel(respond),
            deps_type=AgentDeps,
            capabilities=[build_planning_capability()],
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=5))
        deps = AgentDeps(session_id="planning-non-stream")

        await runner.run("planning-non-stream", "创建计划", deps)
        first_history = model_messages_to_history(
            await self.store.get_history("planning-non-stream")
        )
        first_plan_parts = [
            part
            for message in first_history
            for part in message.parts
            if part.type == "tool_result" and part.source == "planning"
        ]
        self.assertEqual(first_plan_parts[-1].plan["completed"], 1)

        await runner.run("planning-non-stream", "简单回答", deps)
        self.assertEqual((await current_plan_snapshot(deps))["total"], 0)


if __name__ == "__main__":
    unittest.main()
