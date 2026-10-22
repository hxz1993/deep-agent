from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ExternalToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.conversation.projector import project_session_events
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class StructuredOutputRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()
        self.title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.title.start()

    async def asyncTearDown(self):
        self.title.stop()
        await self.persister.aclose()
        await self.engine.dispose()

    async def exercise(self, value, schema, *, stream=False, mixed=False, invalid=False, request_limit=20):
        requests, executed = [], []
        def response(messages, info):
            requests.append(info)
            if mixed and len(requests) == 1:
                return ModelResponse(parts=[ToolCallPart("lookup", {}, "lookup-1"),
                                            ToolCallPart("lease_result", {}, "out-1")])
            actual = {} if invalid and len(requests) == 1 else value
            args = actual if schema.get("type") == "object" else {"response": actual}
            return ModelResponse(parts=[ToolCallPart("lease_result", args, f"out-{len(requests)}")])

        async def streaming(messages, info):
            result = response(messages, info)
            for index, part in enumerate(result.parts):
                yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}

        agent = Agent(FunctionModel(response, stream_function=streaming), deps_type=AgentDeps,
                      retries=2, end_strategy="graceful")
        @agent.tool_plain
        async def lookup():
            executed.append(True)
            return "found"

        runner = AgentRunner(agent, self.stores.coordinator, UsageLimits(request_limit=request_limit))
        runner._output_config_loader = AsyncMock(return_value={"name": "lease_result", "description": "Return result", "json_schema": schema})
        session_id = uuid4().hex
        deps = AgentDeps(session_id=session_id, user="test-user", agent_id=self.agent_id)
        if stream:
            events = [event async for event in runner.run_stream(session_id, "synthetic", deps, persister=self.persister)]
            self.assertEqual(events[-1]["type"], "final", events[-1])
            result = events[-1]
        else:
            events = []
            result = await runner.run(session_id, "synthetic", deps)
        self.assertEqual(result["output"], value)
        self.assertEqual(type(result["output"]), type(value))
        rows = await self.stores.coordinator.list_all_events(session_id)
        history = project_session_events(rows)
        accepted = [item for item in history.items if item.kind == "structured_output"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].output, value)
        self.assertEqual(accepted[0].item_id, result["output_info"]["output_id"])
        self.assertFalse([item for item in history.items if item.kind == "tool"])
        self.assertFalse(executed)
        self.assertEqual(len(requests), 2 if mixed or invalid else 1)
        if len(requests) == 2:
            self.assertEqual(requests[0].output_tools, requests[1].output_tools)
        attempts = [row for row in rows if row.event_type == "output/attempt"]
        self.assertEqual(len(attempts), len(requests))
        self.assertTrue(attempts[-1].payload["accepted"])
        if stream:
            outputs = [event for event in events if event.get("type") == "output"]
            self.assertEqual(len(outputs), len(attempts))
            self.assertEqual(outputs[-1]["output_id"], accepted[0].item_id)
        return result, rows

    async def test_native_roots_and_empty_values(self):
        for value, schema in [({}, {"type": "object", "properties": {}}),
                              ([{"id": "a"}], {"type": "array", "items": {"type": "object", "required": ["id"]}}),
                              (False, {"type": "boolean"}), (0, {"type": "integer"}),
                              (None, {"type": "null"}), ("", {"type": "string"})]:
            for stream in (False, True):
                with self.subTest(value=value, stream=stream):
                    await self.exercise(value, schema, stream=stream)

    async def test_mixed_calls_rejected_before_execution(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                await self.exercise({"ok": True}, {"type": "object"}, stream=stream, mixed=True)

    async def test_full_internal_required_validation(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                await self.exercise({"contract": {"id": "a"}}, {"type": "object", "required": ["contract"],
                    "properties": {"contract": {"type": "object", "required": ["id"]}}}, stream=stream, invalid=True)

    async def test_last_request_retains_output_tool(self):
        await self.exercise({"ok": True}, {"type": "object"}, request_limit=1)

    async def test_deferred_resume_reloads_config_once_and_preserves_budget(self):
        calls = []
        def response(messages, info):
            calls.append(info)
            step = len(calls)
            if step in (1, 3):
                return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{step}")])
            if step == 2:
                return ModelResponse(parts=[ToolCallPart("confirm", {}, "confirm-1")])
            return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "accepted-1")])
        external = ExternalToolset([ToolDefinition(name="confirm", parameters_json_schema={"type": "object", "properties": {}})])
        agent = Agent(FunctionModel(response), deps_type=AgentDeps, toolsets=[external], retries=2)
        runner = AgentRunner(agent, self.stores.coordinator, UsageLimits(request_limit=10))
        contract = {"name": "lease_result", "description": "Frozen", "json_schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
        runner._output_config_loader = AsyncMock(return_value=contract)
        sid = uuid4().hex
        pending = await runner.run(sid, "synthetic", AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user"))
        self.assertEqual(pending["status"], "pending")
        self.assertNotIn("output_info", pending)
        runner._output_config_loader.assert_awaited_once()
        runner._output_config_loader = AsyncMock(return_value=contract)
        result = await runner.run(sid, "", AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user"),
            deferred_tool_results=DeferredToolResults(calls={"confirm-1": True}))
        self.assertEqual(result["output"], {"ok": True})
        self.assertEqual(result["output_info"]["attempt"], 3)
        runner._output_config_loader.assert_awaited_once()
        self.assertEqual(calls[0].output_tools, calls[-1].output_tools)
        rows = await self.stores.coordinator.list_all_events(sid)
        self.assertEqual(len([row for row in rows if row.event_type == "output/attempt"]), 3)
        from deep_agent.agent.deferred import DeferredResultConflict
        with self.assertRaises(DeferredResultConflict):
            await runner.run(sid, "", AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user"),
                deferred_tool_results=DeferredToolResults(calls={"confirm-1": True}))

    async def test_retry_exhaustion_has_no_accepted_or_tool_history(self):
        requests = []
        def response(messages, info):
            requests.append(info)
            return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{len(requests)}")])
        runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.stores.coordinator, UsageLimits(request_limit=10))
        runner._output_config_loader = AsyncMock(return_value={"name": "lease_result", "json_schema": {
            "type": "object", "required": ["id"]}})
        sid = uuid4().hex
        with self.assertRaisesRegex(Exception, "output_retry_exhausted"):
            await runner.run(sid, "synthetic", AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user"))
        self.assertEqual(len(requests), 3)
        rows = await self.stores.coordinator.list_all_events(sid)
        self.assertEqual(len([row for row in rows if row.event_type == "execution/error"]), 1)
        self.assertFalse([item for item in project_session_events(rows).items if item.kind in {"tool", "structured_output"}])
