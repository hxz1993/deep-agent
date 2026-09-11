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
from deep_agent.db.models import ChatSession
from sqlalchemy import update
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
                turn_id="run-sess-1",
                run_id="run-sess-1",
            )
            await store.append(
                "sess-2",
                [ModelRequest(parts=[UserPromptPart(content="hello two")])],
                agent_id=self.agent_b,
                user_id=self.user_id,
                turn_id="run-sess-2",
                run_id="run-sess-2",
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
                turn_id="run-sess-1",
                run_id="run-sess-1",
            )
            await store.append(
                "sess-2",
                [ModelRequest(parts=[UserPromptPart(content="only b")])],
                agent_id=self.agent_b,
                user_id=self.user_id,
                turn_id="run-sess-2",
                run_id="run-sess-2",
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
                turn_id="run-with-messages",
                run_id="run-with-messages",
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?has_messages=true")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["session_id"], "with-messages")
        self.assertEqual(payload["items"][0]["message_count"], 1)
        self.assertEqual(payload["items"][0]["turn_count"], 1)
        self.assertEqual(payload["items"][0]["attachment_count"], 0)

    def test_list_sessions_requires_user_header(self) -> None:
        response = self.client.get("/api/v1/sessions", headers={"user": ""})
        self.assertEqual(response.status_code, 401)

    def test_search_title_substring_or_exact_id_preserves_scope_and_pagination(self) -> None:
        store = get_context().store
        async def seed() -> None:
            for sid, title, agent, user in [
                ("old-session-exact", "季度 Sales 汇总", self.agent_a, self.user_id),
                ("new-session", "季度 Sales 明细", self.agent_a, self.user_id),
                ("different-agent", "季度 Sales 汇总", self.agent_b, self.user_id),
                ("other-user", "季度 Sales 汇总", self.agent_a, "user-b"),
                ("literal-symbols", "增长 100%_完成", self.agent_a, self.user_id),
            ]:
                await store.append(sid, [ModelRequest(parts=[UserPromptPart(content="not the title")])],
                    agent_id=agent, user_id=user, turn_id=f"turn-{sid}", run_id=f"run-{sid}")
                async with self.session_factory() as db:
                    await db.execute(update(ChatSession).where(ChatSession.session_id == sid).values(title=title))
                    await db.commit()
        asyncio.run(seed())

        def search(query, **extra):
            result = self.client.get("/api/v1/sessions", params={
                "agent_id": self.agent_a, "query": query, **extra,
            })
            self.assertEqual(result.status_code, 200, result.text)
            return result.json()

        first = search("  sales  ", limit=1)
        second = search("sales", limit=1, offset=1)
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertNotEqual(first["items"][0]["session_id"], second["items"][0]["session_id"])
        self.assertEqual(search("季度")["total"], 2)
        self.assertEqual(search("old-session")["total"], 0)
        self.assertEqual(search("old-session-exact")["items"][0]["session_id"], "old-session-exact")
        self.assertEqual(search("other-user", user_id="user-b")["total"], 0)
        self.assertEqual(search("different-agent")["total"], 0)
        self.assertEqual(search("%_")["total"], 1)
        self.assertEqual(search("not the title")["total"], 0)
        self.assertEqual(search("   ")["total"], 3)
        self.assertEqual(search("sales", has_messages=False)["total"], 0)

    def test_search_length_and_auth_validation(self) -> None:
        self.assertEqual(self.client.get("/api/v1/sessions", params={"query": "x" * 257}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/sessions?query=other", headers={"user": ""}).status_code, 401)

    def test_naive_utc_query_parameter_remains_supported(self) -> None:
        response = self.client.get(
            "/api/v1/sessions?updated_before=2099-01-01T00:00:00"
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_list_sessions_ignores_user_id_query_and_uses_header(self) -> None:
        store = get_context().store

        async def seed() -> None:
            await store.append(
                "owned",
                [ModelRequest(parts=[UserPromptPart(content="mine")])],
                agent_id=self.agent_a,
                user_id=self.user_id,
                turn_id="run-owned",
                run_id="run-owned",
            )
            await store.append(
                "other",
                [ModelRequest(parts=[UserPromptPart(content="not mine")])],
                agent_id=self.agent_a,
                user_id="user-b",
                turn_id="run-other",
                run_id="run-other",
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
                app_id="initial001", space_id="initial",
            )
        )
        self.assertEqual((info.app_id, info.space_id), ("initial001", "initial"))
        self.assertEqual(info.session_id, "sess-1")
        self.assertEqual(info.agent_name, "Demo")
        self.assertEqual(info.message_count, 3)
        self.assertEqual(info.turn_count, 0)
        self.assertEqual(info.attachment_count, 0)


if __name__ == "__main__":
    unittest.main()
