from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.history import is_user_only_request
from deep_agent.agent.runner import AgentRunner
from deep_agent.db.stores import InMemorySessionStore


class IsUserOnlyRequestTests(unittest.TestCase):
    def test_user_only_request(self) -> None:
        request = ModelRequest(parts=[UserPromptPart(content="hi")])
        self.assertTrue(is_user_only_request(request))

    def test_response_is_not_user_only(self) -> None:
        response = ModelResponse(parts=[TextPart(content="hello")])
        self.assertFalse(is_user_only_request(response))


class AgentRunnerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_stream_persists_user_on_error(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        store = InMemorySessionStore()
        runner = AgentRunner(agent, store, UsageLimits(request_limit=10))
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
        history = await store.get_history("sess-err")
        self.assertEqual(len(history), 1)
        self.assertIsInstance(history[0], ModelRequest)
        self.assertTrue(is_user_only_request(history[0]))

    async def test_run_stream_no_duplicate_user_on_success(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        store = InMemorySessionStore()
        runner = AgentRunner(agent, store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-ok")

        async for _ in runner.run_stream("sess-ok", "hello", deps):
            pass

        history = await store.get_history("sess-ok")
        user_messages = [m for m in history if is_user_only_request(m)]
        self.assertEqual(len(user_messages), 1)

    async def test_branch_from_truncates_and_restarts(self) -> None:
        agent = Agent(TestModel(), deps_type=AgentDeps)
        store = InMemorySessionStore()
        runner = AgentRunner(agent, store, UsageLimits(request_limit=10))
        deps = AgentDeps(session_id="sess-branch")

        async for _ in runner.run_stream("sess-branch", "first", deps):
            pass
        async for _ in runner.run_stream("sess-branch", "second", deps):
            pass
        self.assertEqual(len(await store.get_history("sess-branch")), 4)

        async for _ in runner.run_stream("sess-branch", "branched", deps, branch_from=0):
            pass
        history = await store.get_history("sess-branch")
        user_texts = [
            p.content
            for m in history
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        self.assertEqual(user_texts, ["branched"])


class TruncateHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_memory_truncate(self) -> None:
        store = InMemorySessionStore()
        messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="a")]),
            ModelResponse(parts=[TextPart(content="b")]),
            ModelRequest(parts=[UserPromptPart(content="c")]),
        ]
        await store.append("s1", messages)
        await store.truncate_history("s1", keep_count=2)
        history = await store.get_history("s1")
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[-1], ModelResponse)

    async def test_in_memory_truncate_empty(self) -> None:
        store = InMemorySessionStore()
        await store.append("s2", [ModelRequest(parts=[UserPromptPart(content="x")])])
        await store.truncate_history("s2", keep_count=0)
        self.assertEqual(await store.get_history("s2"), [])


if __name__ == "__main__":
    unittest.main()
