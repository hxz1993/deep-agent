from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deep_agent.db.models import Agent, Base, ChatMessage, ChatSession
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.message import create_message_store
from deep_agent.db.stores.session_query import SessionQueryStoreBase, create_session_query_store
from deep_agent.db.session_key import scoped_session_key

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


class SessionQueryStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        message_store = create_message_store(DbDialect.POSTGRES, self.session_factory)
        self.store: SessionQueryStoreBase = create_session_query_store(
            DbDialect.POSTGRES,
            self.session_factory,
            message_store,
        )

        async with self.session_factory() as db:
            agent = Agent(
                id="00000000-0000-4000-8000-000000000001",
                name="Demo Agent",
                model="test",
                prompt="test",
            )
            db.add(agent)
            now = datetime.now(timezone.utc)
            session_key = scoped_session_key(agent.id, "sess-1")
            db.add(
                ChatSession(
                    session_key=session_key,
                    agent_id=agent.id,
                    user_id="user-1",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                ChatMessage(
                    id=1,
                    session_key=session_key,
                    sequence=0,
                    payload=_message_adapter.dump_python(
                        ModelRequest(parts=[UserPromptPart(content="hello sql")]),
                        mode="json",
                    ),
                )
            )
            await db.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_list_summaries_joins_agent_and_counts_messages(self) -> None:
        rows, total = await self.store.list_summaries()

        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.agent_name, "Demo Agent")
        self.assertEqual(row.message_count, 1)
        self.assertEqual(row.user_prompt, "hello sql")

    async def test_list_summaries_filters_by_agent_and_user(self) -> None:
        rows, total = await self.store.list_summaries(
            agent_id="00000000-0000-4000-8000-000000000001",
            user_id="user-1",
        )
        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)

        rows, total = await self.store.list_summaries(user_id="missing")
        self.assertEqual(total, 0)
        self.assertEqual(rows, [])

    async def test_list_summaries_pagination(self) -> None:
        async with self.session_factory() as db:
            now = datetime.now(timezone.utc)
            for index in range(3):
                key = scoped_session_key("00000000-0000-4000-8000-000000000001", f"sess-extra-{index}")
                db.add(
                    ChatSession(
                        session_key=key,
                        agent_id="00000000-0000-4000-8000-000000000001",
                        created_at=now,
                        updated_at=now,
                    )
                )
            await db.commit()

        rows, total = await self.store.list_summaries(limit=2, offset=1)

        self.assertEqual(total, 4)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
