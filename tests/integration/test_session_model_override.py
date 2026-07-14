from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import deps as api_deps
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.sqlite_stores import ensure_title_uses_main_model, make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model


class SessionModelOverrideIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        await ensure_title_uses_main_model(stores)
        self._app = FastAPI()
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
            )
        )
        self._no_title = patch(
            "deep_agent.agent.runner.AgentRunner._maybe_generate_title",
            new_callable=AsyncMock,
        )
        self._no_title.start()
        self._transport = ASGITransport(app=self._app)

        model_a = await stores.llm_model.create_unique(
            LlmModelCreate(name="model-a", model="test:model-a"),
        )
        model_b = await stores.llm_model.create_unique(
            LlmModelCreate(name="model-b", model="test:model-b"),
        )
        agent = await stores.agent.create_unique(
            AgentCreate(
                name="session-model-agent",
                model_id=model_a.id,
                prompt="You are a test agent.",
            )
        )
        self._agent_id = agent.id
        self._model_a_id = model_a.id
        self._model_b_id = model_b.id
        self._session_id = "sess-model-override"

    async def asyncTearDown(self) -> None:
        self._no_title.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _stream(self, body: dict) -> list[dict]:
        with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model("ok")):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    resp = await client.post("/api/v1/chat/stream", json=body)
                    self.assertEqual(resp.status_code, 200, resp.text)
                    return parse_sse_events(resp.text)

    async def test_request_model_id_overrides_agent_default(self) -> None:
        with patch(
            "deep_agent.api.v1.chat.build_runner_for_agent",
            wraps=api_deps.build_runner_for_agent,
        ) as build:
            events = await self._stream(
                {
                    "agent_id": self._agent_id,
                    "session_id": self._session_id,
                    "message": "hello",
                    "model_id": self._model_b_id,
                    "capabilities": [],
                }
            )

        self.assertTrue(any(ev.get("type") == "final" for ev in events))
        self.assertEqual(build.await_args.kwargs.get("model_id"), self._model_b_id)

    async def test_without_model_id_uses_agent_default(self) -> None:
        with patch(
            "deep_agent.api.v1.chat.build_runner_for_agent",
            wraps=api_deps.build_runner_for_agent,
        ) as build:
            events = await self._stream(
                {
                    "agent_id": self._agent_id,
                    "session_id": self._session_id,
                    "message": "hello",
                    "capabilities": [],
                }
            )

        self.assertTrue(any(ev.get("type") == "final" for ev in events))
        self.assertIsNone(build.await_args.kwargs.get("model_id"))

    async def test_invalid_model_id_returns_404(self) -> None:
        with mock_request_disconnect():
            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
                timeout=30.0,
            ) as client:
                resp = await client.post(
                    "/api/v1/chat/stream",
                    json={
                        "agent_id": self._agent_id,
                        "session_id": self._session_id,
                        "message": "hello",
                        "model_id": "00000000-0000-4000-8000-000000009999",
                        "capabilities": [],
                    },
                )
        self.assertEqual(resp.status_code, 404)

    async def test_switch_model_between_messages(self) -> None:
        with patch(
            "deep_agent.api.v1.chat.build_runner_for_agent",
            wraps=api_deps.build_runner_for_agent,
        ) as build:
            await self._stream(
                {
                    "agent_id": self._agent_id,
                    "session_id": self._session_id,
                    "message": "first",
                    "model_id": self._model_b_id,
                    "capabilities": [],
                }
            )
            await self._stream(
                {
                    "agent_id": self._agent_id,
                    "session_id": self._session_id,
                    "message": "second",
                    "model_id": self._model_a_id,
                    "capabilities": [],
                }
            )

        self.assertEqual(build.await_args_list[0].kwargs.get("model_id"), self._model_b_id)
        self.assertEqual(build.await_args_list[1].kwargs.get("model_id"), self._model_a_id)

    async def test_history_does_not_persist_session_model(self) -> None:
        await self._stream(
            {
                "agent_id": self._agent_id,
                "session_id": self._session_id,
                "message": "hello",
                "model_id": self._model_b_id,
                "capabilities": [],
            }
        )
        await get_context().event_persister.flush(self._session_id, agent_id=self._agent_id)

        async with AsyncClient(transport=self._transport, base_url="http://test", timeout=30.0) as client:
            history = await client.get(
                f"/api/v1/sessions/{self._session_id}/history",
                params={"agent_id": self._agent_id},
            )
        self.assertEqual(history.status_code, 200)
        payload = history.json()
        self.assertNotIn("llm_model_id", payload)
        self.assertGreater(payload.get("model_message_count", 0), 0)


if __name__ == "__main__":
    unittest.main()
