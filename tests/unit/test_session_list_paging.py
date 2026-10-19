from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from sqlalchemy import event, update

from deep_agent.db.event_codec import encode_messages
from deep_agent.db.models import ChatSession, ChatSessionEvent
from deep_agent.db.session_list import SessionListFilters
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class SessionListPagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.store = self.stores.coordinator
        model = (await self.stores.llm_model.list_all())[0]
        self.agent = await self.stores.agent.create_unique(
            AgentCreate(name="paging-test", model_id=model.id, prompt="test"))
        self.now = datetime(2026, 10, 1, tzinfo=UTC)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_filter_before_page_preserves_scope_sort_and_message_counts(self):
        attachment = {"kind": "attachment-ref", "id": "synthetic-file", "name": "input.txt", "mime": "text/plain"}
        await self.store.append_message_parts("filled-a", messages=[
            ModelRequest(parts=[UserPromptPart(["first prompt", attachment])]),
            ModelResponse(parts=[TextPart("first part"), TextPart("second part")]),
        ], agent_id=self.agent.id, user_id="owner", run_id="first", turn_id="same-turn")
        await self.store.append("filled-a", [ModelRequest(parts=[UserPromptPart("later prompt")])],
            agent_id=self.agent.id, user_id="owner", run_id="later", turn_id="later-turn")
        await self.store.append_lifecycle("filled-a", event_type="compaction", status="completed",
            agent_id=self.agent.id, user_id="owner", run_id="compact", turn_id="ignored-turn", payload={})
        # A tool-results-only session still satisfies the existing has_messages contract.
        await self.store.append("filled-b", [ModelRequest(parts=[ToolReturnPart("lookup", "result", "call")])],
            agent_id=self.agent.id, user_id="owner", run_id="tool-only", turn_id="tool-turn")
        await self.store.append_lifecycle("empty", event_type="compaction", status="completed",
            agent_id=self.agent.id, user_id="owner", run_id="empty", turn_id="empty-turn", payload={})
        await self.store.append("other-user", [ModelRequest(parts=[UserPromptPart("private")])],
            agent_id=self.agent.id, user_id="other-owner", run_id="private", turn_id="private-turn")
        other_agent = await self.stores.agent.create_unique(
            AgentCreate(name="other-paging-agent", model_id=self.agent.model_id, prompt="test"))
        await self.store.append("other-agent", [ModelRequest(parts=[UserPromptPart("other agent")])],
            agent_id=other_agent.id, user_id="owner", run_id="other", turn_id="other-turn")
        async with self.factory() as db:
            for sid, created, updated in [
                ("filled-a", 0, 11), ("filled-b", 1, 10), ("empty", 2, 12),
                ("other-user", 3, 13), ("other-agent", 4, 14),
            ]:
                await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(
                    title="Needle", created_at=self.now + timedelta(hours=created),
                    updated_at=self.now + timedelta(hours=updated)))
            # Legacy NULL turn IDs must remain excluded from COUNT(DISTINCT).
            await db.execute(update(ChatSessionEvent).where(
                ChatSessionEvent.session_id == "filled-a", ChatSessionEvent.run_id == "later"
            ).values(turn_id=None))
            await db.commit()

        base = dict(agent_id=self.agent.id, user_id="owner", query="needle", limit=1)
        first, total = await self.store.list_session_summaries(SessionListFilters(**base, has_messages=True))
        second, second_total = await self.store.list_session_summaries(SessionListFilters(**base, has_messages=True, offset=1))
        self.assertEqual((total, second_total), (2, 2))
        self.assertEqual([first[0].session_id, second[0].session_id], ["filled-a", "filled-b"])
        self.assertEqual((first[0].message_count, first[0].turn_count, first[0].attachment_count), (3, 1, 1))
        self.assertEqual(first[0].user_prompt, "first prompt")
        self.assertEqual((second[0].message_count, second[0].turn_count, second[0].user_prompt), (1, 1, None))
        created, _ = await self.store.list_session_summaries(SessionListFilters(**base, has_messages=True, sort="created_at"))
        self.assertEqual(created[0].session_id, "filled-b")
        empty, empty_total = await self.store.list_session_summaries(SessionListFilters(**base, has_messages=False))
        self.assertEqual((empty_total, empty[0].session_id), (1, "empty"))
        self.assertEqual((empty[0].message_count, empty[0].turn_count), (0, 0))
        ranged, ranged_total = await self.store.list_session_summaries(SessionListFilters(
            **base, has_messages=True, updated_after=self.now + timedelta(hours=10),
            updated_before=self.now + timedelta(hours=11)))
        self.assertEqual((ranged_total, ranged[0].session_id), (2, "filled-a"))
        none, still_total = await self.store.list_session_summaries(SessionListFilters(**base, has_messages=True, offset=2))
        self.assertEqual((none, still_total), ([], 2))

    async def test_history_work_is_bounded_to_the_selected_page(self):
        sessions, events = [], []
        for number in range(60):
            sid = f"scale-{number:03d}"
            sessions.append(dict(session_id=sid, agent_id=self.agent.id, user_id="owner",
                title=sid, next_sequence=61, created_at=self.now, updated_at=self.now))
            for index in range(30):
                messages = [ModelRequest(parts=[UserPromptPart("synthetic")]), ModelResponse(parts=[TextPart("answer")])]
                for offset, draft in enumerate(encode_messages(messages, session_id=sid, run_id=f"run-{index}")):
                    events.append(dict(event_id=draft.event_id, session_id=sid, sequence=index * 2 + offset + 1,
                        schema_version=1, event_type=draft.event_type, status=None, turn_id=f"turn-{index}",
                        run_id=f"run-{index}", payload=draft.payload, created_at=self.now, updated_at=self.now))
        async with self.factory() as db:
            await db.execute(ChatSession.__table__.insert(), sessions)
            await db.execute(ChatSessionEvent.__table__.insert(), events)
            await db.commit()
        for has_messages in (None, True, False):
            statements = []
            def capture(_connection, _cursor, sql, params, _context, _many):
                statements.append((sql, params))
            event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
            try:
                rows, total = await self.store.list_session_summaries(SessionListFilters(
                    agent_id=self.agent.id, user_id="owner", limit=2, offset=3, has_messages=has_messages))
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
            self.assertEqual(total, 0 if has_messages is False else 60)
            self.assertEqual([row.session_id for row in rows], [] if has_messages is False else ["scale-056", "scale-055"])
            self.assertTrue(all(row.message_count == 60 and row.turn_count == 30 for row in rows))
            # The initial page cannot expand into all historical event rows.
            page_sql = statements[0][0].upper()
            self.assertNotIn("JOIN CHAT_SESSION_EVENTS", page_sql)
            self.assertNotIn("GROUP BY", page_sql)
            self.assertNotIn("HAVING", page_sql)
            self.assertLessEqual(len(statements), 6)
            for sql, params in statements[1:-1]:
                self.assertIn("chat_session_events.session_id IN", sql)
                self.assertEqual({p for p in params if isinstance(p, str) and p.startswith("scale-")},
                    {row.session_id for row in rows})


if __name__ == "__main__":
    unittest.main()
