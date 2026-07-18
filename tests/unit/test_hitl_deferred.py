from __future__ import annotations

import unittest

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from deep_agent.agent import events
from deep_agent.agent.deferred import build_deferred_tool_results, serialize_deferred
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import build_ui_toolset, builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset, build_tool_hints
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.schemas.chat import DeferredToolResultItem
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits
from tests.helpers.sqlite_stores import make_sqlite_stores


class DeferredSerializationTests(unittest.TestCase):
    def test_serialize_deferred_calls(self) -> None:
        requests = DeferredToolRequests(
            calls=[
                ToolCallPart(
                    tool_name="confirm_action",
                    args={"message": "继续？"},
                    tool_call_id="call-1",
                )
            ],
            approvals=[],
        )
        payload = serialize_deferred(requests)
        self.assertEqual(len(payload["calls"]), 1)
        self.assertEqual(payload["calls"][0]["tool"], "confirm_action")
        self.assertEqual(payload["calls"][0]["tool_call_id"], "call-1")
        self.assertEqual(payload["calls"][0]["args"], {"message": "继续？"})

    def test_build_deferred_tool_results(self) -> None:
        items = [
            DeferredToolResultItem(tool_call_id="c1", result={"confirmed": True}),
            DeferredToolResultItem(tool_call_id="c2", approved=False, denied_message="不允许"),
        ]
        results = build_deferred_tool_results(items)
        self.assertEqual(results.calls["c1"], {"confirmed": True})
        self.assertEqual(results.approvals["c2"].message, "不允许")


class FinalEventTests(unittest.TestCase):
    def test_final_pending_includes_deferred(self) -> None:
        deferred = {"calls": [{"tool": "confirm_action", "args": {}, "tool_call_id": "x"}]}
        payload = events.final(None, {"requests": 1}, status="pending", deferred=deferred)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["deferred"], deferred)

    def test_final_completed_default_status(self) -> None:
        payload = events.final("hello", {})
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["output"], "hello")


class AgentRunnerDeferredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self._engine.dispose()

    async def test_run_stream_emits_pending_final_for_external_tool(self) -> None:
        test_model = TestModel(call_tools=["confirm_action"], custom_output_text="done")
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_ui_toolset()],
            output_type=[str, DeferredToolRequests],
        )
        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            ui_tool_hints=build_tool_hints(builtin_ui_records()),
        )

        events_out: list[dict] = []
        async for ev in runner.run_stream(
            "sess-1",
            "请用户确认",
            AgentDeps(session_id="sess-1"),
            persister=self.persister,
        ):
            events_out.append(ev)

        self.assertEqual(events_out[-1]["type"], "final")
        self.assertEqual(events_out[-1]["status"], "pending")
        self.assertTrue(events_out[-1]["deferred"]["calls"])
        tool_calls = [e for e in events_out if e.get("type") == "tool_call"]
        self.assertFalse(tool_calls)
        self.assertEqual(events_out[-1]["deferred"]["calls"][0]["tool"], "confirm_action")

    async def test_run_stream_tool_call_includes_metadata_from_hints(self) -> None:
        manual_tool = UIToolRecord(
            name="myapp__form",
            description="form",
            parameters_json_schema={"type": "object", "properties": {}},
            widget="text",
            metadata={"form_version": "1"},
            source="manual",
        )
        test_model = TestModel(call_tools=["myapp__form"], custom_output_text="done")
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_external_toolset([manual_tool])],
            output_type=[str, DeferredToolRequests],
        )
        hints = build_tool_hints([manual_tool])
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10), ui_tool_hints=hints)

        events_out: list[dict] = []
        async for ev in runner.run_stream(
            "sess-meta",
            "请填写表单",
            AgentDeps(session_id="sess-meta"),
            persister=self.persister,
        ):
            events_out.append(ev)

        tool_calls = [e for e in events_out if e.get("type") == "tool_call"]
        self.assertFalse(tool_calls)
        self.assertEqual(events_out[-1]["deferred"]["calls"][0]["metadata"], {"form_version": "1"})
        self.assertEqual(events_out[-1]["deferred"]["calls"][0]["widget"], "text")

    async def test_run_stream_only_publishes_validated_external_ui_call(self) -> None:
        clarify = UIToolRecord(
            name="deep_cube__clarify",
            description="clarify",
            parameters_json_schema={
                "type": "object",
                "properties": {"fields": {"type": "array"}},
            },
            widget="clarify",
            source="manual",
        )
        model_calls = 0

        async def stream_function(_messages: list, _info: AgentInfo):
            nonlocal model_calls
            model_calls += 1
            args = "{}" if model_calls == 1 else (
                '{"fields":[{"field":"reason","type":"textarea"}]}'
            )
            yield {
                0: DeltaToolCall(
                    name="deep_cube__clarify",
                    json_args=args,
                    tool_call_id=f"call-{model_calls}",
                )
            }

        agent = Agent(
            FunctionModel(stream_function=stream_function),
            deps_type=AgentDeps,
            toolsets=[build_external_toolset([clarify])],
            output_type=[str, DeferredToolRequests],
        )
        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            ui_tool_hints=build_tool_hints([clarify]),
        )

        events_out: list[dict] = []
        async for ev in runner.run_stream(
            "sess-retry",
            "请澄清",
            AgentDeps(session_id="sess-retry"),
            persister=self.persister,
        ):
            events_out.append(ev)

        self.assertEqual(model_calls, 2)
        self.assertFalse([event for event in events_out if event.get("type") == "tool_call"])
        deferred_call = events_out[-1]["deferred"]["calls"][0]
        self.assertEqual(deferred_call["tool_call_id"], "call-2")
        self.assertEqual(deferred_call["args"]["fields"][0]["field"], "reason")

    async def test_run_stream_resume_completes_after_deferred_result(self) -> None:
        test_model = TestModel(
            call_tools=["confirm_action"],
            custom_output_text="已根据用户确认继续",
        )
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_ui_toolset()],
            output_type=[str, DeferredToolRequests],
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-2")

        first: list[dict] = []
        async for ev in runner.run_stream("sess-2", "需要确认", deps, persister=self.persister):
            first.append(ev)
        await self.persister.flush("sess-2")
        self.assertEqual(first[-1]["status"], "pending")
        tool_call_id = first[-1]["deferred"]["calls"][0]["tool_call_id"]

        second: list[dict] = []
        async for ev in runner.run_stream(
            "sess-2",
            "",
            deps,
            deferred_tool_results=DeferredToolResults(calls={tool_call_id: {"confirmed": True}}),
            persister=self.persister,
        ):
            second.append(ev)

        self.assertEqual(second[-1]["status"], "completed")
        self.assertEqual(second[-1]["output"], "已根据用户确认继续")
        tool_results = [e for e in second if e.get("type") == "tool_result"]
        self.assertTrue(tool_results)


if __name__ == "__main__":
    unittest.main()
