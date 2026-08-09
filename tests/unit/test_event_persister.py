from __future__ import annotations

import asyncio
import unittest

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from deep_agent.agent.event_persister import EventPersister, PersistEvent
from deep_agent.agent.history import is_user_only_request
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

    async def test_flush_completes_when_terminal_worker_exits_before_marker_is_read(self) -> None:
        drained = asyncio.Event()
        release_worker = asyncio.Event()

        class PausingEventPersister(EventPersister):
            async def _drain_pending(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
                await super()._drain_pending(*args, **kwargs)
                drained.set()
                await release_worker.wait()

        await self.persister.aclose()
        persister = PausingEventPersister(self.store)
        self.persister = persister
        await persister.start()
        persister.publish(
            PersistEvent(
                session_id="s-terminal-race",
                agent_id=None,
                user_id=None,
                kind="run_complete",
                new_messages=[ModelResponse(parts=[TextPart(content="reply")])],
            )
        )
        await drained.wait()
        flush_task = asyncio.create_task(persister.flush("s-terminal-race", timeout=0.5))
        await asyncio.sleep(0)
        release_worker.set()
        await flush_task

        history = await self.store.get_history("s-terminal-race")
        self.assertEqual(len(history), 1)

    async def test_run_cancelled_persists_official_partial_text(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append("s3", [user])
        self.persister.publish(
            PersistEvent(
                session_id="s3",
                agent_id=None,
                user_id=None,
                kind="run_cancelled",
                result_messages=[
                    user,
                    ModelResponse(
                        parts=[TextPart(content="partial-")],
                        state="interrupted",
                    ),
                ],
                skip_user_prompt=True,
                repair_dangling_tool_calls=False,
            )
        )
        await self.persister.flush("s3")
        history = await self.store.get_history("s3")
        self.assertEqual(len(history), 2)
        self.assertIsInstance(history[-1], ModelResponse)
        self.assertEqual(history[-1].parts[0].content, "partial-")
        self.assertEqual(history[-1].state, "interrupted")

    async def test_run_cancelled_keeps_official_interrupted_tool_call(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        interrupted = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="builtin__confirm",
                    tool_call_id="tc1",
                    args={"message": "ok"},
                )
            ],
            state="interrupted",
        )
        self.persister.publish(
            PersistEvent(
                session_id="s4",
                agent_id=None,
                user_id=None,
                kind="run_cancelled",
                result_messages=[user, interrupted],
                skip_user_prompt=True,
                repair_dangling_tool_calls=False,
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
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(history[-1].state, "interrupted")

    async def test_run_cancelled_marks_partial_completed_response_interrupted(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append("s5", [user])
        self.persister.publish(
            PersistEvent(
                session_id="s5",
                agent_id=None,
                user_id=None,
                kind="run_cancelled",
                result_messages=[
                    user,
                    ModelResponse(parts=[TextPart(content="partial")]),
                ],
                skip_user_prompt=True,
                repair_dangling_tool_calls=False,
            )
        )

        await self.persister.flush("s5")
        history = await self.store.get_history("s5")

        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1].state, "interrupted")


if __name__ == "__main__":
    unittest.main()
