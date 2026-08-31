from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class SessionOwnershipApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = "user-a"
        self.stores, self.engine, self.session_factory = make_sqlite_stores_sync()
        app = FastAPI()
        app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self.engine,
                db_session_factory=self.session_factory,
            )
        )
        self.client = TestClient(app, headers={"user": self.user_id})
        asyncio.run(self._seed())

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    async def _seed(self) -> None:
        store = get_context().store
        model = (await self.stores.llm_model.list_all())[0]
        agent = await self.stores.agent.create_unique(
            AgentCreate(name="ownership-test", model_id=model.id, prompt="test")
        )
        self.agent_id = agent.id
        message = [ModelRequest(parts=[UserPromptPart(content="hello")])]
        await store.append(
            "mine", message, agent_id=self.agent_id, user_id=self.user_id,
            turn_id="run-mine", run_id="run-mine",
        )
        await store.append(
            "other", message, agent_id=self.agent_id, user_id="user-b",
            turn_id="run-other", run_id="run-other",
        )

    def test_get_and_events_only_return_owned_session(self) -> None:
        detail = self.client.get("/api/v1/sessions/mine")
        self.assertEqual(detail.status_code, 200)
        history = self.client.get("/api/v1/sessions/mine/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["schema_version"], 1)
        self.assertEqual(history.json()["items"][0]["kind"], "user_message")
        events = self.client.get("/api/v1/sessions/mine/events")
        self.assertEqual(events.status_code, 200)
        listed = self.client.get("/api/v1/sessions")
        self.assertEqual(listed.status_code, 200)
        timestamp_pairs = [
            detail.json(),
            history.json()["items"][0],
            events.json()["items"][0],
            listed.json()["items"][0],
        ]
        for payload in timestamp_pairs:
            with self.subTest(payload=payload):
                self.assertTrue(payload["created_at"].endswith("+00:00"))
                self.assertTrue(payload["updated_at"].endswith("+00:00"))
        self.assertEqual(self.client.get("/api/v1/sessions/other").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/sessions/other/history").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/sessions/other/events").status_code,
            404,
        )

    def test_patch_and_delete_only_change_owned_session(self) -> None:
        self.assertEqual(
            self.client.patch(
                "/api/v1/sessions/other",
                json={"title": "not allowed"},
            ).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/v1/sessions/other").status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/sessions/mine").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/sessions/mine").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/sessions/other", headers={"user": "user-b"}).status_code,
            200,
        )

    def test_chat_endpoints_cannot_resume_unowned_session(self) -> None:
        for path in ("/api/v1/chat", "/api/v1/chat/stream"):
            for session_id in ("other",):
                with self.subTest(path=path, session_id=session_id):
                    response = self.client.post(
                        path,
                        json={"session_id": session_id, "message": "continue"},
                    )
                    self.assertEqual(response.status_code, 404)

    def test_missing_user_header_is_rejected(self) -> None:
        requests = (
            ("get", "/api/v1/sessions", None),
            ("get", "/api/v1/sessions/mine", None),
            ("get", "/api/v1/sessions/mine/history", None),
            ("get", "/api/v1/sessions/mine/events", None),
            ("patch", "/api/v1/sessions/mine", {"title": "blocked"}),
            ("delete", "/api/v1/sessions/mine", None),
            ("post", "/api/v1/chat", {"message": "blocked"}),
            ("post", "/api/v1/chat/stream", {"message": "blocked"}),
        )
        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    path,
                    headers={"user": ""},
                    json=body,
                )
                self.assertEqual(response.status_code, 401)

    def test_user_body_is_not_an_authentication_fallback(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            headers={"user": ""},
            json={"session_id": "mine", "message": "continue", "user": self.user_id},
        )
        self.assertEqual(response.status_code, 401)

    def test_openapi_documents_user_header_without_user_body_or_query(self) -> None:
        spec = self.client.get("/openapi.json").json()
        operations = [
            spec["paths"]["/api/v1/chat"]["post"],
            spec["paths"]["/api/v1/chat/stream"]["post"],
            spec["paths"]["/api/v1/sessions"]["get"],
            spec["paths"]["/api/v1/sessions/{session_id}"]["get"],
            spec["paths"]["/api/v1/sessions/{session_id}"]["patch"],
            spec["paths"]["/api/v1/sessions/{session_id}"]["delete"],
        ]
        for operation in operations:
            parameters = operation.get("parameters", [])
            self.assertIn(
                ("user", "header"),
                {(item["name"], item["in"]) for item in parameters},
            )
            self.assertNotIn(
                ("user_id", "query"),
                {(item["name"], item["in"]) for item in parameters},
            )

        chat_schema = spec["components"]["schemas"]["ChatRequest"]
        self.assertNotIn("user", chat_schema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
