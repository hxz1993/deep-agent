from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import anyio

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.history import is_user_only_request
from deep_agent.agent.runner import AgentRunner
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class IsUserOnlyRequestTests(unittest.TestCase):
    def test_user_only_request(self) -> None:
        request = ModelRequest(parts=[UserPromptPart(content="hi")])
        self.assertTrue(is_user_only_request(request))

    def test_response_is_not_user_only(self) -> None:
        response = ModelResponse(parts=[TextPart(content="hello")])
        self.assertFalse(is_user_only_request(response))


class AgentRunnerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        model = (await stores.llm_model.list_all())[0]
        self.db_agent = await stores.agent.create_unique(
            AgentCreate(name="runner-history-test", model_id=model.id, prompt="test")
        )
        self.persister = EventPersister(self.store)
        await self.persister.start()

    def _deps(self, session_id: str) -> AgentDeps:
        return AgentDeps(
            session_id=session_id,
            user="runner-history-user",
            agent_id=self.db_agent.id,
        )

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self._engine.dispose()

    async def _flush(self, session_id: str, *, agent_id: str | None = None) -> None:
        await self.persister.flush(session_id, agent_id=agent_id)

    async def test_run_stream_persists_user_on_error(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-err")

        @asynccontextmanager
        async def failing_stream(*_args, **_kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        with patch.object(agent, "run_stream_events", side_effect=failing_stream):
            events_out: list[dict] = []
            async for ev in runner.run_stream("sess-err", "hello", deps, persister=self.persister):
                events_out.append(ev)

        await self._flush("sess-err", agent_id=self.db_agent.id)

        self.assertEqual(events_out[-1]["type"], "error")
        self.assertTrue(events_out[-1].get("history_persisted"))
        self.assertNotIn("model_message_count", events_out[-1])
        self.assertNotIn("user_branch_point", events_out[-1])
        history = await self.store.get_history("sess-err", agent_id=self.db_agent.id)
        self.assertEqual(len(history), 1)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertTrue(is_user_only_request(history[0]))

    async def test_run_stream_persists_partial_assistant_on_late_error(self) -> None:
        async def fail_after_partial(_messages, _info):
            yield "partial answer"
            raise RuntimeError("boom after partial")

        agent = Agent(
            FunctionModel(stream_function=fail_after_partial),
            deps_type=AgentDeps,
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-partial-error")

        events_out = [
            event
            async for event in runner.run_stream(
                "sess-partial-error",
                "hello",
                deps,
                persister=self.persister,
            )
        ]

        self.assertEqual(events_out[-1]["type"], "error")
        self.assertTrue(events_out[-1]["history_persisted"])
        history = await self.store.get_raw_history(
            "sess-partial-error",
            agent_id=self.db_agent.id,
        )
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertIsInstance(history[1], ModelResponse)
        self.assertEqual(history[1].parts[0].content, "partial answer")
        self.assertEqual(history[1].state, "interrupted")

        persisted = await self.store.list_events("sess-partial-error", limit=100)
        assistant = next(
            item for item in persisted if item.event_type == "assistant/message"
        )
        self.assertEqual(assistant.status, "failed")
        self.assertFalse(any(item.event_type == "run/terminal" for item in persisted))

    async def test_run_stream_no_duplicate_user_on_success(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-ok")

        async for _ in runner.run_stream("sess-ok", "hello", deps, persister=self.persister):
            pass

        await self._flush("sess-ok", agent_id=self.db_agent.id)

        history = await self.store.get_history("sess-ok", agent_id=self.db_agent.id)
        user_messages = [m for m in history if is_user_only_request(m)]
        self.assertEqual(len(user_messages), 1)

    async def test_stream_provider_overflow_compacts_once_then_retries(self) -> None:
        await self.store.append(
            "sess-provider-overflow",
            [
                ModelRequest(parts=[UserPromptPart(content="旧问题" * 200)]),
                ModelResponse(parts=[TextPart(content="旧回答" * 200)]),
            ],
            agent_id=self.db_agent.id,
            user_id="runner-history-user",
            turn_id="run-provider-overflow-seed",
            run_id="run-provider-overflow-seed",
        )

        async def respond(messages, _info):
            if "Primary Request and Intent" not in repr(messages):
                return ModelResponse(parts=[TextPart(content="recovered")])
            headings = (
                "Primary Request and Intent", "Key Technical Concepts", "Files and Code",
                "Errors and Fixes", "Pending Jobs", "Current Work", "Next Step",
                "Critical Context",
            )
            content = "\n\n".join(
                f"## {index}. {heading}\n{'保留旧任务' if index == 1 else '无'}"
                for index, heading in enumerate(headings, start=1)
            )
            return ModelResponse(parts=[TextPart(content=content)])

        async def stream_respond(_messages, _info):
            yield "recovered"

        agent = Agent(
            FunctionModel(respond, stream_function=stream_respond),
            deps_type=AgentDeps,
        )
        original_stream = agent.run_stream_events
        attempts = 0

        @asynccontextmanager
        async def overflow_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ModelHTTPError(
                    400,
                    "test-model",
                    {"error": {"code": "context_length_exceeded"}},
                )
            async with original_stream(*args, **kwargs) as stream:
                yield stream

        compaction = DeepAgentContextCompaction(
            context_window_tokens=10_000,
            max_output_tokens=128,
            summary_timeout=5,
        )
        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            context_window_tokens=10_000,
            max_output_tokens=128,
            context_compaction=compaction,
            model_id="main-model",
        )
        deps = self._deps("sess-provider-overflow")
        with patch.object(agent, "run_stream_events", side_effect=overflow_once):
            output = [
                item
                async for item in runner.run_stream(
                    "sess-provider-overflow",
                    "当前问题",
                    deps,
                    persister=self.persister,
                )
            ]

        await self._flush("sess-provider-overflow", agent_id=self.db_agent.id)
        self.assertEqual(attempts, 2)
        self.assertEqual(output[-1]["type"], "final")
        persisted = await self.store.list_events(
            "sess-provider-overflow",
            limit=100,
        )
        user = next(
            item
            for item in persisted
            if item.event_type == "user/message" and item.run_id == deps.run_id
        )
        compacted = next(
            item
            for item in persisted
            if item.event_type == "compaction" and item.status == "completed"
        )
        self.assertLess(user.sequence, compacted.sequence)
        self.assertEqual(compacted.payload["trigger"], "provider_overflow")
        self.assertTrue(any(item["type"] == "context_compaction_progress" for item in output))

    async def test_run_stream_passes_merged_model_settings(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        settings = ModelSettings(temperature=0.2, max_tokens=1024)
        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            model_settings=settings,
        )
        deps = self._deps("sess-model-settings")

        with patch.object(
            agent,
            "run_stream_events",
            wraps=agent.run_stream_events,
        ) as run_stream_events:
            async for _ in runner.run_stream(
                "sess-model-settings",
                "hello",
                deps,
                persister=self.persister,
            ):
                pass

        self.assertEqual(
            run_stream_events.call_args.kwargs["model_settings"],
            settings,
        )

    async def test_branch_from_is_rejected_without_mutating_message_events(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-branch")

        async for _ in runner.run_stream("sess-branch", "first", deps, persister=self.persister):
            pass
        await self._flush("sess-branch", agent_id=self.db_agent.id)
        before = await self.store.get_history("sess-branch", agent_id=self.db_agent.id)
        rejected = [
            event
            async for event in runner.run_stream(
                "sess-branch", "branched", deps, branch_from=0, persister=self.persister
            )
        ]
        self.assertEqual(rejected[-1]["type"], "error")
        self.assertIn("Branch From", rejected[-1]["message"])
        after = await self.store.get_history("sess-branch", agent_id=self.db_agent.id)
        self.assertEqual(after, before)

    async def test_final_uses_run_identity_not_legacy_history_offsets(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-meta")

        events_out: list[dict] = []
        async for ev in runner.run_stream("sess-meta", "hello", deps, persister=self.persister):
            events_out.append(ev)

        await self._flush("sess-meta", agent_id=self.db_agent.id)

        final = events_out[-1]
        self.assertEqual(final["type"], "final")
        self.assertNotIn("model_message_count", final)
        self.assertNotIn("user_branch_point", final)
        self.assertEqual(final["conversation_id"], f"deepagent:{self.db_agent.id}:sess-meta")
        self.assertEqual(final["run_id"], deps.run_id)
        self.assertEqual(final["run_metrics"]["status"], "completed")
        self.assertIn("cache_read_tokens", final["usage"])

        history = await self.store.get_history("sess-meta", agent_id=self.db_agent.id)
        assistant = next(message for message in history if isinstance(message, ModelResponse))
        self.assertEqual(assistant.run_id, final["run_id"])

    async def test_each_send_gets_a_new_run_id_in_same_conversation(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-run-ids")

        first = [
            event
            async for event in runner.run_stream(
                "sess-run-ids", "first", deps, persister=self.persister
            )
        ][-1]
        await self._flush("sess-run-ids", agent_id=self.db_agent.id)
        second = [
            event
            async for event in runner.run_stream(
                "sess-run-ids", "second", deps, persister=self.persister
            )
        ][-1]
        await self._flush("sess-run-ids", agent_id=self.db_agent.id)

        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        persisted = await self.store.list_events("sess-run-ids", limit=100)
        self.assertNotIn("run", {event.event_type for event in persisted})
        for run_id in (first["run_id"], second["run_id"]):
            run_events = [event for event in persisted if event.run_id == run_id]
            self.assertTrue(run_events)
            self.assertEqual({event.turn_id for event in run_events}, {run_id})

    async def test_transport_close_preserves_official_partial_response(self) -> None:
        from tests.helpers.models import build_slow_stream_model

        chunks = ["alpha-", "beta-", "gamma-", "delta-", "omega"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.03)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-cancel")

        events_out: list[dict] = []
        agen = runner.run_stream("sess-cancel", "hello", deps, persister=self.persister)
        async for ev in agen:
            events_out.append(ev)
            if ev.get("type") == "text" and ev.get("delta"):
                await agen.aclose()
                break

        await self._flush("sess-cancel", agent_id=self.db_agent.id)

        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
        history = await self.store.get_history("sess-cancel", agent_id=self.db_agent.id)
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertIsInstance(history[1], ModelResponse)
        self.assertEqual(history[1].state, "interrupted")
        self.assertTrue(history[1].parts[0].content.startswith("alpha-"))

    async def _assert_disconnect_before_first_response(
        self, *, close_generator: bool, cancel_scope: bool = False,
    ) -> None:
        entered = asyncio.Event()
        exited = asyncio.Event()

        async def waiting_stream(messages, info):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield "never emitted"
            finally:
                exited.set()

        registry = ActiveRunRegistry()
        runner = AgentRunner(
            Agent(FunctionModel(stream_function=waiting_stream), deps_type=AgentDeps),
            self.store,
            UsageLimits(request_limit=10),
            active_runs=registry,
        )
        session_id = "disconnect-before-response"
        deps = self._deps(session_id)
        stream = runner.run_stream(session_id, "hello", deps, persister=self.persister)
        task = None
        try:
            if close_generator:
                # The persisted User Event can reach the consumer before the
                # Provider has produced any response. Close at that boundary.
                first = await asyncio.wait_for(anext(stream), 2)
                self.assertEqual(first["event"]["event_type"], "user/message")
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(stream.aclose(), 2)
            else:
                scopes = []

                async def consume():
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        async for _ in stream:
                            pass

                task = asyncio.create_task(consume())
                await asyncio.wait_for(entered.wait(), 2)
                if cancel_scope:
                    scopes[0].cancel()
                    await asyncio.wait_for(task, 2)
                else:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            await self._flush(session_id, agent_id=self.db_agent.id)
            rows = await self.store.list_events(session_id, limit=100)
            rows = [row for row in rows if row.event_type != "session/title"]
            self.assertEqual([row.event_type for row in rows], ["user/message"])
            self.assertEqual(rows[-1].run_id, deps.run_id)
            self.assertTrue(exited.is_set(), "Provider task survived the disconnected consumer")
            self.assertIsNone(registry.get(agent_id=self.db_agent.id, session_id=session_id))
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await stream.aclose()

    async def test_transport_close_before_first_response_persists_terminal(self) -> None:
        await self._assert_disconnect_before_first_response(close_generator=True)

    async def test_transport_cancel_before_first_response_persists_terminal(self) -> None:
        await self._assert_disconnect_before_first_response(close_generator=False)

    async def test_sse_cancel_scope_before_first_response_persists_terminal(self) -> None:
        await self._assert_disconnect_before_first_response(
            close_generator=False, cancel_scope=True,
        )

    async def test_consumer_close_saves_actual_sdk_content_not_synthetic_text(self) -> None:
        from tests.helpers.models import build_slow_stream_model

        chunks = ["part-a-", "part-b-", "part-c-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.05)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-close")

        agen = runner.run_stream("sess-close", "hello", deps, persister=self.persister)
        async for ev in agen:
            if ev.get("type") == "text" and ev.get("delta"):
                await agen.aclose()
                break

        await self._flush("sess-close", agent_id=self.db_agent.id)

        self.assertGreater(tracker.yielded_chunks, 0)
        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
        history = await self.store.get_history("sess-close", agent_id=self.db_agent.id)
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertEqual(history[1].state, "interrupted")
        self.assertIn(history[1].parts[0].content, ["part-a-", "part-a-part-b-"])

    async def test_terminal_store_failure_does_not_mask_stream_error_or_leak_run(self) -> None:
        registry = ActiveRunRegistry()

        async def failing_model(_messages, _info):
            raise RuntimeError("model failed")
            yield "unreachable"

        agent = Agent(FunctionModel(stream_function=failing_model), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10), active_runs=registry)
        deps = self._deps("terminal-store-failure")

        async def fail_terminal(*args, **kwargs):
            raise RuntimeError("database unavailable")

        with patch.object(self.store, "append_execution_error", side_effect=fail_terminal):
            result = [ev async for ev in runner.run_stream(
                deps.session_id, "hello", deps, persister=self.persister,
            )]
        self.assertEqual(result[-1]["type"], "error")
        self.assertIn("model failed", result[-1]["message"])
        self.assertEqual(registry.active_count(), 0)

    async def test_terminal_write_timeout_keeps_original_error_and_releases_run(self) -> None:
        registry = ActiveRunRegistry()
        exited = asyncio.Event()

        async def failing_model(_messages, _info):
            raise RuntimeError("model failed before terminal")
            yield "unreachable"

        async def stalled_terminal(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        runner = AgentRunner(
            Agent(FunctionModel(stream_function=failing_model), deps_type=AgentDeps),
            self.store, UsageLimits(request_limit=10), active_runs=registry,
        )
        deps = self._deps("terminal-write-timeout")
        with patch("deep_agent.agent.runner._TERMINAL_WRITE_TIMEOUT", 0.02), \
                patch.object(self.store, "append_execution_error", side_effect=stalled_terminal):
            async with asyncio.timeout(2):
                result = [ev async for ev in runner.run_stream(
                    deps.session_id, "hello", deps, persister=self.persister,
                )]
        self.assertTrue(exited.is_set())
        self.assertEqual(result[-1]["type"], "error")
        self.assertIn("model failed before terminal", result[-1]["message"])
        self.assertEqual(registry.active_count(), 0)

    async def test_official_interrupted_tool_call_is_repaired_by_pydantic_next_run(self) -> None:
        captured: list[ModelMessage] = []

        async def respond(messages, _info):
            captured.extend(messages)
            return ModelResponse(parts=[TextPart(content="continued")])

        await self.store.append(
            "sess-interrupted-next-run",
            [
                ModelRequest(parts=[UserPromptPart(content="start")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="slow_tool",
                            args={},
                            tool_call_id="interrupted-call",
                        )
                    ],
                    state="interrupted",
                ),
            ],
            agent_id=self.db_agent.id,
            user_id="runner-history-user",
            turn_id="run-interrupted-seed",
            run_id="run-interrupted-seed",
        )
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-interrupted-next-run")

        result = await runner.run(
            "sess-interrupted-next-run",
            "continue",
            deps,
        )
        self.assertEqual(result["status"], "completed")

        interrupted_results = [
            part
            for message in captured
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_call_id == "interrupted-call"
        ]
        self.assertEqual(len(interrupted_results), 1)
        self.assertEqual(interrupted_results[0].outcome, "interrupted")
        self.assertIn("interrupted", str(interrupted_results[0].content).lower())

    async def test_formal_cancel_stops_running_function_tool(self) -> None:
        tool_started = asyncio.Event()
        tool_cancelled = asyncio.Event()
        registry = ActiveRunRegistry()
        agent = Agent(
            TestModel(call_tools=["slow_tool"], custom_output_text="should not finish"),
            deps_type=AgentDeps,
        )

        @agent.tool_plain
        async def slow_tool() -> str:
            tool_started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise
            return "unexpected"

        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            active_runs=registry,
        )
        deps = self._deps("sess-cancel-tool")
        events_out: list[dict] = []

        async def consume() -> None:
            async for event in runner.run_stream(
                "sess-cancel-tool",
                "run tool",
                deps,
                persister=self.persister,
            ):
                events_out.append(event)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(tool_started.wait(), timeout=3)
        self.assertTrue(
            registry.request_cancel(
                agent_id=self.db_agent.id,
                session_id="sess-cancel-tool",
                user_id="runner-history-user",
            )
        )
        await asyncio.wait_for(task, timeout=3)

        self.assertTrue(tool_cancelled.is_set())
        self.assertIn("cancelled", {event.get("type") for event in events_out})
        self.assertNotIn("final", {event.get("type") for event in events_out})

    async def test_run_stream_normal_complete_does_not_snapshot_after_full_persist(self) -> None:
        from tests.helpers.models import build_slow_stream_model

        chunks = ["only-reply"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.01)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-no-dup")

        async for _ in runner.run_stream("sess-no-dup", "hello", deps, persister=self.persister):
            pass

        await self._flush("sess-no-dup", agent_id=self.db_agent.id)
        history = await self.store.get_history("sess-no-dup", agent_id=self.db_agent.id)
        self.assertEqual(len(history), 2)
        assistants = [m for m in history if isinstance(m, ModelResponse)]
        self.assertEqual(len(assistants), 1)

    async def test_run_stream_new_message_after_pending_hitl(self) -> None:
        from pydantic_ai import DeferredToolRequests

        from deep_agent.capabilities.ui.builtin import build_ui_toolset

        test_model = TestModel(call_tools=["builtin__confirm"], custom_output_text="continued")
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_ui_toolset()],
            output_type=[str, DeferredToolRequests],
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = self._deps("sess-skip-hitl")

        async for _ in runner.run_stream(
            "sess-skip-hitl", "need confirm", deps, persister=self.persister
        ):
            pass
        await self._flush("sess-skip-hitl", agent_id=self.db_agent.id)

        second: list[dict] = []
        async for ev in runner.run_stream(
            "sess-skip-hitl", "skip confirm", deps, persister=self.persister
        ):
            second.append(ev)
        await self._flush("sess-skip-hitl", agent_id=self.db_agent.id)

        self.assertEqual(second[-1]["type"], "final")
        self.assertEqual(second[-1]["status"], "completed")

if __name__ == "__main__":
    unittest.main()
