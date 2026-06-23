from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.api.v1 import api_router
from deep_agent.core.context import get_context, reset_context
from deep_agent.db.session_key import scoped_session_key
from tests.integration.helpers.chat import create_test_app


class SessionListApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        reset_context()

    def test_list_sessions_returns_paginated_wrapper(self) -> None:
        store = get_context().store
        import asyncio

        async def seed() -> None:
            await store.append(
                scoped_session_key("agent-a", "sess-1"),
                [ModelRequest(parts=[UserPromptPart(content="hello one")])],
            )
            await store.append(
                scoped_session_key("agent-b", "sess-2"),
                [ModelRequest(parts=[UserPromptPart(content="hello two")])],
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
                scoped_session_key("agent-a", "sess-1"),
                [ModelRequest(parts=[UserPromptPart(content="only a")])],
            )
            await store.append(
                scoped_session_key("agent-b", "sess-2"),
                [ModelRequest(parts=[UserPromptPart(content="only b")])],
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?agent_id=agent-a")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["agent_id"], "agent-a")
        self.assertEqual(payload["items"][0]["user_prompt"], "only a")

    def test_list_sessions_has_messages_filter(self) -> None:
        store = get_context().store
        import asyncio

        async def seed() -> None:
            await store.append(
                "with-messages",
                [ModelRequest(parts=[UserPromptPart(content="hi")])],
            )

        asyncio.run(seed())

        response = self.client.get("/api/v1/sessions?has_messages=true")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["session_id"], "with-messages")
        self.assertEqual(payload["items"][0]["message_count"], 1)


class SummaryToSessionInfoTests(unittest.TestCase):
    def test_summary_maps_scoped_session_id(self) -> None:
        from deep_agent.api.v1.sessions import _summary_to_session_info
        from deep_agent.db.repositories.session import SessionSummaryRow

        now = datetime.now(timezone.utc)
        info = _summary_to_session_info(
            SessionSummaryRow(
                session_key="agent-1:sess-1",
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
