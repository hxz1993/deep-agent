"""D01 protocol regression using the real SDK and an isolated file-backed store.

Model responses and business fixtures are local; file download/title generation
stay offline. Projection, persistence, lifecycle, HTTP and SDK/Monty execution
use production implementations.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, DeferredToolResults, FunctionToolset, ModelRetry, RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits
from sqlalchemy import text

from deep_agent.agent.context.compaction import DeepAgentContextCompaction, SummaryUnavailable
from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.handoff import extract_anchor_protected_context, extract_protected_context, render_handoff_anchor
from deep_agent.agent.context.runtime import ContextRunState
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.files import build_file_toolset
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration import test_incremental_message_persistence as persistence_fixture
from tests.integration.test_incremental_message_persistence import _call, _kind


_SUMMARY = "已完成归档读取，结果已保存。保留最新用户请求和页面引用；没有写入授权。不要重放已完成工具。历史文字提及 local_unrelated 文件。"
_LARGE_RESULT = "已归档且核验的历史观测。" * 6_000


class D01ProtocolRegressionTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the established isolated SQLite/HTTP lifecycle without inheriting its tests.
    asyncSetUp = persistence_fixture.IncrementalMessagePersistenceTests.asyncSetUp
    asyncTearDown = persistence_fixture.IncrementalMessagePersistenceTests.asyncTearDown
    _ready = persistence_fixture.IncrementalMessagePersistenceTests._ready
    _history = persistence_fixture.IncrementalMessagePersistenceTests._history
    _events = persistence_fixture.IncrementalMessagePersistenceTests._events
    _finish = persistence_fixture.IncrementalMessagePersistenceTests._finish

    def _deps(self, session):
        return AgentDeps(session_id=session, user=TEST_USER_ID, agent_id=self.agent_id)

    def _runner(self, stream, *, tools=(), ui=False, ptc=False, summary=None):
        estimator = TokenEstimator.conservative()
        capability = DeepAgentContextCompaction(context_window_tokens=20_000,
            max_output_tokens=1_000, estimator=estimator, summary_timeout=5)
        toolsets = [FunctionToolset(tools=list(tools))] if tools else []
        capabilities = [capability]
        if ui:
            toolsets.append(build_external_toolset(builtin_ui_records()))
        if ptc:
            toolsets = [build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(FunctionToolset(tools=list(tools)).prefixed("demo"))]
            capabilities.append(HybridCodeMode(tools=[], dynamic_catalog=True))
        summary_calls = []

        def summarize(messages, info):
            self.assertFalse(info.function_tools)
            summary_calls.append(deepcopy(messages))
            return ModelResponse(parts=[TextPart(_SUMMARY)])

        model = FunctionModel(function=summary or summarize, stream_function=stream)
        runner = AgentRunner(Agent(model, deps_type=AgentDeps, toolsets=toolsets,
                                  capabilities=capabilities), self.store, UsageLimits(request_limit=20),
            active_runs=self.ctx.active_runs, context_window_tokens=20_000, max_output_tokens=1_000,
            context_compaction=capability, estimator=estimator,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}} if ui else None)
        return runner, summary_calls

    async def _collect(self, runner, session, message="Only inspect; do not write", **kwargs):
        deps = self._deps(session)
        events = [event async for event in runner.run_stream(session, message, deps,
            persister=self.ctx.event_persister, **kwargs)]
        return events, deps

    async def test_http_sse_keeps_progress_persisted_event_and_nullable_target_contract(self):
        request_count = 0

        async def archive() -> str:
            return _LARGE_RESULT

        async def stream(messages, info):
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                yield _call("archive", "http-once")
            else:
                yield "HTTP_DONE"

        runner, summaries = self._runner(stream, tools=[archive])
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new_callable=AsyncMock, return_value=runner), mock_request_disconnect():
            response = await self.http.post("/api/v1/chat/stream", json={
                "agent_id": self.agent_id, "session_id": "d01-http-sse", "message": "Inspect the archive once"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = parse_sse_events(response.text)
        self.assertEqual(events[-1]["type"], "final", events[-1])
        self.assertEqual(len(summaries), 1)
        progress = [event for event in events if event["type"] == "context_compaction_progress"]
        self.assertTrue(progress)
        self.assertTrue(all(not event["persisted"] for event in progress))
        target_events = [event for event in progress if "target_tokens" in event]
        self.assertTrue(target_events)
        self.assertTrue(all(event["target_tokens"] is None for event in target_events))
        completed = [event for event in events if event["type"] == "session_event_updated"
                     and event["event"]["event_type"] == "compaction" and event["event"]["status"] == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0]["persisted"])
        self.assertTrue(events[-1]["context"]["compaction"]["summary_applied"])
        self.assertTrue(events[-1]["context"]["compaction"]["compaction_event_saved"])

    async def test_three_same_turn_compactions_reopen_user_refs_and_original_attachment_authority(self):
        session = "d01-three-compactions"
        executed, requests = [], []

        async def archive(page: int) -> str:
            executed.append(page)
            return f"page={page}\n{_LARGE_RESULT}"

        async def stream(messages, info):
            requests.append(deepcopy(messages))
            if len(requests) > 1:
                committed = [row for row in await self.store.list_all_events(session)
                             if row.event_type == "compaction" and row.status == "completed"]
                self.assertEqual(len(committed), len(requests) - 1,
                                 "Each checkpoint must be durable before the next model request")
            if len(requests) <= 3:
                yield _call("archive", f"archive-{len(requests)}", {"page": len(requests)})
            else:
                yield "DONE_WITHOUT_WRITING"

        runner, summary_calls = self._runner(stream, tools=[archive])
        user_text = "最新要求：只核对 B2=42，不允许写入。"
        reference = ContextReference(key="selection", type="deeptable.selection", schema_version="2.0",
            label="Budget!B2", payload={"cells": [{"address": "B2", "value": 42}]},
            metadata={"secret": "CONTEXT_METADATA_MUST_STAY_HIDDEN"})
        attachment = AttachmentRef(id="local_d01_owned", name="budget.txt", mime="text/plain")
        events, _ = await self._collect(runner, session, user_text,
            attachments=[attachment], context_refs=[reference])
        self.assertEqual(events[-1]["type"], "final", events[-1])
        self.assertEqual(executed, [1, 2, 3])
        self.assertEqual(len(summary_calls), 3)
        compactions = [row for row in await self.store.list_all_events(session)
                       if row.event_type == "compaction" and row.status == "completed"]
        through = 0
        previous = None
        for event in compactions:
            source = event.payload["source"]
            self.assertEqual(source["from_sequence"], through + 1)
            self.assertGreater(source["through_sequence"], through)
            self.assertEqual(len(source["history_hash"]), 64)
            self.assertEqual(event.payload["previous_compaction_event_id"], previous)
            self.assertEqual(event.payload["replacement"]["format"], "handoff-checkpoint-v2")
            through, previous = source["through_sequence"], event.event_id
        raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
        self.assertEqual(sum(isinstance(p, UserPromptPart) for m in raw for p in m.parts), 1)
        self.assertEqual([p.tool_call_id for m in raw for p in m.parts if isinstance(p, ToolCallPart)],
                         ["archive-1", "archive-2", "archive-3"])
        visible = await self.store.get_history(session, agent_id=self.agent_id)
        protected = extract_anchor_protected_context(visible)
        self.assertEqual(protected["source_sha256"], extract_protected_context(raw)["source_sha256"])
        text = json.dumps(protected, ensure_ascii=False)
        self.assertIn(user_text, text)
        self.assertIn("Budget!B2", text)
        self.assertIn('\\"value\\":42', text)
        self.assertNotIn("CONTEXT_METADATA_MUST_STAY_HIDDEN", text)
        for request in requests[1:]:
            self.assertIn(user_text, str(request))
            self.assertNotIn("CONTEXT_METADATA_MUST_STAY_HIDDEN", str(request))
        reopened = self._deps(session)
        reopened.run_id = "d01-reopened"
        await runner._prepare_history(session, "", reopened)
        self.assertTrue(reopened.has_context_references)
        self.assertEqual(set(reopened.attachment_by_id), {attachment.id})
        ctx = RunContext(deps=reopened, model=runner.agent.model, usage=RunUsage())
        file_tools = await build_file_toolset().get_tools(ctx)
        with patch("deep_agent.files.toolset._download_file", new_callable=AsyncMock, return_value=b"B2=42") as download:
            result = await file_tools["read_file"].call_func({"resource": {"type": "attachment", "file_id": attachment.id}}, ctx)
            self.assertEqual(result.content, "B2=42")
            with self.assertRaises(ModelRetry):
                await file_tools["read_file"].call_func({"resource": {"type": "attachment", "file_id": "local_unrelated"}}, ctx)
            download.assert_awaited_once()
        history = await self._history(session)
        self.assertEqual(len(_kind(history, "user_message")), 1)
        self.assertEqual([item["call_id"] for item in _kind(history, "tool")],
                         ["archive-1", "archive-2", "archive-3"])
        denied = await self.http.get(f"/api/v1/sessions/{session}/history", headers={"user": "other-owner"})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(len(compactions), 3)

    async def test_compaction_before_deferred_ui_resume_does_not_replay_completed_calls(self):
        session, executed, requests = "d01-ui-resume", [], []

        async def archive() -> str:
            executed.append("archive")
            return _LARGE_RESULT

        async def stream(messages, info):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                yield _call("archive", "archive-before-ui")
            elif len(requests) == 2:
                yield _call("builtin__confirm", "confirm-once", {"message": "Confirm synthetic action"})
            else:
                returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
                           and p.tool_call_id == "confirm-once"]
                self.assertEqual(len(returns), 1)
                yield "RESUMED_ONCE"

        runner, summaries = self._runner(stream, tools=[archive], ui=True)
        pending, first_deps = await self._collect(runner, session)
        self.assertEqual(pending[-1]["status"], "pending", pending[-1])
        self.assertEqual(len(summaries), 1)
        self.assertTrue(pending[-1]["context"]["compaction"]["compaction_event_saved"])
        self.assertEqual(len([row for row in await self.store.list_all_events(session)
                              if row.event_type == "compaction" and row.status == "completed"]), 1)
        resumed, next_deps = await self._collect(runner, session, "",
            deferred_tool_results=DeferredToolResults(calls={"confirm-once": {"confirmed": True}}))
        self.assertEqual(resumed[-1]["type"], "final", resumed[-1])
        self.assertEqual(first_deps.turn_id, next_deps.turn_id)
        self.assertNotEqual(first_deps.run_id, next_deps.run_id)
        self.assertEqual(executed, ["archive"])
        self.assertEqual(len(summaries), 1, "A saved checkpoint must avoid re-summarizing the same archive on UI resume")
        history = await self._history(session)
        self.assertEqual(len(_kind(history, "user_message")), 1)
        self.assertEqual([item["call_id"] for item in _kind(history, "tool")],
                         ["archive-before-ui", "confirm-once"])

    async def test_ptc_nested_side_effect_survives_compaction_without_reexecution(self):
        session, writes, request_count = "d01-ptc", [], 0

        async def record_once() -> dict[str, str]:
            writes.append("LOCAL_SYNTHETIC_WRITE")
            return {"receipt": "ACK-D01", "archive": _LARGE_RESULT}

        async def stream(messages, info):
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                yield _call("run_code", "ptc-once", {"code": "result = await demo_record_once()\nresult"})
            else:
                yield "ACK-D01_ALREADY_APPLIED"

        runner, summaries = self._runner(stream, tools=[record_once], ptc=True)
        events, _ = await self._collect(runner, session, "Apply exactly one synthetic local update")
        self.assertEqual(events[-1]["type"], "final", events[-1])
        self.assertEqual(writes, ["LOCAL_SYNTHETIC_WRITE"])
        self.assertEqual(len(summaries), 1)
        raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
        results = [p for m in raw for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "run_code"]
        self.assertEqual(len(results), 1)
        self.assertIn("ACK-D01", str(results[0].content))
        self.assertTrue(results[0].metadata["code_mode"])

    async def test_cancel_during_summary_closes_lifecycle_without_replaying_or_checkpointing(self):
        await self._assert_cancelled_summary(transport_disconnect=False)

    async def test_disconnect_during_summary_closes_lifecycle_without_checkpointing(self):
        await self._assert_cancelled_summary(transport_disconnect=True)

    async def _assert_cancelled_summary(self, *, transport_disconnect):
        session, executed = "d01-disconnect-summary" if transport_disconnect else "d01-cancel-summary", []
        summarizing = asyncio.Event()

        async def archive() -> str:
            executed.append("archive")
            return _LARGE_RESULT

        async def summary(messages, info):
            summarizing.set()
            await asyncio.Event().wait()
            return ModelResponse(parts=[TextPart("unreachable")])

        async def stream(messages, info):
            yield _call("archive", "completed-before-cancel")

        runner, _ = self._runner(stream, tools=[archive], summary=summary)
        result = []

        async def collect():
            events, _ = await self._collect(runner, session)
            result.extend(events)

        task = asyncio.create_task(collect())
        self.tasks.append(task)
        await self._ready(summarizing, task)
        if transport_disconnect:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        else:
            response = await self.http.post(f"/api/v1/sessions/{session}/cancel", params={"agent_id": self.agent_id})
            self.assertEqual(response.status_code, 200)
            await self._finish(task)
            self.assertEqual(result[-1]["type"], "cancelled", result[-1])
        self.assertEqual(executed, ["archive"])
        compactions = [row for row in await self.store.list_all_events(session) if row.event_type == "compaction"]
        self.assertEqual(len(compactions), 1)
        self.assertIn(compactions[0].status, {"cancelled", "interrupted"})
        self.assertIsNone(compactions[0].payload.get("replacement"))
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
        visible = await self.store.get_history(session, agent_id=self.agent_id)
        self.assertEqual(raw, visible)

    async def test_source_append_during_summary_rejects_stale_checkpoint(self):
        session = "d01-stale-source"
        raw = [ModelRequest(parts=[UserPromptPart("original request")]),
               ModelResponse(parts=[TextPart("completed evidence")]),
               ModelResponse(parts=[TextPart("retained tail")])]
        await self.store.append(session, raw, agent_id=self.agent_id, user_id=TEST_USER_ID,
                                turn_id="source-turn", run_id="source-run")
        lifecycle = await self.store.append_lifecycle(session, event_type="compaction", status="running",
            agent_id=self.agent_id, user_id=TEST_USER_ID, turn_id="source-turn", run_id="source-run",
            payload={"trigger": "pressure"})
        protected = extract_protected_context(raw, cutoff=2)
        projected = [render_handoff_anchor(_SUMMARY, protected_context=protected), raw[-1]]
        await self.store.append(session, [ModelResponse(parts=[TextPart("concurrently persisted response")])],
            agent_id=self.agent_id, user_id=TEST_USER_ID, turn_id="source-turn", run_id="other-run")
        event = await self.store.persist_compaction_projection(session, agent_id=self.agent_id,
            user_id=TEST_USER_ID, raw_history=raw, projected_history=projected, summary_markdown=_SUMMARY,
            trigger="pressure", model_id=None, estimated_before=100, estimated_after=50,
            budget_limit_tokens=1000, context_window_tokens=2000, preserved_tail_tokens=20,
            duration_ms=1, compaction_event_id=lifecycle.event_id)
        self.assertIsNone(event)
        saved = next(row for row in await self.store.list_all_events(session) if row.event_id == lifecycle.event_id)
        self.assertEqual(saved.status, "stale")
        self.assertEqual(saved.payload["error"]["code"], "source_history_changed")
        self.assertNotIn("replacement", saved.payload)
        self.assertEqual(await self.store.get_history(session, agent_id=self.agent_id),
                         await self.store.get_raw_history(session, agent_id=self.agent_id))

    async def test_pressure_callback_rejects_assistant_appended_after_summary_snapshot(self):
        await self._assert_pressure_callback_rejects_late_event(user_event=False)

    async def test_pressure_callback_rejects_user_appended_after_summary_snapshot(self):
        await self._assert_pressure_callback_rejects_late_event(user_event=True)

    async def test_pressure_rejects_assistant_appended_before_source_refresh(self):
        await self._assert_pressure_rejects_early_source_change(user_event=False)

    async def test_pressure_rejects_user_appended_before_source_refresh(self):
        await self._assert_pressure_rejects_early_source_change(user_event=True)

    async def test_pressure_source_validation_compares_content_at_equal_message_count(self):
        runner, deps, raw = await self._overflow_fixture("d01-equal-size-source")
        unseen = deepcopy(raw)
        unseen[-1].parts[0].content += " changed business result"
        request = ModelRequestContext(model=runner.agent.model, messages=unseen,
            model_settings=None, model_request_parameters=ModelRequestParameters())
        ctx = RunContext(deps=deps, model=runner.agent.model, usage=RunUsage(),
                         usage_limits=deps.context_state.usage_limits)
        with self.assertRaisesRegex(SummaryUnavailable, "source_history_changed"):
            await runner._context_compaction.before_model_request(ctx, request)
        self.assertIsNone(deps.context_state.committed_event)
        self.assertEqual(await self.store.get_history("d01-equal-size-source", agent_id=self.agent_id), raw)

    async def _assert_pressure_rejects_early_source_change(self, *, user_event):
        session = "d01-early-user" if user_event else "d01-early-assistant"
        summary_calls = []

        def summary(messages, info):
            summary_calls.append(messages)
            return ModelResponse(parts=[TextPart(_SUMMARY)])

        runner, deps, raw = await self._overflow_fixture(session, summary=summary)
        request = ModelRequestContext(model=runner.agent.model, messages=deepcopy(raw),
            model_settings=None, model_request_parameters=ModelRequestParameters())
        late = (ModelRequest(parts=[UserPromptPart("UNSEEN_SOURCE_FACT: latest correction")])
                if user_event else ModelResponse(parts=[TextPart("UNSEEN_SOURCE_FACT: completed write")]))
        await self.store.append(session, [late], agent_id=self.agent_id, user_id=TEST_USER_ID,
                                turn_id="late-turn", run_id="late-run")
        ctx = RunContext(deps=deps, model=runner.agent.model, usage=RunUsage(),
                         usage_limits=deps.context_state.usage_limits)
        with self.assertRaisesRegex(SummaryUnavailable, "source_history_changed"):
            await runner._context_compaction.before_model_request(ctx, request)
        self.assertEqual(summary_calls, [], "Source validation must precede the summary model call")
        self.assertIsNone(deps.context_state.committed_event)
        compactions = [row for row in await self.store.list_all_events(session) if row.event_type == "compaction"]
        self.assertEqual([row.status for row in compactions], ["failed"])
        self.assertIsNone(compactions[0].payload.get("replacement"))
        visible = await self.store.get_history(session, agent_id=self.agent_id)
        self.assertEqual(visible, await self.store.get_raw_history(session, agent_id=self.agent_id))
        self.assertIn("UNSEEN_SOURCE_FACT", str(visible))

    async def _assert_pressure_callback_rejects_late_event(self, *, user_event):
        session = "d01-late-user" if user_event else "d01-late-assistant"
        summarizing, release = asyncio.Event(), asyncio.Event()
        requests = 0

        async def archive() -> str:
            return _LARGE_RESULT

        async def summary(messages, info):
            self.assertNotIn("UNSUMMARIZED_LATE_FACT", str(messages))
            summarizing.set()
            await release.wait()
            return ModelResponse(parts=[TextPart(_SUMMARY)])

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield _call("archive", "before-late-event")
            else:
                yield "MUST_NOT_CONTINUE_WITH_STALE_CHECKPOINT"

        runner, _ = self._runner(stream, tools=[archive], summary=summary)
        collected = []

        async def collect():
            events, _ = await self._collect(runner, session)
            collected.extend(events)

        task = asyncio.create_task(collect())
        self.tasks.append(task)
        await self._ready(summarizing, task)
        late = (ModelRequest(parts=[UserPromptPart("UNSUMMARIZED_LATE_FACT: latest user correction")])
                if user_event else ModelResponse(parts=[TextPart("UNSUMMARIZED_LATE_FACT: completed side effect")]))
        await self.store.append(session, [late], agent_id=self.agent_id, user_id=TEST_USER_ID,
                                turn_id="late-turn", run_id="late-run")
        await self._finish(task, release)
        self.assertEqual(collected[-1]["type"], "error", collected[-1])
        self.assertEqual(requests, 1, "No model request may consume a checkpoint made from stale source data")
        compactions = [row for row in await self.store.list_all_events(session) if row.event_type == "compaction"]
        self.assertEqual(len(compactions), 1)
        self.assertIn(compactions[0].status, {"stale", "failed"})
        self.assertIsNone(compactions[0].payload.get("replacement"))
        visible = await self.store.get_history(session, agent_id=self.agent_id)
        raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
        self.assertEqual(visible, raw)
        self.assertIn("UNSUMMARIZED_LATE_FACT", str(visible))

    async def _overflow_fixture(self, session, *, summary=None):
        raw = [ModelRequest(parts=[UserPromptPart("Read the archive without writing")]),
               ModelResponse(parts=[ToolCallPart("archive", {}, "historical-archive")]),
               ModelRequest(parts=[ToolReturnPart("archive", _LARGE_RESULT, "historical-archive")])]
        await self.store.append(session, raw, agent_id=self.agent_id, user_id=TEST_USER_ID,
                                turn_id="overflow-turn", run_id="overflow-seed")

        async def unused_stream(messages, info):
            raise AssertionError("Recovery must not execute a main model request")
            yield "unreachable"

        runner, _ = self._runner(unused_stream, summary=summary)
        deps = self._deps(session)
        deps.run_id, deps.turn_id = "overflow-run", "overflow-turn"
        deps.context_state = ContextRunState(raw_history=raw, estimator=TokenEstimator.conservative(),
                                             usage_limits=UsageLimits(request_limit=10))
        runner._bind_compaction_lifecycle(session_id=session, deps=deps)
        return runner, deps, raw

    async def test_overflow_summary_cancellation_closes_recovery_lifecycle(self):
        session = "d01-overflow-cancel"
        summarizing = asyncio.Event()

        async def summary(messages, info):
            summarizing.set()
            await asyncio.Event().wait()
            return ModelResponse(parts=[TextPart("unreachable")])

        runner, deps, raw = await self._overflow_fixture(session, summary=summary)
        task = asyncio.create_task(runner._recover_provider_overflow(session, deps,
            message_history=raw, raw_history=raw, usage=RunUsage()))
        self.tasks.append(task)
        await self._ready(summarizing, task)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        compactions = [row for row in await self.store.list_all_events(session) if row.event_type == "compaction"]
        self.assertEqual(len(compactions), 1)
        self.assertEqual(compactions[0].status, "cancelled")
        self.assertIsNone(compactions[0].payload.get("replacement"))
        self.assertIsNone(deps.context_state.committed_event)
        self.assertEqual(await self.store.get_history(session, agent_id=self.agent_id), raw)

    async def test_overflow_database_commit_failure_closes_recovery_without_checkpoint(self):
        session = "d01-overflow-save-failure"
        runner, deps, raw = await self._overflow_fixture(session)
        # Fail the actual SQLite write, while keeping lifecycle failure updates
        # and original message reads available. No projection/store method mocks.
        async with self.ctx.db_engine.begin() as connection:
            await connection.execute(text("""
                CREATE TRIGGER d01_reject_compaction_commit
                BEFORE UPDATE OF status ON chat_session_events
                WHEN NEW.event_type = 'compaction' AND NEW.status = 'completed'
                BEGIN SELECT RAISE(FAIL, 'D01 synthetic compaction commit failure'); END
            """))
        recovered = await runner._recover_provider_overflow(session, deps,
            message_history=raw, raw_history=raw, usage=RunUsage())
        self.assertIsNone(recovered)
        compactions = [row for row in await self.store.list_all_events(session) if row.event_type == "compaction"]
        self.assertEqual(len(compactions), 1)
        self.assertEqual(compactions[0].status, "failed")
        self.assertEqual(compactions[0].payload["error"]["code"], "IntegrityError")
        self.assertEqual(compactions[0].payload["budget"]["budget_limit_tokens"],
                         deps.context_state.compaction_budget_limit_tokens)
        self.assertGreater(compactions[0].payload["budget"]["budget_limit_tokens"], 0)
        self.assertNotIn("budget_limit_tokens", deps.context_state.telemetry.as_dict())
        self.assertIsNone(compactions[0].payload.get("replacement"))
        self.assertIsNone(deps.context_state.committed_event)
        self.assertEqual(await self.store.get_history(session, agent_id=self.agent_id), raw)

    async def test_success_after_failed_pressure_attempt_clears_attempt_status_preserving_counters(self):
        session = "d01-pressure-retry-status"
        estimator = TokenEstimator.conservative()
        # Pick genuine pressure that still fits: the first failure may safely
        # fall back to the original history, then the next attempt can succeed.
        for count in range(1_000, 15_000, 200):
            raw = [ModelRequest(parts=[UserPromptPart("original task")]),
                   ModelResponse(parts=[ToolCallPart("archive", {}, "old-archive")]),
                   ModelRequest(parts=[ToolReturnPart("archive", "archived observation " * count, "old-archive")]),
                   ModelRequest(parts=[UserPromptPart("latest task")])]
            budget = build_context_budget(raw, context_window_tokens=20_000,
                max_output_tokens=1_000, estimator=estimator)
            if budget.trigger_tokens < budget.estimated_request_tokens < budget.usable_request_tokens:
                break
        else:
            self.fail("Fixture could not reach a safe pressure interval")
        await self.store.append(session, raw, agent_id=self.agent_id, user_id=TEST_USER_ID,
                                turn_id="retry-turn", run_id="retry-seed")
        summary_calls = 0

        def summary(messages, info):
            nonlocal summary_calls
            summary_calls += 1
            if summary_calls == 1:
                raise ModelHTTPError(400, "fixture", "synthetic non-retryable summary failure")
            return ModelResponse(parts=[TextPart(_SUMMARY)])

        async def unused_stream(messages, info):
            raise AssertionError("Only the real summary SDK call should execute")
            yield "unreachable"

        runner, _ = self._runner(unused_stream, summary=summary)
        deps = self._deps(session)
        deps.run_id, deps.turn_id = "retry-run", "retry-turn"
        limits = UsageLimits(request_limit=10)
        state = ContextRunState(raw_history=raw, estimator=estimator, usage_limits=limits)
        deps.context_state = state
        runner._bind_compaction_lifecycle(session_id=session, deps=deps)
        ctx = RunContext(deps=deps, model=runner.agent.model, usage=RunUsage(), usage_limits=limits)
        telemetry = state.telemetry
        for attempt in (1, 2):
            request = ModelRequestContext(model=runner.agent.model, messages=deepcopy(raw),
                model_settings=None, model_request_parameters=ModelRequestParameters())
            projected = await runner._context_compaction.before_model_request(ctx, request)
            if attempt == 1:
                self.assertEqual(projected.messages, raw)
                self.assertTrue(telemetry.summary_failed)
                self.assertEqual(telemetry.strategy, "original_history_fallback")
                self.assertIsNone(state.committed_event)
        self.assertIs(state.telemetry, telemetry)
        self.assertEqual(telemetry.trigger_count, 2)
        self.assertEqual(telemetry.summary_calls, 2)
        self.assertEqual(summary_calls, 2)
        self.assertTrue(telemetry.summary_applied)
        self.assertTrue(telemetry.candidate_valid)
        self.assertFalse(telemetry.summary_failed)
        self.assertIsNone(telemetry.failure)
        self.assertIsNone(telemetry.fallback_reason)
        self.assertEqual(telemetry.strategy, "tiered_summary")
        self.assertEqual(state.committed_event.status, "completed")
        self.assertEqual([row.status for row in await self.store.list_all_events(session)
                          if row.event_type == "compaction"], ["failed", "completed"])

    async def test_v1_checkpoint_remains_readable_with_continuous_original_tail(self):
        session = "d01-v1-checkpoint"
        raw = [ModelRequest(parts=[UserPromptPart("old request")]),
               ModelResponse(parts=[TextPart("old answer")]),
               ModelRequest(parts=[UserPromptPart("latest original request")])]
        rows = await self.store.append(session, raw, agent_id=self.agent_id, user_id=TEST_USER_ID,
                                       turn_id="old-turn", run_id="old-run")
        await self.store.append_lifecycle(session, event_type="compaction", status="completed",
            agent_id=self.agent_id, user_id=TEST_USER_ID, turn_id="old-turn", run_id=None,
            payload={"source": {"through_sequence": rows[1].sequence}, "replacement": {
                "format": "dsh-structured-checkpoint-v1", "summary_markdown": "LEGACY_SUMMARY",
                "tool_evidence": [], "context_evidence": [], "attachment_evidence": []}})
        history = await self.store.get_history(session, agent_id=self.agent_id)
        self.assertEqual(len(history), 2)
        self.assertIn("LEGACY_SUMMARY", history[0].parts[0].content)
        self.assertEqual(history[1], raw[-1])
        self.assertEqual(extract_anchor_protected_context(history), {})
        self.assertEqual(await self.store.get_raw_history(session, agent_id=self.agent_id), raw)


if __name__ == "__main__":
    unittest.main()
