"""Late release gates, run separately after the final full suite had imported tests."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.agent.structured_output import StructuredOutputState
from deep_agent.conversation.projector import project_session_events
from tests.integration import test_structured_output_remaining_boundaries as fixture


class StructuredOutputValidationCancellationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StructuredOutputRemainingBoundaryTests.asyncSetUp
    asyncTearDown = fixture.StructuredOutputRemainingBoundaryTests.asyncTearDown
    deps = fixture.StructuredOutputRemainingBoundaryTests.deps
    execute = fixture.StructuredOutputRemainingBoundaryTests.execute
    runner = fixture.StructuredOutputRemainingBoundaryTests.runner
    rows = fixture.StructuredOutputRemainingBoundaryTests.rows

    async def test_real_validation_worker_and_sdk_validation_preaccept_cancellation(self):
        """DFR-14: cancel in the actual worker and after actual SDK output validation."""
        from deep_agent.structured_output import validation
        for streaming in (False, True):
            for phase in ("worker", "after_sdk"):
                with self.subTest(streaming=streaming, phase=phase):
                    reached = asyncio.Event()
                    requests, processes = [], []
                    config = {"name": "lease_result", "description": "cancellation fixture",
                        "json_schema": {"type": "object", "properties": {
                            "text": {"type": "string", **({"pattern": "(a+)+$"} if phase == "worker" else {})}},
                            "required": ["text"]}}
                    def respond(messages, info):
                        requests.append(True)
                        value = {} if len(requests) == 1 else {"text": "a" * 40 + "!" if phase == "worker" else "valid"}
                        return ModelResponse(parts=[ToolCallPart("lease_result", value, f"cancel-{len(requests)}")])
                    runner = self.runner(respond, loader=AsyncMock(return_value=config))
                    sid = uuid4().hex
                    spawn = asyncio.create_subprocess_exec
                    original_after_validate = StructuredOutputState.after_output_validate
                    async def record_worker(*args, **kwargs):
                        process = await spawn(*args, **kwargs)
                        if str(validation._WORKER) in args:
                            processes.append(process)
                            if phase == "worker" and len(processes) == 2:
                                communicate = process.communicate
                                async def mark_running(input=None):
                                    if input is not None:
                                        # The real one-shot worker has received the
                                        # pathological regex input before cancellation.
                                        asyncio.get_running_loop().call_later(0.1, reached.set)
                                    return await communicate(input)
                                process.communicate = mark_running
                        return process
                    async def block_after_sdk(state, ctx, **kwargs):
                        result = await original_after_validate(state, ctx, **kwargs)
                        if phase == "after_sdk" and not ctx.partial_output:
                            reached.set()
                            await asyncio.Event().wait()
                        return result
                    with patch("deep_agent.structured_output.validation.asyncio.create_subprocess_exec", record_worker), \
                         patch.object(StructuredOutputState, "after_output_validate", block_after_sdk):
                        task = asyncio.create_task(self.execute(runner, sid, stream=streaming))
                        try:
                            await asyncio.wait_for(reached.wait(), 4)
                            self.assertFalse(task.done())
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await asyncio.wait_for(task, 4)
                        finally:
                            if not task.done():
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(len(processes), 2)
                    self.assertTrue(all(p.returncode is not None for p in processes))
                    for process in processes:
                        with self.assertRaises(ChildProcessError):
                            os.waitpid(process.pid, os.WNOHANG)
                    rows = await self.rows(sid)
                    attempts = [r for r in rows if r.event_type == "output/attempt"]
                    self.assertEqual(len(attempts), 1, "prior failed output fact must remain")
                    self.assertFalse(attempts[0].payload["accepted"])
                    self.assertFalse([i for i in project_session_events(rows).items if i.kind == "structured_output"])
                    self.assertEqual(runner._active_runs.active_count(), 0)


class StructuredOutputPendingConfigurationDeletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StructuredOutputDeferredOwnershipTests.asyncSetUp
    asyncTearDown = fixture.StructuredOutputDeferredOwnershipTests.asyncTearDown

    async def test_admin_api_disable_or_delete_while_pending_resumes_original_snapshot(self):
        """DFR-05: actual management PATCH false/null, then actual SSE resume."""
        from httpx import AsyncClient
        from deep_agent.schemas.agent import AgentUpdate
        from deep_agent.security import ConsoleAdminAuthorizer
        from tests.helpers.request_identity import TEST_USER_ID
        from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
        self.ctx.console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=True))
        original = {"name": "public_result", "description": "frozen before removal", "json_schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
        for delete_definition in (False, True):
            with self.subTest(delete_definition=delete_definition):
                await self.ctx.stores.agent.update_unique(self._agent_id, AgentUpdate(
                    structured_output_enabled=True, output_config=deepcopy(original)), user_id=TEST_USER_ID)
                calls = []
                def respond(messages, info):
                    calls.append(info)
                    if len(calls) == 1:
                        return ModelResponse(parts=[ToolCallPart("public_result", {}, "invalid-first")])
                    if len(calls) == 2:
                        return ModelResponse(parts=[ToolCallPart("builtin__confirm", {"message": "confirm fixture"}, "pending")])
                    self.assertEqual(info.output_tools, calls[0].output_tools)
                    return ModelResponse(parts=[ToolCallPart("public_result", {"ok": True}, "frozen-final")])
                async def stream(messages, info):
                    for i, part in enumerate(respond(messages, info).parts):
                        yield {i: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
                model = FunctionModel(respond, stream_function=stream)
                with patch("deep_agent.agent.factory._resolve_model", return_value=model), mock_request_disconnect():
                    async with AsyncClient(transport=self._transport, base_url="http://test") as http:
                        first = await http.post("/api/v1/chat/stream", json={"agent_id": self._agent_id,
                            "message": "start frozen workflow", "capabilities": ["ui:builtin"]})
                        self.assertEqual(first.status_code, 200, first.text)
                        frames = parse_sse_events(first.text)
                        pending = next(f for f in reversed(frames) if f["type"] == "final")
                        self.assertEqual(pending["status"], "pending")
                        sid = frames[0]["session_id"]
                        body = {"structured_output_enabled": False}
                        if delete_definition:
                            body["output_config"] = None
                        changed = await http.patch(f"/api/v1/agents/{self._agent_id}", json=body)
                        self.assertEqual(changed.status_code, 200, changed.text)
                        config = await http.get(f"/api/v1/agents/{self._agent_id}/output-config")
                        self.assertEqual(config.status_code, 200, config.text)
                        self.assertFalse(config.json()["structured_output_enabled"])
                        self.assertEqual(config.json()["output_config"], None if delete_definition else original)
                        resumed = await http.post("/api/v1/chat/stream", json={"agent_id": self._agent_id,
                            "session_id": sid, "capabilities": ["ui:builtin"],
                            "deferred_tool_results": [{"tool_call_id": "pending", "result": {"confirmed": True}}]})
                        self.assertEqual(resumed.status_code, 200, resumed.text)
                        final = next(f for f in reversed(parse_sse_events(resumed.text)) if f["type"] == "final")
                        self.assertEqual(final["output"], {"ok": True})
                        self.assertEqual(final["output_info"]["attempt"], 2)
                        self.assertEqual(len(calls), 3)
                        output = next(i for i in project_session_events(await self.ctx.stores.coordinator.list_all_events(sid)).items
                                      if i.kind == "structured_output")
                        self.assertEqual(output.item_id, final["output_info"]["output_id"])
