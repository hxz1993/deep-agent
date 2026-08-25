from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

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
from deep_agent.agent.history import is_user_only_request, model_messages_to_history
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
        )

        async def summarize(_messages, _info):
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

        agent = Agent(TestModel(custom_output_text="recovered"), deps_type=AgentDeps)
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
            summary_model=FunctionModel(summarize),
            summary_context_window_tokens=16_000,
            summary_max_output_tokens=256,
        )
        runner = AgentRunner(
            agent,
            self.store,
            UsageLimits(request_limit=10),
            context_window_tokens=10_000,
            max_output_tokens=128,
            context_compaction=compaction,
            summary_model_id="summary-model",
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
        projected = model_messages_to_history(history)
        self.assertEqual(projected[-1].run_id, final["run_id"])

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

        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])

    async def test_transport_close_keeps_user_without_synthetic_partial_response(self) -> None:
        from tests.integration.helpers.models import build_slow_stream_model

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
        self.assertEqual(len(history), 1)
        self.assertIsInstance(history[0], ModelRequest)

    async def test_consumer_close_does_not_fabricate_cancelled_assistant(self) -> None:
        from tests.integration.helpers.models import build_slow_stream_model

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
        self.assertEqual(len(history), 1)
        self.assertIsInstance(history[0], ModelRequest)

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
        from tests.integration.helpers.models import build_slow_stream_model

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


class BranchPointAlignmentTests(unittest.TestCase):
    def test_branch_point_matches_model_index(self) -> None:
        messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="a")]),
            ModelResponse(parts=[TextPart(content="b")]),
            ModelRequest(parts=[UserPromptPart(content="c")]),
            ModelResponse(parts=[TextPart(content="d")]),
        ]
        history = model_messages_to_history(messages)
        self.assertEqual([m.branch_point for m in history], [0, 1, 2, 3])


class AgentRunnerTitleGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        model = (await stores.llm_model.list_all())[0]
        self.db_agent = await stores.agent.create_unique(
            AgentCreate(name="runner-title-test", model_id=model.id, prompt="test")
        )
        self.runner = AgentRunner(
            Agent(TestModel(), output_type=str),
            self.store,
            UsageLimits(request_limit=5),
        )

    async def asyncTearDown(self) -> None:
        await self._engine.dispose()

    async def test_second_exchange_skips_title_generation_when_title_exists(self) -> None:
        await self.store.append(
            "title-session",
            [
                ModelRequest(parts=[UserPromptPart(content="第一句")]),
                ModelResponse(parts=[TextPart(content="第一句回复")]),
                ModelRequest(parts=[UserPromptPart(content="第二句")]),
                ModelResponse(parts=[TextPart(content="第二句回复")]),
            ],
            agent_id=self.db_agent.id,
            user_id="runner-title-user",
        )
        await self.store.update_session_title(
            "title-session", self.db_agent.id, "已有标题"
        )

        with patch("deep_agent.agent.title_generator.generate_title", autospec=True) as generate_title:
            await self.runner._maybe_generate_title(
                "title-session",
                AgentDeps(
                    session_id="title-session",
                    agent_id=self.db_agent.id,
                    user="runner-title-user",
                ),
                [ModelResponse(parts=[TextPart(content="第二句回复")])],
            )

        generate_title.assert_not_called()


if __name__ == "__main__":
    unittest.main()
