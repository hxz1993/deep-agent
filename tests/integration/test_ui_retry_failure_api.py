"""UI retry exhaustion through real HTTP/SSE, SDK and isolated SQLite."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import get_default_test_provider_id, make_sqlite_stores
from tests.integration.helpers.chat import aclose_test_context, mock_request_disconnect, parse_sse_events


class UIRetryFailureAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stores, self.engine, factory = await make_sqlite_stores()
        model = await stores.llm_configuration.create_model(LlmModelCreate(
            provider_id=await get_default_test_provider_id(stores),
            name="synthetic-ui-model", model="private:ui-retry", input_modalities=["text"],
            context_window_tokens=32000, max_output_tokens=2000,
        ))
        self.agent = await stores.agent.create_unique(AgentCreate(
            name="synthetic-ui-agent", model_id=model.id, prompt="Synthetic confirmation test.",
            capabilities=["ui:builtin"],
        ))
        self.app = FastAPI()
        install_test_user_header(self.app)
        self.app.include_router(api_router)
        init_context(AppContext.for_testing(
            registry=build_registry(), stores=stores, mcp_lease_manager=McpLeaseManager(),
            db_engine=self.engine, db_session_factory=factory,
        ))
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.no_title = patch("deep_agent.agent.runner.AgentRunner._generate_model_title", new_callable=AsyncMock)
        self.no_title.start()

    async def asyncTearDown(self):
        self.no_title.stop()
        await self.client.aclose()
        await aclose_test_context(engine=self.engine)

    async def test_exhaustion_closes_sse_history_and_allows_next_request(self):
        attempts = 0

        async def invalid(_messages, _info):
            nonlocal attempts
            attempts += 1
            yield {0: DeltaToolCall(name="builtin__confirm", json_args="{}", tool_call_id=f"invalid-{attempts}")}

        async def send(model, session_id=None):
            with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=model)):
                with mock_request_disconnect():
                    response = await asyncio.wait_for(self.client.post("/api/v1/chat/stream", json={
                        "agent_id": self.agent.id, "session_id": session_id, "message": "Synthetic request",
                    }), timeout=8)
            self.assertEqual(response.status_code, 200, response.text[:300])
            return parse_sse_events(response.text)

        events = await send(invalid)
        self.assertEqual(attempts, 4)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["history_persisted"])
        self.assertFalse(any(e["type"] == "tool_call" for e in events))
        session_id = events[0]["session_id"]
        status = await self.client.get(f"/api/v1/sessions/{session_id}/run-status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "idle")
        stored = await self.client.get(f"/api/v1/sessions/{session_id}/events", params={"agent_id": self.agent.id})
        self.assertEqual(stored.status_code, 200, stored.text)
        failures = [row for row in stored.json()["items"] if row["event_type"] == "execution/error"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["payload"]["code"], "tool_retry_exhausted")
        self.assertEqual(failures[0]["payload"]["phase"], "tool_validation")
        self.assertFalse(any(row["event_type"].startswith("run/") for row in stored.json()["items"]))
        history = await self.client.get(f"/api/v1/sessions/{session_id}/history", params={"agent_id": self.agent.id})
        self.assertEqual(history.status_code, 200)
        items = history.json()["items"]
        self.assertFalse(any(i["status"] in {"pending", "awaiting_input"} for i in items))
        tools = [i for i in items if i["kind"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "failed")

        async def recovered(_messages, _info):
            yield "Synthetic recovery completed."

        continued = await send(recovered, session_id)
        self.assertEqual(continued[-1]["type"], "final")
        self.assertEqual(continued[-1]["status"], "completed")
