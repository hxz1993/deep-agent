from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from deep_agent.agent.event_persister import EventPersister, PersistEvent
from deep_agent.agent.history import is_user_only_request
from deep_agent.agent.stream_accumulator import StreamAccumulator
from tests.helpers.sqlite_stores import make_sqlite_stores


class EventPersisterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self._engine.dispose()

    async def test_user_turn_persisted_after_flush(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hello")])
        self.persister.publish(
            PersistEvent(
                session_id="s1",
                agent_id=None,
                user_id=None,
                kind="user_turn",
                user_message=user,
            )
        )
        await self.persister.flush("s1")
        history = await self.store.get_history("s1")
        self.assertEqual(len(history), 1)
        self.assertTrue(is_user_only_request(history[0]))

    async def test_run_complete_skips_duplicate_user(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append("s2", [user])
        new_messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="reply")]),
        ]
        self.persister.publish(
            PersistEvent(
                session_id="s2",
                agent_id=None,
                user_id=None,
                kind="run_complete",
                new_messages=new_messages,
                skip_user_prompt=True,
            )
        )
        await self.persister.flush("s2")
        history = await self.store.get_history("s2")
        self.assertEqual(len(history), 2)
        users = [m for m in history if is_user_only_request(m)]
        self.assertEqual(len(users), 1)

    async def test_run_cancelled_accumulator_partial_text(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append("s3", [user])
        acc = StreamAccumulator()
        acc.ingest({"type": "text", "delta": "partial-"})
        self.persister.publish(
            PersistEvent(
                session_id="s3",
                agent_id=None,
                user_id=None,
                kind="run_cancelled",
                accumulator=acc,
                skip_user_prompt=True,
            )
        )
        await self.persister.flush("s3")
        history = await self.store.get_history("s3")
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[-1], ModelResponse)
        self.assertEqual(history[-1].parts[0].content, "partial-")

    async def test_run_cancelled_deferred_omits_tool_calls(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        assistant = ModelResponse(
            parts=[],
            state="interrupted",
        )
        await self.store.append("s4", [user, assistant])
        acc = StreamAccumulator()
        acc.ingest(
            {
                "type": "tool_call",
                "tool": "confirm_action",
                "tool_call_id": "tc1",
                "args": {"message": "ok"},
            }
        )
        acc.ingest(
            {
                "type": "tool_result",
                "tool": "confirm_action",
                "tool_call_id": "tc1",
                "result": {"confirmed": True},
            }
        )
        self.persister.publish(
            PersistEvent(
                session_id="s4",
                agent_id=None,
                user_id=None,
                kind="run_cancelled",
                accumulator=acc,
                skip_user_prompt=True,
                include_tool_calls=False,
            )
        )
        await self.persister.flush("s4")
        history = await self.store.get_history("s4")
        tool_calls = [
            p
            for m in history
            if isinstance(m, ModelResponse)
            for p in m.parts
            if getattr(p, "tool_name", None)
        ]
        self.assertEqual(len(tool_calls), 0)


if __name__ == "__main__":
    unittest.main()
