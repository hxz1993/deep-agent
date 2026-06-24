from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
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

    async def asyncTearDown(self) -> None:
        await self._engine.dispose()

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
            async for ev in runner.run_stream("sess-err", "hello", deps):
                events_out.append(ev)

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

        async for _ in runner.run_stream("sess-ok", "hello", deps):
            pass

        history = await self.store.get_history("sess-ok")
        user_messages = [m for m in history if is_user_only_request(m)]
        self.assertEqual(len(user_messages), 1)

    async def test_branch_from_truncates_and_restarts(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-branch")

        async for _ in runner.run_stream("sess-branch", "first", deps):
            pass
        async for _ in runner.run_stream("sess-branch", "second", deps):
            pass
        self.assertEqual(len(await self.store.get_history("sess-branch")), 4)

        async for _ in runner.run_stream("sess-branch", "branched", deps, branch_from=0):
            pass
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

        async for _ in runner.run_stream("sess-mid", "first", deps):
            pass
        async for _ in runner.run_stream("sess-mid", "second", deps):
            pass
        history_before = await self.store.get_history("sess-mid")
        self.assertEqual(len(history_before), 4)

        async for _ in runner.run_stream("sess-mid", "edited-second", deps, branch_from=2):
            pass

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

        async for _ in runner.run_stream("sess-repeat", "hello", deps):
            pass

        for edited in ("hello v2", "hello v3"):
            async for _ in runner.run_stream("sess-repeat", edited, deps, branch_from=0):
                pass
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
        async for ev in runner.run_stream("sess-meta", "hello", deps):
            events_out.append(ev)

        final = events_out[-1]
        self.assertEqual(final["type"], "final")
        self.assertEqual(final["model_message_count"], 2)
        self.assertEqual(final["user_branch_point"], 0)


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


if __name__ == "__main__":
    unittest.main()
