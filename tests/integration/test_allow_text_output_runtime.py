"""Mixed text/structured contracts with the actual SDK, validator and Event Store."""
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic_ai import DeferredToolResults, ExternalToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import Tool, ToolDefinition

from deep_agent.agent.structured_output import CONTRACT, StructuredOutputState
from deep_agent.conversation.projector import project_session_events
from tests.integration import test_structured_output_remaining_boundaries as fixture

BASE = {"name": "lease_result", "allow_text_output": True, "json_schema": {
    "type": "object", "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"], "additionalProperties": False}}


class AllowTextOutputRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StructuredOutputRemainingBoundaryTests.asyncSetUp
    asyncTearDown = fixture.StructuredOutputRemainingBoundaryTests.asyncTearDown
    deps = fixture.StructuredOutputRemainingBoundaryTests.deps
    execute = fixture.StructuredOutputRemainingBoundaryTests.execute
    runner = fixture.StructuredOutputRemainingBoundaryTests.runner
    rows = fixture.StructuredOutputRemainingBoundaryTests.rows

    def sequence(self, replies):
        calls = []
        def respond(messages, info):
            calls.append(info)
            self.assertLessEqual(len(calls), len(replies), "unexpected extra model request")
            return ModelResponse(parts=deepcopy(replies[len(calls) - 1]))
        return respond, calls

    async def history(self, sid):
        return project_session_events(await self.rows(sid)).items

    async def test_text_is_a_normal_reply_in_http_and_streaming(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                respond, requests = self.sequence([[TextPart("请先上传文件。")]])
                sid = uuid4().hex
                runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)))
                final, frames = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(final["status"], "completed", final)
                self.assertEqual(final["output"], "请先上传文件。")
                self.assertNotIn("output_info", final)
                self.assertTrue(requests[0].allow_text_output)
                self.assertEqual([t.name for t in requests[0].output_tools], ["lease_result"])
                self.assertFalse([r for r in await self.rows(sid) if r.event_type == "output/attempt"])
                items = await self.history(sid)
                self.assertEqual([i.text for i in items if i.kind == "assistant_message"], ["请先上传文件。"])
                self.assertFalse([i for i in items if i.kind == "structured_output"])
                if streaming:
                    self.assertTrue([f for f in frames if f["type"] == "text"])
                    self.assertFalse([f for f in frames if f["type"] == "output"])

    async def test_json_and_structured_string_still_have_output_identity(self):
        for streaming in (False, True):
            for value, schema in [({"ok": True}, BASE["json_schema"]), ("a structured string", {"type": "string"}),
                                  (None, {"type": "null"}), (False, {"type": "boolean"})]:
                with self.subTest(streaming=streaming, value=value):
                    args = value if schema.get("type") == "object" else {"response": value}
                    respond, calls = self.sequence([[ToolCallPart("lease_result", args, "out-1")]])
                    runner = self.runner(respond, loader=AsyncMock(return_value={**BASE, "json_schema": schema}))
                    sid = uuid4().hex
                    final, _ = await self.execute(runner, sid, stream=streaming)
                    self.assertEqual(final["output"], value)
                    self.assertIn("output_info", final)
                    items = [i for i in await self.history(sid) if i.kind == "structured_output"]
                    self.assertEqual(len(items), 1)
                    self.assertEqual(items[0].item_id, final["output_info"]["output_id"])
                    self.assertEqual(items[0].output, value)
                    self.assertEqual(len(calls), 1)

    async def test_text_with_json_keeps_both_live_and_history_without_two_outputs(self):
        for streaming in (False, True):
            respond, _ = self.sequence([[TextPart("结果如下。"), ToolCallPart("lease_result", {"ok": True}, "out-1")]])
            runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)))
            sid = uuid4().hex
            final, frames = await self.execute(runner, sid, stream=streaming)
            self.assertEqual(final["output"], {"ok": True})
            items = await self.history(sid)
            self.assertEqual([i.text for i in items if i.kind == "assistant_message"], ["结果如下。"])
            self.assertEqual(len([i for i in items if i.kind == "structured_output"]), 1)
            self.assertFalse([i for i in items if i.kind == "tool"])
            if streaming:
                self.assertTrue([f for f in frames if f["type"] == "text"])
                self.assertEqual(len([f for f in frames if f["type"] == "output" and f.get("accepted")]), 1)

    async def test_ordinary_tool_preamble_does_not_end_run_early(self):
        for streaming in (False, True):
            executed = []
            async def lookup():
                executed.append(True)
                return "synthetic evidence"
            respond, calls = self.sequence([[TextPart("先查询。"), ToolCallPart("lookup", {}, "lookup-1")],
                                             [TextPart("查询已完成。")]])
            runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)), tools=[Tool(lookup)])
            sid = uuid4().hex
            final, _ = await self.execute(runner, sid, stream=streaming)
            self.assertEqual(final["output"], "查询已完成。")
            self.assertEqual(executed, [True])
            self.assertEqual(len(calls), 2)
            self.assertNotIn("output_info", final)
            self.assertEqual(len([i for i in await self.history(sid) if i.kind == "tool"]), 1)

    async def test_invalid_output_then_text_retains_failure_but_accepts_no_json(self):
        for streaming in (False, True):
            respond, calls = self.sequence([[ToolCallPart("lease_result", {}, "bad-1")], [TextPart("还需要确认信息。")]])
            runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)))
            sid = uuid4().hex
            final, _ = await self.execute(runner, sid, stream=streaming)
            self.assertEqual(final["output"], "还需要确认信息。")
            self.assertNotIn("output_info", final)
            rows = await self.rows(sid)
            attempts = [r for r in rows if r.event_type == "output/attempt"]
            self.assertEqual(len(attempts), 1)
            self.assertFalse(attempts[0].payload["accepted"])
            self.assertFalse([r for r in rows if r.event_type == "execution/error"])
            self.assertFalse([i for i in await self.history(sid) if i.kind == "structured_output"])
            self.assertEqual(len(calls), 2)

    async def test_mixed_and_multiple_output_calls_remain_rejected_before_dispatch(self):
        for streaming in (False, True):
            for multiple in (False, True):
                executed = []
                async def lookup():
                    executed.append(True)
                    return "must not execute"
                extra = ToolCallPart("lease_result" if multiple else "lookup", {"ok": True} if multiple else {}, "extra")
                respond, calls = self.sequence([[ToolCallPart("lease_result", {"ok": True}, "out-1"), extra], [TextPart("需要重新确认。")]])
                runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)), tools=[Tool(lookup)])
                sid = uuid4().hex
                final, _ = await self.execute(runner, sid, stream=streaming)
                self.assertNotIn("output_info", final)
                self.assertFalse(executed)
                items = await self.history(sid)
                self.assertFalse([i for i in items if i.kind in {"structured_output", "tool"}])
                self.assertEqual(len(calls), 2)

    async def test_last_request_allows_text_and_keeps_output_tool(self):
        for streaming in (False, True):
            respond, calls = self.sequence([[TextPart("依据现有信息回复。")]])
            runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)), limit=1)
            final, _ = await self.execute(runner, uuid4().hex, stream=streaming)
            self.assertEqual(final["status"], "completed")
            self.assertTrue(calls[0].allow_text_output)
            self.assertEqual([t.name for t in calls[0].output_tools], ["lease_result"])
            self.assertFalse(calls[0].function_tools)

    async def test_deferred_resume_uses_frozen_mixed_policy_and_keeps_correction_budget(self):
        for streaming in (False, True):
            respond, calls = self.sequence([[ToolCallPart("lease_result", {}, "bad-1")],
                [TextPart("请确认。"), ToolCallPart("confirm", {}, "confirm-1")], [TextPart("已确认。")]])
            external = ExternalToolset([ToolDefinition(name="confirm", parameters_json_schema={"type": "object", "properties": {}})])
            runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(BASE)), toolsets=[external])
            sid = uuid4().hex
            pending, _ = await self.execute(runner, sid, stream=streaming)
            self.assertEqual(pending["status"], "pending")
            runner._output_config_loader = AsyncMock(side_effect=AssertionError("must use frozen policy"))
            final, _ = await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls={"confirm-1": True}))
            self.assertEqual(final["output"], "已确认。")
            self.assertNotIn("output_info", final)
            self.assertTrue(calls[-1].allow_text_output)
            attempts = [r for r in await self.rows(sid) if r.event_type == "output/attempt"]
            self.assertEqual(len(attempts), 1)
            snapshots = [(r.payload.get("message", {}).get("metadata") or {}).get(CONTRACT) for r in await self.rows(sid)]
            self.assertTrue(next(s for s in snapshots if s)["allow_text_output"])

    async def test_missing_policy_is_strict_and_invalid_policy_rejected(self):
        state = StructuredOutputState({k: v for k, v in BASE.items() if k != "allow_text_output"})
        self.assertFalse(state.allow_text_output)
        self.assertNotIn(str, state.output_types)
        for flag in [None, 1, "true"]:
            with self.assertRaises(ValueError):
                StructuredOutputState({**BASE, "allow_text_output": flag})
