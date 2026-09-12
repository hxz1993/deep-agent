from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.db.session_list import SessionListFilters
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class ChatSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.store = self.stores.coordinator
        model = (await self.stores.llm_model.list_all())[0]
        agent = await self.stores.agent.create_unique(
            AgentCreate(name="session-test", model_id=model.id, prompt="test")
        )
        self.agent_id = agent.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_list_summaries_joins_agent_and_counts_messages(self) -> None:
        await self.store.append(
            "sess-1",
            [ModelRequest(parts=[UserPromptPart(content=[
                "hello sql",
                {"kind": "attachment-ref", "id": "file-1", "name": "input.xlsx", "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            ])])],
            agent_id=self.agent_id,
            user_id="user-1",
            app_id="initial001",
            space_id="initial",
            turn_id="1" * 32,
            run_id="run-1",
        )
        await self.store.append(
            "sess-1",
            [ModelRequest(parts=[UserPromptPart(content="second turn")])],
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="2" * 32,
            run_id="run-2",
            app_id="later002", space_id="later",
        )
        rows, total = await self.store.list_session_summaries(
            SessionListFilters(user_id="user-1")
        )
        self.assertEqual((rows[0].app_id, rows[0].space_id), ("initial001", "initial"))
        self.assertEqual(await self.stores.chat_session.get_initial_scope(self.agent_id, "sess-1"), ("initial001", "initial"))
        self.assertEqual(await self.stores.chat_session.get_initial_scope("another-agent", "sess-1"), (None, None))
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual(rows[0].turn_count, 2)
        self.assertEqual(rows[0].attachment_count, 1)
        self.assertEqual(rows[0].user_prompt, "hello sql")

    async def test_filters_and_has_messages_total_match(self) -> None:
        await self.store.append_lifecycle(
            "empty", event_type="compaction", status="completed",
            agent_id=self.agent_id, user_id="user-1", turn_id="2" * 32,
            run_id="run-empty", payload={},
        )
        await self.store.append(
            "filled", [ModelRequest(parts=[UserPromptPart(content="有消息")])],
            agent_id=self.agent_id, user_id="user-1", turn_id="3" * 32,
            run_id="run-filled",
        )
        filled, filled_total = await self.store.list_session_summaries(
            SessionListFilters(user_id="user-1", has_messages=True)
        )
        empty, empty_total = await self.store.list_session_summaries(
            SessionListFilters(user_id="user-1", has_messages=False)
        )
        self.assertEqual(filled_total, 1)
        self.assertEqual([item.session_id for item in filled], ["filled"])
        self.assertEqual(empty_total, 1)
        self.assertEqual([item.session_id for item in empty], ["empty"])

    async def test_global_session_id_cannot_be_reowned(self) -> None:
        await self.store.append_lifecycle(
            "global", event_type="compaction", status="completed",
            agent_id=self.agent_id, user_id="user-1", turn_id="4" * 32,
            run_id="run-global", payload={},
        )
        with self.assertRaises(PermissionError):
            await self.store.append_lifecycle(
                "global", event_type="compaction", status="completed",
                agent_id=self.agent_id, user_id="user-2", turn_id="5" * 32,
                run_id="run-other", payload={},
            )


if __name__ == "__main__":
    unittest.main()
