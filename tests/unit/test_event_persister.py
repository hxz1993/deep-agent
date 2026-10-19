from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

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
from deep_agent.schemas.agent import AgentCreate


class EventPersisterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        model = (await stores.llm_model.list_all())[0]
        self.agent = await stores.agent.create_unique(
            AgentCreate(name="persister-test", model_id=model.id, prompt="test")
        )
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self._engine.dispose()

    async def test_user_turn_persisted_after_flush(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hello")])
        result_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.persister.publish(
            PersistEvent(
                session_id="s1",
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-s1",
                run_id="run-s1",
                kind="user_turn",
                user_message=user,
                result_queue=result_queue,
            )
        )
        await self.persister.flush("s1", agent_id=self.agent.id)
        history = await self.store.get_history("s1", agent_id=self.agent.id)
        self.assertEqual(len(history), 1)
        self.assertTrue(is_user_only_request(history[0]))
        event = result_queue.get_nowait()
        self.assertEqual(event["type"], "session_event_appended")
        self.assertTrue(event["persisted"])
        self.assertEqual(event["event"]["event_type"], "user/message")

    async def test_flush_reports_write_failure_even_after_worker_exited(self) -> None:
        failed = asyncio.Event()

        async def broken_write(*args, **kwargs):
            failed.set()
            raise RuntimeError("storage offline")

        with patch.object(self.store, "append", side_effect=broken_write):
            self.persister.publish(PersistEvent(
                session_id="failed-write", agent_id=self.agent.id, user_id="user-1",
                kind="user_turn", turn_id="failed-run", run_id="failed-run",
                user_message=ModelRequest(parts=[UserPromptPart(content="hello")]),
            ))
            await failed.wait()
            await asyncio.sleep(0)
            with self.assertRaisesRegex(RuntimeError, "persistence failed"):
                await self.persister.flush("failed-write", agent_id=self.agent.id)

    async def test_drained_failure_logs_actual_run_once(self) -> None:
        records = []
        async def write(*args, **kwargs):
            if kwargs["run_id"] == "later-run":
                raise ValueError("PRIVATE_STORAGE_ERROR")
            return []
        with patch.object(self.store, "append", side_effect=write), \
                patch.object(self.persister, "_drain_pending", wraps=self.persister._drain_pending) as drain, \
                patch("deep_agent.diagnostics.emit", side_effect=lambda name, **fields: records.append((name, fields))):
            self.persister.publish(PersistEvent(
                session_id="drained-failure", agent_id=self.agent.id, user_id="owner",
                kind="run_complete", turn_id="earlier-turn", run_id="earlier-run",
                new_messages=[ModelResponse(parts=[TextPart("PRIVATE_EARLIER")])],
                repair_dangling_tool_calls=False,
            ))
            self.persister.publish(PersistEvent(
                session_id="drained-failure", agent_id=self.agent.id, user_id="owner",
                kind="user_turn", turn_id="later-turn", run_id="later-run",
                user_message=ModelRequest(parts=[UserPromptPart("PRIVATE_LATER"), UserPromptPart("PRIVATE_SECOND")]),
            ))
            with self.assertRaisesRegex(RuntimeError, "persistence failed"):
                await self.persister.flush("drained-failure", agent_id=self.agent.id)
            self.assertEqual(drain.await_count, 1)
        failures = [fields for name, fields in records if name == "message_persistence_failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["run_id"], "later-run")
        self.assertEqual(failures[0]["write_part_count"], 2)
        self.assertEqual(failures[0]["phase"], "background")
        self.assertNotIn("PRIVATE", repr(records))

    async def test_background_diagnostic_failure_does_not_replace_storage_exception(self) -> None:
        original = ValueError("PRIVATE_STORAGE_ERROR")
        event = PersistEvent(session_id="failed", agent_id=self.agent.id, user_id="owner",
            kind="user_turn", turn_id="turn", run_id="run",
            user_message=ModelRequest(parts=[UserPromptPart("PRIVATE_BODY")]))
        with patch.object(self.store, "append", side_effect=original), \
                patch("deep_agent.agent.event_persister.persistence_failure_fields", side_effect=RuntimeError("diagnostics unavailable")):
            with self.assertRaises(ValueError) as caught:
                await self.persister._handle_event("failed", self.agent.id, event)
        self.assertIs(caught.exception, original)

    async def test_stalled_write_times_out_and_releases_session_lock(self) -> None:
        exited = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        with patch("deep_agent.agent.event_persister._WRITE_TIMEOUT", 0.01), \
                patch.object(self.store, "append", side_effect=stalled_write):
            self.persister.publish(PersistEvent(
                session_id="stalled-write", agent_id=self.agent.id, user_id="user-1",
                kind="user_turn", turn_id="stalled-run", run_id="stalled-run",
                user_message=ModelRequest(parts=[UserPromptPart(content="hello")]),
            ))
            with self.assertRaisesRegex(RuntimeError, "TimeoutError"):
                await self.persister.flush("stalled-write", agent_id=self.agent.id, timeout=1)
        self.assertTrue(exited.is_set())
        async with asyncio.timeout(1):
            async with self.store.lock("stalled-write", agent_id=self.agent.id):
                pass

    async def test_run_complete_skips_duplicate_user(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append(
            "s2", [user], agent_id=self.agent.id, user_id="user-1",
            turn_id="run-s2", run_id="run-s2",
        )
        new_messages: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content="reply")]),
        ]
        self.persister.publish(
            PersistEvent(
                session_id="s2",
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-s2",
                run_id="run-s2",
                kind="run_complete",
                new_messages=new_messages,
                skip_user_prompt=True,
            )
        )
        await self.persister.flush("s2", agent_id=self.agent.id)
        history = await self.store.get_history("s2", agent_id=self.agent.id)
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
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-terminal-race",
                run_id="run-terminal-race",
                kind="run_complete",
                new_messages=[ModelResponse(parts=[TextPart(content="reply")])],
            )
        )
        await drained.wait()
        flush_task = asyncio.create_task(
            persister.flush("s-terminal-race", agent_id=self.agent.id, timeout=0.5)
        )
        await asyncio.sleep(0)
        release_worker.set()
        await flush_task

        history = await self.store.get_history("s-terminal-race", agent_id=self.agent.id)
        self.assertEqual(len(history), 1)

    async def test_run_cancelled_persists_official_partial_text(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="hi")])
        await self.store.append(
            "s3", [user], agent_id=self.agent.id, user_id="user-1",
            turn_id="run-s3", run_id="run-s3",
        )
        self.persister.publish(
            PersistEvent(
                session_id="s3",
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-s3",
                run_id="run-s3",
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
        await self.persister.flush("s3", agent_id=self.agent.id)
        history = await self.store.get_history("s3", agent_id=self.agent.id)
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
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-s4",
                run_id="run-s4",
                kind="run_cancelled",
                result_messages=[user, interrupted],
                skip_user_prompt=True,
                repair_dangling_tool_calls=False,
            )
        )
        await self.persister.flush("s4", agent_id=self.agent.id)
        history = await self.store.get_history("s4", agent_id=self.agent.id)
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
        await self.store.append(
            "s5", [user], agent_id=self.agent.id, user_id="user-1",
            turn_id="run-s5", run_id="run-s5",
        )
        self.persister.publish(
            PersistEvent(
                session_id="s5",
                agent_id=self.agent.id,
                user_id="user-1",
                turn_id="run-s5",
                run_id="run-s5",
                kind="run_cancelled",
                result_messages=[
                    user,
                    ModelResponse(parts=[TextPart(content="partial")]),
                ],
                skip_user_prompt=True,
                repair_dangling_tool_calls=False,
            )
        )

        await self.persister.flush("s5", agent_id=self.agent.id)
        history = await self.store.get_history("s5", agent_id=self.agent.id)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1].state, "interrupted")


if __name__ == "__main__":
    unittest.main()
