"""Exercise distinct provider names and the original client/history contract."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.toolsets import CombinedToolset, FunctionToolset

from deep_agent.agent.history import repair_unprocessed_tool_calls
from deep_agent.agent.tool_names import ScopedToolset, ToolName, WIRE_NAMES, model_tool_history, wire_name
from deep_agent.capabilities.ui.registry import build_ui_tool_package, build_tool_hints
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.conversation.projector import project_session_events
from tests.integration import test_structured_output_boundaries as fixture


class ToolNameCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StructuredOutputBoundaryTests.asyncSetUp
    asyncTearDown = fixture.StructuredOutputBoundaryTests.asyncTearDown
    runner = fixture.StructuredOutputBoundaryTests.runner
    deps = fixture.StructuredOutputBoundaryTests.deps
    execute = fixture.StructuredOutputBoundaryTests.execute

    def ui_tool(self, name="get_current_time"):
        record = UIToolRecord(
            name=name, package_code="ui:form", description="Collect a value.",
            parameters_json_schema={"type": "object", "properties": {}},
            widget="text", result_schema={"type": "object"}, metadata={}, source="manual",
        )
        package = build_ui_tool_package([record], code=record.package_code, label="Form", source="manual")
        hints = build_tool_hints([record])
        hints[name]["wire_name"] = wire_name("ui", "form", name)
        return package.toolset, hints

    async def test_same_system_mcp_ui_and_output_name_in_both_chat_modes(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                sid = uuid4().hex
                ui, hints = self.ui_tool()
                name = "get_current_time"
                mcp_name = ToolName("mcp", name, "clock")
                names = (name, mcp_name.wire, "ui__form__get_current_time", "output__get_current_time")
                public_names = (name, mcp_name.public, name, name)
                mcp = FunctionToolset()
                mcp.add_function(lambda: "external clock", name=name)
                sent = []

                def respond(messages, info):
                    function_names = {tool.name for tool in info.function_tools}
                    self.assertTrue(set(names[:3]) <= function_names)
                    self.assertEqual([tool.name for tool in info.output_tools], [names[3]])
                    step = len(sent)
                    sent.append(messages)
                    if step == 2:
                        self.assertTrue(any(
                            isinstance(part, ToolReturnPart) and part.tool_name == names[1]
                            and part.content == "external clock"
                            for message in messages for part in message.parts
                        ))
                    if step == 3:
                        self.assertTrue(any(
                            isinstance(part, ToolReturnPart) and part.tool_name == names[2]
                            and part.content == {"confirmed": True}
                            for message in messages for part in message.parts
                        ))
                    args = {"ok": True} if step == 3 else {}
                    return ModelResponse(parts=[ToolCallPart(names[step], args, f"call-{step}")])

                runner, agent = self.runner(respond, loader=AsyncMock(return_value={
                    **fixture.CONTRACT_DATA, "name": name,
                }), toolsets=[ui, ScopedToolset(mcp, "mcp", "clock")])
                runner.ui_tool_hints = hints
                runner.tool_display_names = {name: "系统时钟", names[2]: "业务确认"}

                @agent.tool_plain
                def get_current_time() -> str:
                    return "fixture time"

                pending, frames = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(pending["status"], "pending", pending)
                self.assertEqual(pending["deferred"]["calls"][0]["tool"], name)
                self.assertEqual(pending["deferred"]["calls"][0]["display_name"], "业务确认")
                if streaming:
                    live = [frame for frame in frames if frame["type"] == "tool_call"]
                    self.assertEqual([frame["tool"] for frame in live], list(public_names[:3]))
                    self.assertNotIn("widget", live[0])
                    self.assertEqual(live[2]["widget"], "text")
                final, _ = await self.execute(runner, sid, stream=streaming, message="",
                    results=DeferredToolResults(calls={"call-2": {"confirmed": True}}))
                self.assertEqual(final["output_info"]["name"], name)
                rows = await self.stores.coordinator.list_all_events(sid)
                items = project_session_events(rows).items
                self.assertEqual([(item.call_id, item.tool_name) for item in items if item.kind == "tool"],
                                 [(f"call-{i}", public) for i, public in enumerate(public_names[:3])])
                self.assertEqual([item.name for item in items if item.kind == "structured_output"], [name])
                name_by_call = {f"call-{i}": public for i, public in enumerate(public_names)}
                for row in rows:
                    for part in (row.payload.get("message") or {}).get("parts", []):
                        if part.get("tool_call_id") in name_by_call and part.get("tool_name"):
                            self.assertEqual(part["tool_name"], name_by_call[part["tool_call_id"]])

    async def test_legacy_unprefixed_ui_pending_resumes_and_replays_without_rewriting(self):
        sid = uuid4().hex
        ui, hints = self.ui_tool("old_form")
        legacy = await self.stores.coordinator.append(
            sid, [ModelRequest(parts=[UserPromptPart("old prompt")]),
                  ModelResponse(parts=[ToolCallPart("old_form", {}, "old-id")])],
            agent_id=self.agent_id, user_id="test-user", turn_id="legacy-turn", run_id="legacy-run",
            terminal_status="pending",
        )
        original = {row.event_id: json.dumps(row.payload, sort_keys=True) for row in legacy}
        name = wire_name("ui", "form", "old_form")
        checks = []

        def respond(messages, info):
            matching = [part.tool_name for message in messages for part in message.parts
                        if isinstance(part, (ToolCallPart, ToolReturnPart)) and part.tool_call_id == "old-id"]
            self.assertEqual(matching, [name, name])
            checks.append(True)
            return ModelResponse(parts=[ToolCallPart("output__lease_result", {"ok": True}, uuid4().hex)])

        runner, _ = self.runner(respond, toolsets=[ui])
        runner.ui_tool_hints = hints
        final, _ = await self.execute(runner, sid, stream=True, message="",
            results=DeferredToolResults(calls={"old-id": {"confirmed": True}}))
        self.assertEqual(final["status"], "completed", final)
        follow_up, _ = await self.execute(runner, sid)
        self.assertEqual(follow_up["status"], "completed", follow_up)
        self.assertEqual(len(checks), 2)
        rows = await self.stores.coordinator.list_all_events(sid)
        self.assertEqual({row.event_id: json.dumps(row.payload, sort_keys=True)
                          for row in rows if row.event_id in original}, original)

    async def test_mcp_legacy_prefix_collision_still_reaches_each_server(self):
        left, right = FunctionToolset(), FunctionToolset()
        left.add_function(lambda: "server a_b", name="c")
        right.add_function(lambda: "server a", name="b_c")
        pair = (ToolName("mcp", "c", "a_b"), ToolName("mcp", "b_c", "a"))
        self.assertEqual(pair[0].public, pair[1].public)
        self.assertNotEqual(pair[0].wire, pair[1].wire)
        steps = []

        def respond(messages, info):
            self.assertTrue({item.wire for item in pair} <= {tool.name for tool in info.function_tools})
            if not steps:
                steps.append(True)
                return ModelResponse(parts=[
                    ToolCallPart(identity.wire, {}, f"mcp-{i}") for i, identity in enumerate(pair)
                ])
            results = {part.tool_name: part.content for message in messages for part in message.parts
                       if isinstance(part, ToolReturnPart)}
            self.assertEqual(results, {pair[0].wire: "server a_b", pair[1].wire: "server a"})
            return ModelResponse(parts=[ToolCallPart("output__lease_result", {"ok": True}, "result")])

        toolset = CombinedToolset([ScopedToolset(left, "mcp", "a_b"), ScopedToolset(right, "mcp", "a")])
        runner, _ = self.runner(respond, toolsets=[toolset])
        sid = uuid4().hex
        final, _ = await self.execute(runner, sid, stream=True)
        self.assertEqual(final["status"], "completed", final)
        rows = await self.stores.coordinator.list_all_events(sid)
        items = project_session_events(rows).items
        self.assertEqual({item.call_id: item.output for item in items if item.kind == "tool"},
                         {"mcp-0": "server a_b", "mcp-1": "server a"})
        self.assertEqual({value for row in rows
                          for value in (row.payload.get("message", {}).get("metadata") or {}).get(WIRE_NAMES, {}).values()},
                         {pair[0].wire, pair[1].wire, "output__lease_result"})

    def test_history_repair_keeps_tool_alias_with_its_retained_result(self):
        name = "a_b_c"
        rejected_wire = wire_name("mcp", "a_b", "c")
        retained_wire = wire_name("mcp", "a", "b_c")
        messages = [
            ModelResponse(parts=[ToolCallPart(name, {}, "rejected"), ToolCallPart(name, {}, "retained")],
                          metadata={WIRE_NAMES: {"0": rejected_wire, "1": retained_wire}}),
            ModelRequest(parts=[RetryPromptPart("retry", tool_name=name, tool_call_id="rejected")],
                         metadata={WIRE_NAMES: {"0": rejected_wire}}),
            ModelRequest(parts=[ToolReturnPart(name, ToolDenied(message="stopped"), "rejected"),
                                ToolReturnPart(name, "retained result", "retained")],
                         metadata={WIRE_NAMES: {"0": rejected_wire, "1": retained_wire}}),
        ]
        repaired = model_tool_history(repair_unprocessed_tool_calls(messages))
        self.assertEqual(len(repaired[-1].parts), 1)
        self.assertEqual(repaired[-1].parts[0].tool_name, retained_wire)
        self.assertEqual(repaired[-1].parts[0].content, "retained result")

    def test_unsafe_and_delimiter_names_have_stable_bounded_aliases(self):
        entries = [
            ("mcp", "a_", "b"), ("mcp", "a", "_b"), ("mcp", "a__b", "c"),
            ("mcp", "a", "b__c"), ("mcp", "a", "工具"), ("mcp", "a", "查询"),
            ("mcp", "service1", "query"), ("mcp", "service2", "query"),
            ("ui", "a", "a" * 120), ("output", "a" * 120),
        ]
        aliases = [wire_name(*entry) for entry in entries]
        self.assertEqual(aliases, [wire_name(*entry) for entry in entries])
        self.assertEqual(len(aliases), len(set(aliases)))
        for alias in aliases:
            self.assertRegex(alias, r"^[A-Za-z0-9_-]{1,64}$")
