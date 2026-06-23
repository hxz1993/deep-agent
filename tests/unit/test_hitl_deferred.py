from __future__ import annotations

import unittest

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ToolCallPart

from deep_agent.agent import events
from deep_agent.agent.deferred import build_deferred_tool_results, serialize_deferred
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import build_ui_toolset
from deep_agent.memory import InMemorySessionStore
from deep_agent.schemas.chat import DeferredToolResultItem
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits


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
    async def test_run_stream_emits_pending_final_for_external_tool(self) -> None:
        test_model = TestModel(call_tools=["confirm_action"], custom_output_text="done")
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_ui_toolset()],
            output_type=[str, DeferredToolRequests],
        )
        store = InMemorySessionStore()
        runner = AgentRunner(agent, store, UsageLimits(request_limit=10))

        events_out: list[dict] = []
        async for ev in runner.run_stream(
            "sess-1",
            "请用户确认",
            AgentDeps(session_id="sess-1"),
        ):
            events_out.append(ev)

        self.assertEqual(events_out[-1]["type"], "final")
        self.assertEqual(events_out[-1]["status"], "pending")
        self.assertTrue(events_out[-1]["deferred"]["calls"])
        tool_calls = [e for e in events_out if e.get("type") == "tool_call"]
        self.assertTrue(tool_calls)
        self.assertEqual(tool_calls[-1]["tool"], "confirm_action")

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
        store = InMemorySessionStore()
        runner = AgentRunner(agent, store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-2")

        first: list[dict] = []
        async for ev in runner.run_stream("sess-2", "需要确认", deps):
            first.append(ev)
        self.assertEqual(first[-1]["status"], "pending")
        tool_call_id = first[-1]["deferred"]["calls"][0]["tool_call_id"]

        second: list[dict] = []
        async for ev in runner.run_stream(
            "sess-2",
            "",
            deps,
            deferred_tool_results=DeferredToolResults(calls={tool_call_id: {"confirmed": True}}),
        ):
            second.append(ev)

        self.assertEqual(second[-1]["status"], "completed")
        self.assertEqual(second[-1]["output"], "已根据用户确认继续")
        tool_results = [e for e in second if e.get("type") == "tool_result"]
        self.assertTrue(tool_results)


if __name__ == "__main__":
    unittest.main()
