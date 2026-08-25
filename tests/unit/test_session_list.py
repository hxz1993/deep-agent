from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class SessionListApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "user-a"
        self.stores, self.engine, self.session_factory = make_sqlite_stores_sync()
        async def seed_agents() -> tuple[str, str]:
            model = (await self.stores.llm_model.list_all())[0]
            agent_a = await self.stores.agent.create_unique(
                AgentCreate(name="session-list-a", model_id=model.id, prompt="test")
            )
            agent_b = await self.stores.agent.create_unique(
                AgentCreate(name="session-list-b", model_id=model.id, prompt="test")
            )
            return agent_a.id, agent_b.id

        self.agent_a, self.agent_b = asyncio.run(seed_agents())
        self.app = FastAPI()
        self.app.include_router(api_router)
        ctx = AppContext.for_testing(
            registry=build_registry(),
            stores=self.stores,
            mcp_lease_manager=McpLeaseManager(),
            db_engine=self.engine,
            db_session_factory=self.session_factory,
        )
        init_context(ctx)
        self.client = TestClient(self.app, headers={"user": self.user_id})

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_list_sessions_returns_paginated_wrapper(self) -> None:
        store = get_context().store
        import asyncio

        async def seed() -> None:
            await store.append(
                "sess-1",
                [ModelRequest(parts=[UserPromptPart(content="hello one")])],
                agent_id=self.agent_a,
                user_id=self.user_id,
            )
            await store.append(
                "sess-2",
                [ModelRequest(parts=[UserPromptPart(content="hello two")])],
                agent_id=self.agent_b,
                user_id=self.user_id,
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?limit=1&offset=0")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("session_id", payload["items"][0])

    def test_list_sessions_filters_by_agent_id(self) -> None:
        store = get_context().store
        import asyncio

        async def seed() -> None:
            await store.append(
                "sess-1",
                [ModelRequest(parts=[UserPromptPart(content="only a")])],
                agent_id=self.agent_a,
                user_id=self.user_id,
            )
            await store.append(
                "sess-2",
                [ModelRequest(parts=[UserPromptPart(content="only b")])],
                agent_id=self.agent_b,
                user_id=self.user_id,
            )

        asyncio.run(seed())

        response = self.client.get(f"/api/v1/sessions?agent_id={self.agent_a}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["agent_id"], self.agent_a)
        self.assertEqual(payload["items"][0]["user_prompt"], "only a")

    def test_list_sessions_has_messages_filter(self) -> None:
        store = get_context().store
        import asyncio

        async def seed() -> None:
            await store.append(
                "with-messages",
                [ModelRequest(parts=[UserPromptPart(content="hi")])],
                agent_id=self.agent_a,
                user_id=self.user_id,
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?has_messages=true")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["session_id"], "with-messages")
        self.assertEqual(payload["items"][0]["message_count"], 1)

    def test_list_sessions_requires_user_header(self) -> None:
        response = self.client.get("/api/v1/sessions", headers={"user": ""})
        self.assertEqual(response.status_code, 401)

    def test_list_sessions_ignores_user_id_query_and_uses_header(self) -> None:
        store = get_context().store

        async def seed() -> None:
            await store.append(
                "owned",
                [ModelRequest(parts=[UserPromptPart(content="mine")])],
                agent_id=self.agent_a,
                user_id=self.user_id,
            )
            await store.append(
                "other",
                [ModelRequest(parts=[UserPromptPart(content="not mine")])],
                agent_id=self.agent_a,
                user_id="user-b",
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?user_id=user-b")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["session_id"], "owned")


class SummaryToSessionInfoTests(unittest.TestCase):
    def test_summary_maps_session_id(self) -> None:
        from deep_agent.api.v1.sessions import _summary_to_session_info
        from deep_agent.db.session_list import SessionSummaryRow

        now = datetime.now(timezone.utc)
        info = _summary_to_session_info(
            SessionSummaryRow(
                session_id="sess-1",
                agent_id="agent-1",
                agent_name="Demo",
                created_at=now,
                updated_at=now,
                message_count=3,
                user_prompt="hello",
            )
        )
        self.assertEqual(info.session_id, "sess-1")
        self.assertEqual(info.agent_name, "Demo")
        self.assertEqual(info.message_count, 3)


if __name__ == "__main__":
    unittest.main()
