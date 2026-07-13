from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.history import is_user_only_request, model_messages_to_history
from deep_agent.agent.runner import AgentRunner
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
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self._engine.dispose()

    async def _flush(self, session_id: str, *, agent_id: str | None = None) -> None:
        await self.persister.flush(session_id, agent_id=agent_id)

    async def test_run_stream_persists_user_on_error(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-err")

        @asynccontextmanager
        async def failing_stream(*_args, **_kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        with patch.object(agent, "run_stream_events", side_effect=failing_stream):
            events_out: list[dict] = []
            async for ev in runner.run_stream("sess-err", "hello", deps, persister=self.persister):
                events_out.append(ev)

        await self._flush("sess-err")

        self.assertEqual(events_out[-1]["type"], "error")
        self.assertTrue(events_out[-1].get("history_persisted"))
        self.assertEqual(events_out[-1].get("model_message_count"), 1)
        self.assertEqual(events_out[-1].get("user_branch_point"), 0)
        history = await self.store.get_history("sess-err")
        self.assertEqual(len(history), 1)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertTrue(is_user_only_request(history[0]))

    async def test_run_stream_no_duplicate_user_on_success(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-ok")

        async for _ in runner.run_stream("sess-ok", "hello", deps, persister=self.persister):
            pass

        await self._flush("sess-ok")

        history = await self.store.get_history("sess-ok")
        user_messages = [m for m in history if is_user_only_request(m)]
        self.assertEqual(len(user_messages), 1)

    async def test_branch_from_truncates_and_restarts(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-branch")

        async for _ in runner.run_stream("sess-branch", "first", deps, persister=self.persister):
            pass
        await self._flush("sess-branch")
        async for _ in runner.run_stream("sess-branch", "second", deps, persister=self.persister):
            pass
        await self._flush("sess-branch")
        self.assertEqual(len(await self.store.get_history("sess-branch")), 4)

        async for _ in runner.run_stream(
            "sess-branch", "branched", deps, branch_from=0, persister=self.persister
        ):
            pass
        await self._flush("sess-branch")
        history = await self.store.get_history("sess-branch")
        user_texts = [
            p.content
            for m in history
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(user_texts, ["branched"])

    async def test_branch_from_middle_turn_keeps_prior_assistant(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-mid")

        async for _ in runner.run_stream("sess-mid", "first", deps, persister=self.persister):
            pass
        await self._flush("sess-mid")
        async for _ in runner.run_stream("sess-mid", "second", deps, persister=self.persister):
            pass
        await self._flush("sess-mid")
        history_before = await self.store.get_history("sess-mid")
        self.assertEqual(len(history_before), 4)

        async for _ in runner.run_stream(
            "sess-mid", "edited-second", deps, branch_from=2, persister=self.persister
        ):
            pass
        await self._flush("sess-mid")

        history = await self.store.get_history("sess-mid")
        self.assertEqual(len(history), 4)
        user_texts = [
            p.content
            for m in history
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(user_texts, ["first", "edited-second"])
        self.assertIsInstance(history[1], ModelResponse)
        self.assertIsInstance(history[3], ModelResponse)

    async def test_branch_from_repeated_edit_same_message(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-repeat")

        async for _ in runner.run_stream("sess-repeat", "hello", deps, persister=self.persister):
            pass
        await self._flush("sess-repeat")

        for edited in ("hello v2", "hello v3"):
            async for _ in runner.run_stream(
                "sess-repeat", edited, deps, branch_from=0, persister=self.persister
            ):
                pass
            await self._flush("sess-repeat")
            history = await self.store.get_history("sess-repeat")
            user_texts = [
                p.content
                for m in history
                if isinstance(m, ModelRequest)
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            self.assertEqual(user_texts, [edited])
            self.assertEqual(len(history), 2)

    async def test_final_includes_history_meta(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-meta")

        events_out: list[dict] = []
        async for ev in runner.run_stream("sess-meta", "hello", deps, persister=self.persister):
            events_out.append(ev)

        await self._flush("sess-meta")

        final = events_out[-1]
        self.assertEqual(final["type"], "final")
        self.assertEqual(final["model_message_count"], 2)
        self.assertEqual(final["user_branch_point"], 0)

    async def test_run_stream_cancel_persists_partial_text(self) -> None:
        from tests.integration.helpers.models import build_slow_stream_model

        chunks = ["alpha-", "beta-", "gamma-", "delta-", "omega"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.03)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-cancel")

        events_out: list[dict] = []
        agen = runner.run_stream("sess-cancel", "hello", deps, persister=self.persister)
        async for ev in agen:
            events_out.append(ev)
            if ev.get("type") == "text" and ev.get("delta"):
                await agen.aclose()
                break

        await self._flush("sess-cancel")

        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
        history = await self.store.get_history("sess-cancel")
        self.assertGreaterEqual(len(history), 2)
        last = history[-1]
        self.assertIsInstance(last, ModelResponse)
        text = last.parts[0].content if last.parts else ""
        self.assertTrue(str(text).startswith("alpha"))

    async def test_run_stream_persists_partial_when_consumer_closes_mid_yield(self) -> None:
        from tests.integration.helpers.models import build_slow_stream_model

        chunks = ["part-a-", "part-b-", "part-c-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.05)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-close")

        agen = runner.run_stream("sess-close", "hello", deps, persister=self.persister)
        async for ev in agen:
            if ev.get("type") == "text" and ev.get("delta"):
                await agen.aclose()
                break

        await self._flush("sess-close")

        self.assertGreater(tracker.yielded_chunks, 0)
        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
        history = await self.store.get_history("sess-close")
        self.assertGreaterEqual(len(history), 2)
        last = history[-1]
        self.assertIsInstance(last, ModelResponse)
        text = last.parts[0].content if last.parts else ""
        self.assertIn("part-a", str(text))

    async def test_run_stream_normal_complete_does_not_snapshot_after_full_persist(self) -> None:
        from tests.integration.helpers.models import build_slow_stream_model

        chunks = ["only-reply"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.01)
        agent = Agent(model, deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-no-dup")

        async for _ in runner.run_stream("sess-no-dup", "hello", deps, persister=self.persister):
            pass

        await self._flush("sess-no-dup")
        history = await self.store.get_history("sess-no-dup")
        self.assertEqual(len(history), 2)
        assistants = [m for m in history if isinstance(m, ModelResponse)]
        self.assertEqual(len(assistants), 1)

    async def test_run_stream_new_message_after_pending_hitl(self) -> None:
        from pydantic_ai import DeferredToolRequests

        from deep_agent.capabilities.ui.builtin import build_ui_toolset

        test_model = TestModel(call_tools=["confirm_action"], custom_output_text="continued")
        agent = Agent(
            test_model,
            deps_type=AgentDeps,
            toolsets=[build_ui_toolset()],
            output_type=[str, DeferredToolRequests],
        )
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-skip-hitl")

        async for _ in runner.run_stream(
            "sess-skip-hitl", "need confirm", deps, persister=self.persister
        ):
            pass
        await self._flush("sess-skip-hitl")

        second: list[dict] = []
        async for ev in runner.run_stream(
            "sess-skip-hitl", "skip confirm", deps, persister=self.persister
        ):
            second.append(ev)
        await self._flush("sess-skip-hitl")

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


class TruncateHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator

    async def asyncTearDown(self) -> None:
        await self._engine.dispose()

    async def test_truncate(self) -> None:
        messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="a")]),
            ModelResponse(parts=[TextPart(content="b")]),
            ModelRequest(parts=[UserPromptPart(content="c")]),
        ]
        await self.store.append("s1", messages)
        await self.store.truncate_history("s1", keep_count=2)
        history = await self.store.get_history("s1")
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[-1], ModelResponse)

    async def test_truncate_empty(self) -> None:
        await self.store.append("s2", [ModelRequest(parts=[UserPromptPart(content="x")])])
        await self.store.truncate_history("s2", keep_count=0)
        self.assertEqual(await self.store.get_history("s2"), [])

    async def test_truncate_does_not_remove_prior_assistant(self) -> None:
        messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="u0")]),
            ModelResponse(parts=[TextPart(content="a0")]),
            ModelRequest(parts=[UserPromptPart(content="u1")]),
            ModelResponse(parts=[TextPart(content="a1")]),
        ]
        await self.store.append("s3", messages)
        await self.store.truncate_history("s3", keep_count=2)
        history = await self.store.get_history("s3")
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertIsInstance(history[1], ModelResponse)
        self.assertEqual(history[1].parts[0].content, "a0")


class AgentRunnerTitleGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
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
        )
        await self.store.update_session_title("title-session", None, "已有标题")

        with patch("deep_agent.agent.title_generator.generate_title", autospec=True) as generate_title:
            await self.runner._maybe_generate_title(
                "title-session",
                AgentDeps(session_id="title-session"),
                [ModelResponse(parts=[TextPart(content="第二句回复")])],
            )

        generate_title.assert_not_called()


if __name__ == "__main__":
    unittest.main()
