from __future__ import annotations

import asyncio
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, FunctionToolset, ModelRequestNode, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, NativeToolCallPart, NativeToolReturnPart,
    PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ThinkingPart,
    ThinkingPartDelta, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.run_progress import (
    RunProgressCapability, RunProgressPersistenceError, StreamRunProgressCapability,
)
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.db.run_ownership import live_run, owned_progress_payload, progress_is_abandoned, run_is_live
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.files.storage import LocalFileStore


class RecordingStore:
    def __init__(self):
        self.writes = []
        self.touches = 0
        self.reject = False
        self.fail_when = lambda messages: False
        self.lease_valid = True

    async def save_run_progress(self, session_id, **kwargs):
        if self.reject:
            return None
        if self.fail_when(kwargs["messages"]):
            raise RuntimeError("simulated database outage")
        self.writes.append(deepcopy(kwargs))
        return SimpleNamespace(status="running", event_id="progress")

    async def touch_run_progress(self, session_id, *, run_id):
        self.touches += 1
        return self.lease_valid


def parts(messages, kind):
    return [part for message in messages for part in message.parts if isinstance(part, kind)]


class RunProgressCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = RecordingStore()
        self.deps = AgentDeps(session_id="session", agent_id="agent", user="user",
                              run_id="run", turn_id="turn")
        self.ctx = RunContext(deps=self.deps, model=TestModel(), usage=RunUsage(), messages=[])

    def capability(self, *, stream=True, history_length=0):
        cls = StreamRunProgressCapability if stream else RunProgressCapability
        return cls(self.store, history_length=history_length, skip_user_prompt=True)

    async def asyncTearDown(self):
        await asyncio.sleep(0)
        self.assertFalse([task for task in asyncio.all_tasks()
                          if task.get_name() == "deepagent-run-progress-writer" and not task.done()])

    async def test_thinking_only_partial_is_durable_not_final_answer(self):
        cap = self.capability()

        async def stream():
            yield PartStartEvent(index=0, part=ThinkingPart("first"))
            yield PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" second"))

        async for _ in cap.wrap_run_event_stream(self.ctx, stream=stream()):
            pass
        self.assertEqual(parts(cap.messages, ThinkingPart)[0].content, "first second")
        self.assertEqual(cap.messages[-1].state, "incomplete")
        self.assertEqual(cap.canonical_messages([]), [])

    async def test_native_search_parts_preserve_order_without_exposing_local_args(self):
        cap = self.capability()
        native_call = NativeToolCallPart("web_search", {"query": "sample"}, "native")
        native_result = NativeToolReturnPart("web_search", {"found": 1}, "native")

        async def stream():
            for index, part in enumerate([TextPart("lookup"), native_call, native_result,
                                          ToolCallPart("ui", {"unsafe": 1}, "invalid"),
                                          TextPart("answer")]):
                yield PartStartEvent(index=index, part=part)

        async for _ in cap.wrap_run_event_stream(self.ctx, stream=stream()):
            pass
        self.assertEqual([part.part_kind for part in cap.messages[-1].parts],
                         ["text", "builtin-tool-call", "builtin-tool-return", "text"])

    async def test_compaction_retains_completed_current_run_ledger(self):
        cap = self.capability(history_length=20)
        request = ModelRequest(parts=[UserPromptPart("new")], run_id="run")
        await cap._prepare_model_node(self.ctx, ModelRequestNode(request=request))
        response = ModelResponse(parts=[TextPart("first")], run_id="run")
        await cap.after_model_request(self.ctx, request_context=None, response=response)
        next_request = ModelRequest(parts=[ToolReturnPart("read", "value", "call")], run_id="run")
        await cap.after_node_run(self.ctx, node=object(), result=ModelRequestNode(request=next_request))
        # A replacement much shorter than the old history length, as compaction does.
        self.ctx.messages[:] = [ModelRequest(parts=[UserPromptPart("summary")]), next_request]
        await cap._prepare_model_node(self.ctx, ModelRequestNode(request=next_request))
        final = ModelResponse(parts=[TextPart("final")], run_id="run")
        await cap.after_model_request(self.ctx, request_context=None, response=final)
        self.assertEqual([p.content for p in parts(cap.messages, TextPart)], ["first", "final"])
        canonical = cap.canonical_messages([deepcopy(next_request), deepcopy(final)])
        self.assertEqual(len(canonical), 4)
        self.assertEqual([p.content for p in parts(canonical, TextPart)], ["first", "final"])
        self.assertEqual(len(parts(canonical, ToolReturnPart)), 1)

    async def test_tool_id_reuse_does_not_accept_new_unvalidated_call(self):
        cap = self.capability()
        first = ModelResponse(parts=[ToolCallPart("ui", {"valid": 1}, "same")])
        self.ctx.messages.append(first)
        await cap.after_model_request(self.ctx, request_context=None, response=first)
        await cap.before_tool_execute(self.ctx, call=first.parts[0], tool_def=None, args={})
        second = ModelResponse(parts=[ToolCallPart("ui", {"invalid": 1}, "same")])
        self.ctx.messages.append(second)
        await cap.after_model_request(self.ctx, request_context=None, response=second)
        self.assertEqual(len(parts(cap.messages, ToolCallPart)), 1)
        self.assertEqual(parts(cap.messages, ToolCallPart)[0].args, {"valid": 1})

    async def test_delta_reuses_frozen_large_tool_prefix_until_official_boundary(self):
        cap = self.capability()
        request = ModelRequest(parts=[ToolReturnPart("read", "large" * 50_000, "call")])
        cap._record(request)
        cap._stage(self.ctx)
        frozen = cap._pending[0]
        # A live SDK object changes later; the cached official prefix is already
        # frozen and cannot be retroactively changed in a pending DB snapshot.
        request.parts[0].content = "updated"
        cap._parts[0] = TextPart("first token")
        for index in range(100):
            cap._parts[0] = TextPart(str(index))
            cap._stage(self.ctx)
            self.assertIs(cap._pending[0], frozen)
        self.assertEqual(frozen.parts[0].content, "large" * 50_000)
        cap._record(request)
        cap._stage(self.ctx)
        self.assertEqual(cap._pending[0].parts[0].content, "updated")

    async def test_resume_without_prompt_does_not_copy_previous_request(self):
        cap = self.capability()
        previous = ModelRequest(parts=[ToolReturnPart("read", "old", "old")])
        await cap._prepare_model_node(self.ctx, ModelRequestNode(request=previous, is_resuming_without_prompt=True))
        self.assertEqual(cap.messages, [])

    async def test_nonstream_real_sdk_preserves_tool_loop_and_final(self):
        cap = self.capability(stream=False)

        async def model(messages, info):
            self.assertTrue(run_is_live("run"))
            if not parts(messages, ToolReturnPart):
                return ModelResponse(parts=[TextPart("working"), ToolCallPart("read", {}, "call")])
            return ModelResponse(parts=[TextPart("final")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[cap])

        @agent.tool_plain
        def read() -> str:
            self.assertEqual(len(parts(cap.messages, ToolCallPart)), 1)
            return "value"

        result = await agent.run("question", deps=self.deps, run_id="run")
        canonical = cap.canonical_messages(result.new_messages())
        self.assertEqual([p.content for p in parts(canonical, TextPart)], ["working", "final"])
        self.assertEqual(len(parts(canonical, ToolReturnPart)), 1)
        self.assertEqual(result.output, "final")
        self.assertFalse(run_is_live("run"))

    async def test_nonstream_second_model_failure_keeps_completed_tool_result(self):
        cap = self.capability(stream=False)

        async def model(messages, info):
            if parts(messages, ToolReturnPart):
                raise RuntimeError("provider failed")
            return ModelResponse(parts=[TextPart("working"), ToolCallPart("read", {}, "call")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[cap])

        @agent.tool_plain
        def read() -> str:
            return "value"

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            await agent.run("question", deps=self.deps, run_id="run")
        self.assertEqual(parts(cap.messages, ToolReturnPart)[0].content, "value")

    async def test_nonstream_parallel_return_is_durable_before_sibling_cancel_or_failure(self):
        for ending in ("cancel", "failure"):
            with self.subTest(ending=ending):
                self.store = RecordingStore()
                cap = self.capability(stream=False)
                entered = asyncio.Event()
                release = asyncio.Event()
                exited = asyncio.Event()

                async def model(messages, info):
                    return ModelResponse(parts=[ToolCallPart("fast", {}, "fast"),
                                                ToolCallPart("slow", {}, "slow")])

                agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[cap])

                @agent.tool_plain
                async def fast() -> str:
                    return "preserved fast result"

                @agent.tool_plain
                async def slow() -> str:
                    entered.set()
                    try:
                        await release.wait()
                        raise RuntimeError("slow tool failed")
                    finally:
                        exited.set()

                task = asyncio.create_task(agent.run("question", deps=self.deps, run_id="run"))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    for _ in range(100):
                        if parts(cap.messages, ToolReturnPart):
                            break
                        await asyncio.sleep(0.005)
                    self.assertFalse(task.done())
                    self.assertEqual([p.content for p in parts(cap.messages, ToolReturnPart)],
                                     ["preserved fast result"])
                    if ending == "cancel":
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        release.set()
                        with self.assertRaisesRegex(RuntimeError, "slow tool failed"):
                            await task
                    self.assertTrue(exited.is_set())
                    self.assertEqual([p.content for p in parts(cap.messages, ToolReturnPart)],
                                     ["preserved fast result"])
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_nonstream_progress_only_sees_actual_governed_large_tool_result(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-progress-spill-",
                                         dir=Path(__file__).resolve().parents[2]) as folder:
            files = LocalFileStore(folder)
            await files.initialize()
            limits, artifacts = build_tool_output_limits(
                request_headers={"user": "user"}, context_window_tokens=10_000,
                max_output_tokens=2_000, estimator=TokenEstimator.conservative(), file_store=files,
            )
            artifacts.bind(self.deps, [])
            cap = self.capability(stream=False)
            original = "文" * 5001

            async def model(messages, info):
                if not parts(messages, ToolReturnPart):
                    return ModelResponse(parts=[ToolCallPart("read", {}, "read")])
                return ModelResponse(parts=[TextPart("done")])

            # The real runtime capability is appended after configured policies.
            # Public ordering must move progress outside limits at Run assembly.
            agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[limits])

            @agent.tool_plain
            def read() -> str:
                return original

            result = await agent.run("question", deps=self.deps, run_id="run", capabilities=[cap])
            observed = [part for write in self.store.writes for part in parts(write["messages"], ToolReturnPart)]
            self.assertTrue(observed)
            self.assertTrue(all(part.metadata.get("overflow_handle") for part in observed))
            self.assertTrue(all(original not in str(part.content) for part in observed))
            handle = observed[0].metadata["overflow_handle"]
            self.assertIn(original, (await artifacts.read(handle)).decode())
            canonical = cap.canonical_messages(result.new_messages())
            self.assertEqual(len(parts(canonical, ToolReturnPart)), 1)
            self.assertEqual(parts(canonical, ToolReturnPart)[0].metadata["overflow_handle"], handle)

    async def test_request_replaced_by_before_node_hook_is_not_duplicated(self):
        cap = self.capability(stream=False)

        class ReplaceRequest(AbstractCapability):
            async def before_node_run(self, ctx, *, node):
                if isinstance(node, ModelRequestNode):
                    return replace(node, request=replace(node.request, parts=[UserPromptPart("replacement")]))
                return node

        async def model(messages, info):
            self.assertEqual(parts(messages, UserPromptPart)[-1].content, "replacement")
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[ReplaceRequest()])
        result = await agent.run("original", deps=self.deps, run_id="run", capabilities=[cap])
        canonical = cap.canonical_messages(result.new_messages())
        self.assertEqual([p.content for p in parts(canonical, UserPromptPart)], ["replacement"])

    async def test_database_failure_prevents_real_tool_side_effect(self):
        for reject in (False, True):
            with self.subTest(reject=reject):
                self.store = RecordingStore()
                cap = self.capability(stream=False)
                executions = []

                async def model(messages, info):
                    if reject:
                        self.store.reject = True
                    else:
                        self.store.fail_when = lambda items: bool(parts(items, ToolCallPart))
                    return ModelResponse(parts=[ToolCallPart("write", {}, "call")])

                agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[cap])

                @agent.tool_plain
                def write() -> str:
                    executions.append("executed")
                    return "done"

                with self.assertRaises(RunProgressPersistenceError):
                    await agent.run("question", deps=self.deps, run_id="run")
                self.assertEqual(executions, [])

    async def test_real_ptc_nested_call_uses_durable_parent_without_fake_model_event(self):
        cap = self.capability()
        executions = []

        async def read() -> str:
            # No nested call can get here until the outer run_code is durable.
            self.assertEqual([p.tool_name for p in parts(cap.messages, ToolCallPart)], ["run_code"])
            self.assertTrue(any(parts(write["messages"], ToolCallPart) for write in self.store.writes))
            executions.append("read")
            return "nested result"

        async def model(messages, info):
            if not parts(messages, ToolReturnPart):
                yield {0: DeltaToolCall(name="run_code", json_args='{"code":"await demo_read()"}',
                                       tool_call_id="outer")}
            else:
                yield "final"

        agent = Agent(
            FunctionModel(stream_function=model), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset,
                      FilePayloadResolvingToolset(FunctionToolset(tools=[read]).prefixed("demo"))],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True), cap],
        )
        result = await asyncio.wait_for(agent.run("question", deps=self.deps, run_id="run"), 3)
        self.assertEqual(executions, ["read"])
        canonical = cap.canonical_messages(result.new_messages())
        self.assertEqual([p.tool_name for p in parts(canonical, ToolCallPart)], ["run_code"])
        returned = parts(canonical, ToolReturnPart)
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0].metadata["nested_calls"][0]["status"], "completed")
        self.assertIsNone(cap._executing_parent.get())

    async def test_unknown_top_level_call_cannot_bypass_durable_guard(self):
        cap = self.capability()
        with self.assertRaisesRegex(RunProgressPersistenceError, "no official"):
            await cap.before_tool_execute(self.ctx, call=ToolCallPart("read", {}, "guessed__1"),
                                          tool_def=None, args={})

    async def test_nested_call_does_not_continue_after_timer_persistence_failure(self):
        cap = self.capability()
        response = ModelResponse(parts=[ToolCallPart("run_code", {}, "outer")])
        self.ctx.messages.append(response)
        await cap.before_tool_execute(self.ctx, call=response.parts[0], tool_def=None, args={})

        async def nested(args):
            cap.persistence_error = RunProgressPersistenceError("database failed")
            await cap.before_tool_execute(self.ctx, call=ToolCallPart("read", {}, "outer__1"),
                                          tool_def=None, args={})
            self.fail("Nested side effect must remain blocked")

        with self.assertRaisesRegex(RunProgressPersistenceError, "database failed"):
            await cap.wrap_tool_execute(self.ctx, call=response.parts[0], tool_def=None, args={}, handler=nested)
        self.assertIsNone(cap._executing_parent.get())

    async def test_provider_silence_flushes_last_delta_and_renews_lease(self):
        cap = self.capability()
        cap.snapshot_interval = 0.05
        cap.heartbeat_interval = 0.08
        paused = asyncio.Event()
        release = asyncio.Event()

        async def model(messages, info):
            yield "first"
            yield " second"
            paused.set()
            await release.wait()

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps, capabilities=[cap])
        task = asyncio.create_task(agent.run("question", deps=self.deps, run_id="run"))
        try:
            await asyncio.wait_for(paused.wait(), 2)
            await asyncio.sleep(0.22)
            self.assertFalse(task.done())
            self.assertEqual(parts(cap.messages, TextPart)[-1].content, "first second")
            self.assertGreater(self.store.touches, 0)
            self.assertLess(len(self.store.writes), 10)
            release.set()
            self.assertEqual((await asyncio.wait_for(task, 2)).output, "first second")
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_timer_database_failure_interrupts_silent_sdk_without_cancel_misreport(self):
        cap = self.capability()
        cap.snapshot_interval = 0.05
        self.store.fail_when = lambda items: any(p.content == "first second" for p in parts(items, TextPart))

        async def model(messages, info):
            yield "first"
            yield " second"
            await asyncio.Event().wait()

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps, capabilities=[cap])
        with self.assertRaises(RunProgressPersistenceError):
            await asyncio.wait_for(agent.run("question", deps=self.deps, run_id="run"), 2)

    async def test_external_cancellation_does_not_become_persistence_failure(self):
        cap = self.capability()
        started = asyncio.Event()

        async def model(messages, info):
            yield "first"
            started.set()
            await asyncio.Event().wait()

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps, capabilities=[cap])
        task = asyncio.create_task(agent.run("question", deps=self.deps, run_id="run"))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(cap.persistence_error)
        self.assertFalse(run_is_live("run"))

    async def test_expired_same_owner_requires_no_live_run_and_keeps_commit_grace(self):
        now = datetime.now(timezone.utc)
        fresh = owned_progress_payload({})
        expired = {**fresh, "lease_expires_at": (now - timedelta(seconds=1)).isoformat()}
        self.assertFalse(progress_is_abandoned(fresh, now, run_id="run"))
        with live_run("run"):
            with live_run("run"):
                self.assertFalse(progress_is_abandoned(expired, now, run_id="run"))
            self.assertTrue(run_is_live("run"))
            self.assertFalse(progress_is_abandoned(expired, now, run_id="run"))
        self.assertFalse(run_is_live("run"))
        self.assertTrue(progress_is_abandoned(expired, now, run_id="run"))

    async def test_lost_lease_stops_silent_provider_with_explicit_failure(self):
        cap = self.capability()
        cap.snapshot_interval = cap.heartbeat_interval = 0.04
        self.store.lease_valid = False

        async def model(messages, info):
            yield "first"
            await asyncio.Event().wait()

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps, capabilities=[cap])
        with self.assertRaisesRegex(RunProgressPersistenceError, "lease was lost"):
            await asyncio.wait_for(agent.run("question", deps=self.deps, run_id="run"), 2)
        self.assertEqual(self.store.touches, 1)

    async def test_actual_sdk_compaction_preserves_prior_loop_canonical_events(self):
        cap = self.capability(stream=False, history_length=20)

        class CompactSecondRequest(AbstractCapability):
            async def before_model_request(self, ctx, request_context):
                if parts(request_context.messages, ToolReturnPart):
                    return replace(request_context, messages=[
                        ModelRequest(parts=[UserPromptPart("Summary of previous conversation")]),
                        request_context.messages[-1],
                    ])
                return request_context

        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[TextPart("early work"), ToolCallPart("read", {}, "call")])
            self.assertEqual(parts(messages, TextPart), [])
            return ModelResponse(parts=[TextPart("final")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[cap, CompactSecondRequest()])

        @agent.tool_plain
        def read() -> str:
            return "value"

        result = await agent.run("question", deps=self.deps, run_id="run")
        self.assertNotIn("early work", [p.content for p in parts(result.new_messages(), TextPart)])
        canonical = cap.canonical_messages(result.new_messages())
        self.assertEqual([p.content for p in parts(canonical, TextPart)], ["early work", "final"])
        self.assertEqual(len(parts(canonical, ToolCallPart)), 1)
        self.assertEqual(len(parts(canonical, ToolReturnPart)), 1)

    async def test_invalid_ui_call_is_not_actionable_and_resume_has_no_old_response(self):
        cap = self.capability()
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            args = "{}" if calls == 1 else '{"message":"继续？"}'
            yield {0: DeltaToolCall(name="builtin__confirm", json_args=args,
                                   tool_call_id=f"call-{calls}")}

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps,
                      toolsets=[build_external_toolset(builtin_ui_records())],
                      output_type=[str, DeferredToolRequests], capabilities=[cap])
        result = await asyncio.wait_for(agent.run("question", deps=self.deps, run_id="run"), 2)
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual(calls, 2)
        seen = {part.tool_call_id for write in self.store.writes
                for part in parts(write["messages"], ToolCallPart)}
        self.assertEqual(seen, {"call-2"})

        resumed = self.capability(stream=False, history_length=len(result.all_messages()))
        resumed_deps = replace(self.deps, run_id="resume", resume_from_run_id="run")

        async def resume_model(messages, info):
            return ModelResponse(parts=[TextPart("resumed answer")])

        resume_agent = Agent(FunctionModel(resume_model), deps_type=AgentDeps,
                             toolsets=[build_external_toolset(builtin_ui_records())],
                             output_type=[str, DeferredToolRequests], capabilities=[resumed])
        final = await resume_agent.run(
            deps=resumed_deps, run_id="resume", message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(calls={"call-2": "confirmed"}),
        )
        canonical = resumed.canonical_messages(final.new_messages())
        self.assertEqual(parts(canonical, ToolCallPart), [])
        self.assertEqual(len(parts(canonical, ToolReturnPart)), 1)
        self.assertEqual([p.content for p in parts(canonical, TextPart)], ["resumed answer"])

    async def test_parallel_fast_return_is_durable_before_slow_tool_finishes(self):
        cap = self.capability()
        slow_started = asyncio.Event()
        release = asyncio.Event()

        async def model(messages, info):
            if not parts(messages, ToolReturnPart):
                yield {0: DeltaToolCall(name="fast", json_args="{}", tool_call_id="fast"),
                       1: DeltaToolCall(name="slow", json_args="{}", tool_call_id="slow")}
            else:
                yield "final"

        agent = Agent(FunctionModel(stream_function=model), deps_type=AgentDeps, capabilities=[cap])

        @agent.tool_plain
        async def fast() -> str:
            return "fast result"

        @agent.tool_plain
        async def slow() -> str:
            slow_started.set()
            await release.wait()
            return "slow result"

        task = asyncio.create_task(agent.run("question", deps=self.deps, run_id="run"))
        try:
            await asyncio.wait_for(slow_started.wait(), 2)
            for _ in range(100):
                if parts(cap.messages, ToolReturnPart):
                    break
                await asyncio.sleep(0.005)
            self.assertEqual([p.content for p in parts(cap.messages, ToolReturnPart)], ["fast result"])
            self.assertFalse(task.done())
            release.set()
            result = await asyncio.wait_for(task, 2)
            canonical = cap.canonical_messages(result.new_messages())
            self.assertEqual(len(parts(canonical, ToolReturnPart)), 2)
            self.assertEqual(len(parts(cap.messages, ToolReturnPart)), 2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
