"""Runner/Deferred release boundaries using the real SDK, store and validator.

Provider rejection and native search are deterministic Provider protocol fixtures;
they do not claim that a remote native search can be undone by the host.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent, DeferredToolResults, ExternalToolset, ModelRetry
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (ModelRequest, ModelResponse, NativeToolCallPart,
    NativeToolReturnPart, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import Tool
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deferred import DeferredResultConflict
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.request_budget import BUDGET_METADATA, restored_request_count
from deep_agent.agent.structured_output import BUDGET, CONTRACT
from deep_agent.agent.deps import AgentDeps
from deep_agent.conversation.projector import project_session_events
from deep_agent.db.models import ChatSession
from deep_agent.schemas.attachment import AttachmentRef
from tests.integration import test_structured_output_boundaries as fixture
from tests.unit.test_turn_request_budget import SpendSummaryRequest


class StructuredOutputRemainingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StructuredOutputBoundaryTests.asyncSetUp
    asyncTearDown = fixture.StructuredOutputBoundaryTests.asyncTearDown
    deps = fixture.StructuredOutputBoundaryTests.deps
    execute = fixture.StructuredOutputBoundaryTests.execute

    def runner(self, respond, *, loader=None, tools=(), toolsets=(), limit=20,
               capabilities=(), stream_function=None):
        async def stream(messages, info):
            result = respond(messages, info)
            for i, part in enumerate(result.parts):
                if isinstance(part, TextPart):
                    yield part.content
                elif isinstance(part, (NativeToolCallPart, NativeToolReturnPart)):
                    yield {i: part}
                elif isinstance(part, ToolCallPart):
                    yield {i: DeltaToolCall(name=part.tool_name,
                        json_args=part.args if isinstance(part.args, str) else json.dumps(part.args),
                        tool_call_id=part.tool_call_id)}
        agent = Agent(FunctionModel(respond, stream_function=stream_function or stream),
            deps_type=AgentDeps, tools=list(tools), toolsets=list(toolsets),
            capabilities=list(capabilities), retries=2, end_strategy="graceful")
        runner = AgentRunner(agent, self.stores.coordinator, UsageLimits(request_limit=limit))
        runner._output_config_loader = loader or AsyncMock(return_value=fixture.CONTRACT_DATA)
        return runner

    async def rows(self, sid):
        return await self.stores.coordinator.list_all_events(sid)

    async def assert_failed(self, runner, sid, streaming):
        if streaming:
            final, frames = await self.execute(runner, sid, stream=True)
            self.assertEqual(final.get("type"), "error", final)
            self.assertFalse([f for f in frames if f.get("type") == "final"])
        else:
            with self.assertRaises(Exception):
                await self.execute(runner, sid)
        rows = await self.rows(sid)
        self.assertFalse([r for r in rows if r.event_type == "output/attempt" and r.payload["accepted"]])
        self.assertEqual(len([r for r in rows if r.event_type == "execution/error"]), 1)

    async def test_final_request_invalid_mixed_and_business_calls_do_not_execute_or_retry(self):
        """RUN-16: the total request boundary wins over the correction allowance."""
        for streaming in (False, True):
            for kind in ("invalid", "mixed", "business"):
                with self.subTest(streaming=streaming, kind=kind):
                    calls, requests = [], []
                    async def lookup():
                        calls.append(True)
                        return "must not run"
                    def respond(messages, info):
                        requests.append(info)
                        self.assertFalse(info.function_tools)
                        self.assertEqual([t.name for t in info.output_tools], ["lease_result"])
                        self.assertFalse(info.allow_text_output)
                        output = ToolCallPart("lease_result", {}, "invalid")
                        business = ToolCallPart("lookup", {}, "forbidden")
                        return ModelResponse(parts=[output, business] if kind == "mixed" else
                                             [business] if kind == "business" else [output])
                    sid = uuid4().hex
                    await self.assert_failed(self.runner(respond, tools=[lookup], limit=1), sid, streaming)
                    self.assertEqual(len(requests), 1)
                    self.assertFalse(calls)
                    self.assertFalse([i for i in project_session_events(await self.rows(sid)).items
                                      if i.kind in {"tool", "structured_output"}])

    async def test_request_limits_one_two_three_and_none_keep_final_output_and_correction_ceiling(self):
        """RUN-17: each permitted request is used once; unlimited is not unlimited retries."""
        for streaming in (False, True):
            for limit in (1, 2, 3, None):
                with self.subTest(streaming=streaming, limit=limit):
                    requests, executions = [], []
                    async def lookup():
                        executions.append(True)
                        return "queried"
                    def respond(messages, info):
                        requests.append(info)
                        # Unlimited mode exercises two ordinary calls then one output.
                        if info.function_tools and (limit is not None or len(requests) < 3):
                            return ModelResponse(parts=[ToolCallPart("lookup", {}, f"q-{len(requests)}")])
                        self.assertFalse(info.allow_text_output)
                        return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "accepted")])
                    runner = self.runner(respond, tools=[lookup], limit=limit)
                    sid = uuid4().hex
                    result, _ = await self.execute(runner, sid, stream=streaming)
                    expected = limit if limit is not None else 3
                    self.assertEqual(result["output"], {"ok": True})
                    self.assertEqual(len(requests), expected)
                    self.assertEqual(len(executions), expected - 1)
                    self.assertEqual(restored_request_count(await self.rows(sid)), expected)
                    failed_calls = []
                    def invalid(messages, info):
                        failed_calls.append(info)
                        return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{len(failed_calls)}")])
                    await self.assert_failed(self.runner(invalid, limit=limit), uuid4().hex, streaming)
                    self.assertEqual(len(failed_calls), min(limit, 3) if limit is not None else 3)

    async def test_summary_request_is_charged_once_and_final_gate_retains_only_output(self):
        """RUN-18: use the actual compaction request accounting capability boundary."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, executions = [], []
                async def lookup():
                    executions.append(True)
                def respond(messages, info):
                    requests.append(info)
                    self.assertFalse(info.function_tools)
                    self.assertEqual([t.name for t in info.output_tools], ["lease_result"])
                    self.assertFalse(info.allow_text_output)
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "summary-final")])
                runner = self.runner(respond, tools=[lookup], limit=2, capabilities=[SpendSummaryRequest()])
                sid = uuid4().hex
                result, _ = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(result["output"], {"ok": True})
                self.assertEqual(len(requests), 1)
                self.assertFalse(executions)
                rows = await self.rows(sid)
                self.assertEqual(restored_request_count(rows), 2)
                checkpoints = [((r.payload.get("message") or {}).get("metadata") or {}).get(BUDGET_METADATA)
                               for r in rows]
                self.assertTrue(any(c and c["run_requests"] == 2 for c in checkpoints))

    async def test_provider_schema_or_tool_rejection_has_no_text_native_fallback_or_config_mutation(self):
        """RUN-20: deterministic Provider HTTP errors, not a client business retry."""
        for streaming in (False, True):
            for body in ("tool use is not supported", "invalid JSON Schema"):
                with self.subTest(streaming=streaming, body=body):
                    requests = []
                    config = deepcopy(fixture.CONTRACT_DATA)
                    original = deepcopy(config)
                    def respond(messages, info):
                        requests.append(info)
                        raise ModelHTTPError(400, "protocol-fixture", body)
                    loader = AsyncMock(return_value=config)
                    runner = self.runner(respond, loader=loader)
                    sid = uuid4().hex
                    await self.assert_failed(runner, sid, streaming)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(config, original)
                    self.assertEqual(loader.await_count, 1)
                    self.assertEqual([t.name for t in requests[0].output_tools], ["lease_result"])
                    self.assertFalse(requests[0].allow_text_output)
                    self.assertFalse([r for r in await self.rows(sid) if r.event_type == "output/attempt"])

    async def test_progressively_exposed_tool_name_collision_fails_before_second_provider_request(self):
        """RUN-21: prepare exposes a conflicting tool only after an earlier query."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, collisions = [], []
                async def reveal(ctx, definition):
                    return (definition.__class__(name="lease_result", parameters_json_schema=definition.parameters_json_schema)
                            if requests else None)
                async def later_tool():
                    collisions.append(True)
                    return "must not be confused with output"
                async def lookup():
                    return "reveal resources"
                def respond(messages, info):
                    requests.append(info)
                    return ModelResponse(parts=[ToolCallPart("lookup", {}, "reveal")])
                runner = self.runner(respond, tools=[lookup, Tool(later_tool, prepare=reveal)])
                sid = uuid4().hex
                await self.assert_failed(runner, sid, streaming)
                self.assertEqual(len(requests), 1)
                self.assertFalse(collisions)
                errors = [r.payload for r in await self.rows(sid) if r.event_type == "execution/error"]
                self.assertEqual(errors[0]["code"], "agent_run_failed")
                self.assertIn("UserError", errors[0]["message"])

    async def test_ordinary_tool_model_retry_does_not_spend_output_corrections(self):
        """RUN-23: a real business tool retry remains distinct from output validation."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, tool_calls = [], []
                async def lookup():
                    tool_calls.append(True)
                    if len(tool_calls) == 1:
                        raise ModelRetry("disambiguate query")
                    return "found"
                def respond(messages, info):
                    requests.append(info)
                    if len(requests) <= 2:
                        return ModelResponse(parts=[ToolCallPart("lookup", {}, f"lookup-{len(requests)}")])
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "accepted")])
                runner = self.runner(respond, tools=[lookup])
                sid = uuid4().hex
                result, frames = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(result["output_info"]["attempt"], 1)
                self.assertEqual(len(tool_calls), 2)
                rows = await self.rows(sid)
                self.assertEqual(len([r for r in rows if r.event_type == "output/attempt"]), 1)
                self.assertFalse(any(BUDGET in (((r.payload.get("message") or {}).get("metadata")) or {}) for r in rows))
                if streaming:
                    life = [(f.get("type"), f.get("tool_call_id")) for f in frames
                            if f.get("type") in {"tool_call", "tool_call_removed", "tool_result"}]
                    self.assertEqual(life, [("tool_call", "lookup-1"), ("tool_call_removed", "lookup-1"),
                                            ("tool_call", "lookup-2"), ("tool_result", "lookup-2")])

    async def test_on_off_turns_interleave_and_next_user_uses_current_contract_without_replaying_output(self):
        """RUN-24 + DFR-17: separate Turns/sessions never borrow a cached schema."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                config = deepcopy(fixture.CONTRACT_DATA)
                async def load():
                    return deepcopy(config)
                def respond(messages, info):
                    if not info.output_tools:
                        return ModelResponse(parts=[TextPart("ordinary unchanged")])
                    name = info.output_tools[0].name
                    return ModelResponse(parts=[ToolCallPart(name,
                        {"ok": True} if name == "lease_result" else {"different": 0}, uuid4().hex)])
                runner = self.runner(respond, loader=load)
                sid, sid2 = uuid4().hex, uuid4().hex
                first, _ = await self.execute(runner, sid, stream=streaming)
                config = None
                second, _ = await self.execute(runner, sid, stream=streaming, message="second real user")
                self.assertEqual(second["output"], "ordinary unchanged")
                self.assertNotIn("output_info", second)
                config = {"name": "other_result", "description": "other schema", "json_schema": {
                    "type": "object", "properties": {"different": {"type": "integer"}}, "required": ["different"]}}
                third, _ = await self.execute(runner, sid2, stream=streaming)
                fourth, frames = await self.execute(runner, sid, stream=streaming, message="third real user")
                self.assertEqual(third["output"], {"different": 0})
                self.assertEqual(fourth["output_info"]["name"], "other_result")
                self.assertNotEqual(first["output_info"]["turn_id"], fourth["output_info"]["turn_id"])
                accepted = [r for r in await self.rows(sid) if r.event_type == "output/attempt" and r.payload["accepted"]]
                self.assertEqual([r.payload["name"] for r in accepted], ["lease_result", "other_result"])
                self.assertEqual(len([f for f in frames if f.get("type") == "output"]), 1 if streaming else 0)

    async def test_exhausted_response_has_budget_and_failure_fact_but_no_sdk_retry_prompt(self):
        """RUN-26: the third rejection is durable even though no retry message follows."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests = []
                def respond(messages, info):
                    requests.append(info)
                    return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{len(requests)}")])
                sid = uuid4().hex
                await self.assert_failed(self.runner(respond), sid, streaming)
                raw = await self.stores.coordinator.get_raw_history(sid, agent_id=self.agent_id)
                retries = [p.tool_call_id for m in raw if isinstance(m, ModelRequest)
                           for p in m.parts if isinstance(p, RetryPromptPart)]
                self.assertNotIn("bad-3", retries)
                rows = await self.rows(sid)
                self.assertEqual(restored_request_count(rows), 3)
                failures = [r for r in rows if r.event_type == "output/attempt"]
                self.assertEqual(len(failures), 3)
                self.assertFalse(any(r.payload["accepted"] for r in failures))
                budget = [((r.payload.get("message") or {}).get("metadata") or {}).get(BUDGET)
                          for r in rows if isinstance(r.payload.get("message"), dict)]
                self.assertTrue(any(b and b["rejected_responses"] == 3 and b["remaining_corrections"] == 0 for b in budget))

    async def test_duplicate_keys_and_nonfinite_json_are_rejected_by_real_runner(self):
        """SCH-21: JSON decoding never silently chooses duplicate keys/nonfinite values."""
        for streaming in (False, True):
            for raw in ('{', '{"ok":false,"ok":true}', '{"ok":NaN}', '{"ok":Infinity}'):
                with self.subTest(streaming=streaming, raw=raw):
                    calls = []
                    def respond(messages, info):
                        calls.append(True)
                        return ModelResponse(parts=[ToolCallPart("lease_result",
                            raw if len(calls) == 1 else {"ok": True}, f"json-{len(calls)}")])
                    sid = uuid4().hex
                    final, _ = await self.execute(self.runner(respond), sid, stream=streaming)
                    self.assertEqual(final["output"], {"ok": True})
                    self.assertEqual(final["output_info"]["attempt"], 2)
                    attempts = [r for r in await self.rows(sid) if r.event_type == "output/attempt"]
                    self.assertEqual(attempts[0].payload["errors"], [{"code": "invalid_json", "message": "invalid_json"}])
                    self.assertNotIn("output", attempts[0].payload)
                    self.assertEqual(len(calls), 2)

    async def test_native_search_mixed_output_is_rejected_without_claiming_remote_undo(self):
        """RUN-13: native results are Provider-side; the host cannot undo that work."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, remote_actions, host_actions = [], [], []
                async def lookup():
                    host_actions.append(True)
                    return "host side effect"
                def respond(messages, info):
                    requests.append(info)
                    output = ToolCallPart("lease_result", {"ok": True}, f"out-{len(requests)}")
                    if len(requests) == 1:
                        remote_actions.append("provider already executed web search")
                        return ModelResponse(parts=[NativeToolCallPart("web_search", {"query": "fixture"}, "native"),
                            NativeToolReturnPart("web_search", {"hits": []}, "native"), output,
                            ToolCallPart("lookup", {}, "never-host")])
                    return ModelResponse(parts=[output])
                sid = uuid4().hex
                result, _ = await self.execute(self.runner(respond, tools=[lookup]), sid, stream=streaming)
                self.assertEqual(result["output"], {"ok": True})
                self.assertEqual(len(remote_actions), 1)
                self.assertFalse(host_actions)
                self.assertEqual(len(requests), 2)
                history = project_session_events(await self.rows(sid))
                self.assertFalse([i for i in history.items if i.kind in {"tool", "plan"}])

    async def test_two_ui_pauses_with_query_between_share_turn_and_freeze_contract(self):
        """DFR-02: three SDK Runs, one Turn, a real ordinary query between pauses."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                calls, queries = [], []
                async def lookup():
                    queries.append(True)
                    return {"evidence": "query result"}
                def respond(messages, info):
                    calls.append(info)
                    step = len(calls)
                    name = "confirm" if step in (1, 3) else "lookup" if step == 2 else "lease_result"
                    return ModelResponse(parts=[ToolCallPart(name, {"ok": True} if step == 4 else {}, f"step-{step}")])
                runner = self.runner(respond, tools=[lookup], toolsets=[ExternalToolset([fixture.CONFIRM])])
                sid = uuid4().hex
                first, _ = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(first["status"], "pending")
                runner._output_config_loader = AsyncMock(side_effect=AssertionError("resume must freeze config"))
                second, _ = await self.execute(runner, sid, stream=streaming, message="",
                    results=DeferredToolResults(calls={"step-1": {"confirmed": True}}))
                self.assertEqual(second["status"], "pending")
                final, _ = await self.execute(runner, sid, stream=streaming, message="",
                    results=DeferredToolResults(calls={"step-3": {"confirmed": True}}))
                self.assertEqual(final["output"], {"ok": True})
                self.assertEqual(len(queries), 1)
                self.assertTrue(all(call.output_tools == calls[0].output_tools for call in calls))
                rows = await self.rows(sid)
                model_rows = [r for r in rows if r.event_type == "assistant/message"]
                self.assertEqual(len({r.turn_id for r in model_rows}), 1)
                self.assertEqual(len({r.run_id for r in model_rows}), 3)

    async def test_ui_rejected_and_cancelled_answers_remain_false_in_model_history(self):
        """DFR-03: ordinary external UI answers are passed through, not approved."""
        for streaming in (False, True):
            for answer in ({"confirmed": False}, {"cancelled": True, "reason": "user cancelled"}):
                with self.subTest(streaming=streaming, answer=answer):
                    calls = []
                    def respond(messages, info):
                        calls.append(info)
                        if len(calls) == 1:
                            return ModelResponse(parts=[ToolCallPart("confirm", {}, "answer")])
                        returns = [p.content for m in messages if isinstance(m, ModelRequest)
                                   for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_call_id == "answer"]
                        self.assertEqual(returns, [answer])
                        return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": False}, "accepted")])
                    runner = self.runner(respond, toolsets=[ExternalToolset([fixture.CONFIRM])])
                    sid = uuid4().hex
                    self.assertEqual((await self.execute(runner, sid, stream=streaming))[0]["status"], "pending")
                    final, _ = await self.execute(runner, sid, stream=streaming, message="",
                        results=DeferredToolResults(calls={"answer": answer}))
                    self.assertEqual(final["output"], {"ok": False})

    async def test_unknown_expired_and_replayed_answers_cannot_change_structured_pending_or_result(self):
        """DFR-09: wrong IDs and a prior UI ID stay outside execution facts."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                calls = []
                def respond(messages, info):
                    calls.append(info)
                    name = "confirm" if len(calls) <= 2 else "lease_result"
                    return ModelResponse(parts=[ToolCallPart(name, {"ok": True} if len(calls) == 3 else {}, f"id-{len(calls)}")])
                runner = self.runner(respond, toolsets=[ExternalToolset([fixture.CONFIRM])])
                sid = uuid4().hex
                await self.execute(runner, sid, stream=streaming)
                for answer in ({"unknown": True},):
                    before = [r.public_dict() for r in await self.rows(sid)]
                    with self.assertRaises(DeferredResultConflict):
                        await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls=answer))
                    self.assertEqual([r.public_dict() for r in await self.rows(sid)], before)
                await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls={"id-1": False}))
                for answer in ({"id-1": True}, {"id-2": True, "id-1": True}):
                    before = [r.public_dict() for r in await self.rows(sid)]
                    with self.assertRaises(DeferredResultConflict):
                        await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls=answer))
                    self.assertEqual([r.public_dict() for r in await self.rows(sid)], before)
                final, _ = await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls={"id-2": True}))
                self.assertEqual(final["output"], {"ok": True})
                before = [r.public_dict() for r in await self.rows(sid)]
                with self.assertRaises(DeferredResultConflict):
                    await self.execute(runner, sid, stream=streaming, message="", results=DeferredToolResults(calls={"id-2": True}))
                self.assertEqual([r.public_dict() for r in await self.rows(sid)], before)
                self.assertEqual(len(calls), 3)

    async def test_cancel_during_partial_output_stream_never_accepts_json(self):
        """DFR-13: explicit SDK cancellation after partial JSON, before completion."""
        reached = asyncio.Event()
        async def stream(messages, info):
            yield {0: DeltaToolCall(name="lease_result", json_args='{"ok":', tool_call_id="half")}
            reached.set()
            await asyncio.Event().wait()
        runner = self.runner(lambda *_: ModelResponse(parts=[]), stream_function=stream)
        sid = uuid4().hex
        task = asyncio.create_task(self.execute(runner, sid, stream=True))
        try:
            await asyncio.wait_for(reached.wait(), 3)
            self.assertTrue(runner._active_runs.request_cancel(agent_id=self.agent_id, session_id=sid, user_id="test-user"))
            result, frames = await asyncio.wait_for(task, 5)
            self.assertEqual(result.get("type"), "cancelled", result)
            self.assertFalse([f for f in frames if f.get("type") == "output" or f.get("type") == "final"])
            self.assertFalse([r for r in await self.rows(sid) if r.event_type == "output/attempt"])
            self.assertFalse([i for i in project_session_events(await self.rows(sid)).items if i.kind == "structured_output"])
            self.assertEqual(runner._active_runs.active_count(), 0)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_after_ui_resume_accepted_commit_before_final_preserves_completed_identity(self):
        """DFR-15: transport cancellation cannot undo the accepted result commit."""
        from deep_agent.agent.structured_output import StructuredOutputState
        calls = []
        def respond(messages, info):
            calls.append(True)
            return ModelResponse(parts=[ToolCallPart("confirm", {}, "confirmation")]
                if len(calls) == 1 else [ToolCallPart("lease_result", {"ok": True}, "accepted-after-ui")])
        runner = self.runner(respond, toolsets=[ExternalToolset([fixture.CONFIRM])])
        sid = uuid4().hex
        pending, _ = await self.execute(runner, sid, stream=True)
        self.assertEqual(pending["status"], "pending")
        original = StructuredOutputState.accept
        committed = []
        async def disconnect_after_accept(state, deps):
            result = await original(state, deps)
            committed.append(deepcopy(state.accepted))
            raise asyncio.CancelledError("transport disconnected after commit before final")
        with patch.object(StructuredOutputState, "accept", disconnect_after_accept):
            with self.assertRaises(asyncio.CancelledError):
                await self.execute(runner, sid, stream=True, message="",
                    results=DeferredToolResults(calls={"confirmation": True}))
        self.assertEqual(len(committed), 1)
        rows = await self.rows(sid)
        accepted = [r for r in rows if r.event_type == "output/attempt" and r.payload["accepted"]]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].event_id, committed[0]["output_id"])
        history = project_session_events(rows)
        output = next(i for i in history.items if i.kind == "structured_output")
        self.assertEqual((output.item_id, output.status, output.output),
                         (committed[0]["output_id"], "completed", {"ok": True}))
        self.assertFalse([r for r in rows if r.event_type == "execution/error"])
        self.assertEqual(runner._active_runs.active_count(), 0)

    async def test_precreated_empty_session_places_snapshot_on_first_real_user_with_attachment_and_title(self):
        """DFR-18: no fake user event is required for pre-created sessions."""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                sid = uuid4().hex
                async with self.stores.agent._write() as db:
                    db.add(ChatSession(session_id=sid, agent_id=self.agent_id, user_id="test-user",
                        next_sequence=1, source_type="internal"))
                self.assertEqual(await self.rows(sid), [])
                requests = []
                def respond(messages, info):
                    requests.append(messages)
                    self.assertIn("fixture.txt", str(messages))
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "first")])
                runner = self.runner(respond)
                deps = self.deps(sid)
                attachment = AttachmentRef(id="synthetic-file", name="fixture.txt", mime="text/plain", input_mode="reference")
                kwargs = dict(session_title="Explicit fixture title", attachments=[attachment])
                if streaming:
                    frames = [f async for f in runner.run_stream(sid, "Read reference", deps,
                        persister=self.persister, **kwargs)]
                    final = frames[-1]
                else:
                    final = await runner.run(sid, "Read reference", deps, **kwargs)
                self.assertEqual(final["output"], {"ok": True})
                rows = await self.rows(sid)
                snapshots = [r for r in rows if CONTRACT in (((r.payload.get("message") or {}).get("metadata")) or {})]
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].event_type, "user/message")
                self.assertEqual(await self.stores.coordinator.get_session_title(sid, agent_id=self.agent_id), "Explicit fixture title")
                self.assertIn("synthetic-file", deps.attachment_by_id)
                self.assertIn("fixture.txt", str(snapshots[0].payload))


class StructuredOutputDeferredOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """Ownership is enforced at the public API before Runner admission."""

    async def asyncSetUp(self):
        from tests.integration import test_structured_output_public_protocol as protocol
        await protocol.StructuredOutputPublicProtocolTests.asyncSetUp(self)

    async def asyncTearDown(self):
        from tests.integration import test_structured_output_public_protocol as protocol
        await protocol.StructuredOutputPublicProtocolTests.asyncTearDown(self)

    async def test_cross_user_ui_answer_is_rejected_before_execution_and_preserves_pending(self):
        """DFR-09: the foreign user's real HTTP request cannot consume an owner UI."""
        from httpx import AsyncClient
        from deep_agent.schemas.agent import AgentUpdate
        from tests.helpers.request_identity import TEST_USER_ID
        from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
        await self.ctx.stores.agent.update_unique(self._agent_id, AgentUpdate(
            structured_output_enabled=True,
            output_config={"name": "public_result", "description": "owner fixture", "json_schema": {"type": "object"}}),
            user_id=TEST_USER_ID)
        calls = []
        def respond(messages, info):
            calls.append(True)
            return ModelResponse(parts=[ToolCallPart("builtin__confirm", {"message": "fixture approval"}, "owner-ui")]
                if len(calls) == 1 else [ToolCallPart("public_result", {"done": True}, "owner-output")])
        async def stream(messages, info):
            for i, part in enumerate(respond(messages, info).parts):
                yield {i: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
        model = FunctionModel(respond, stream_function=stream)
        with patch("deep_agent.agent.factory._resolve_model", return_value=model), mock_request_disconnect():
            async with AsyncClient(transport=self._transport, base_url="http://test") as http:
                first = await http.post("/api/v1/chat/stream", json={"agent_id": self._agent_id,
                    "message": "owner asks", "capabilities": ["ui:builtin"]})
                self.assertEqual(first.status_code, 200, first.text)
                frames = parse_sse_events(first.text)
                final = next(f for f in reversed(frames) if f["type"] == "final")
                self.assertEqual(final["status"], "pending")
                sid = frames[0]["session_id"]
                before = [r.public_dict() for r in await self.ctx.stores.coordinator.list_all_events(sid)]
                answer = {"agent_id": self._agent_id, "session_id": sid, "capabilities": ["ui:builtin"],
                          "deferred_tool_results": [{"tool_call_id": "owner-ui", "result": {"confirmed": True}}]}
                foreign = await http.post("/api/v1/chat/stream", json=answer, headers={"user": "different-user"})
                self.assertEqual(foreign.status_code, 404, foreign.text)
                self.assertEqual([r.public_dict() for r in await self.ctx.stores.coordinator.list_all_events(sid)], before)
                self.assertEqual(len(calls), 1)
                owner = await http.post("/api/v1/chat/stream", json=answer)
                self.assertEqual(owner.status_code, 200, owner.text)
                done = next(f for f in reversed(parse_sse_events(owner.text)) if f["type"] == "final")
                self.assertEqual(done["output"], {"done": True})
                self.assertEqual(len(calls), 2)
