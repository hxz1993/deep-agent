from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from sqlalchemy import text

from deep_agent.api.v1 import deps as api_deps
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import (
    ensure_title_uses_main_model,
    get_default_test_provider_id,
    make_sqlite_stores,
)
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model


class SessionModelOverrideIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        await ensure_title_uses_main_model(stores)
        self._app = FastAPI()
        install_test_user_header(self._app)
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

        provider_id = await get_default_test_provider_id(stores)
        model_a = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=provider_id,
                name="model-a",
                model="test:model-a",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
        )
        model_b = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=provider_id,
                name="model-b",
                model="test:model-b",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
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

    async def test_context_status_and_final_event_expose_read_only_budget(
        self,
    ) -> None:
        events = await self._stream(
            {
                "agent_id": self._agent_id,
                "session_id": self._session_id,
                "message": "hello",
                "capabilities": [],
            }
        )
        await get_context().event_persister.flush(
            self._session_id,
            agent_id=self._agent_id,
        )

        final = next(event for event in events if event.get("type") == "final")
        self.assertEqual(
            final["context"]["budget"]["context_window_tokens"],
            128_000,
        )
        self.assertEqual(
            final["context"]["budget"]["reason"],
            "enabled",
        )

        async with AsyncClient(
            transport=self._transport,
            base_url="http://test",
            timeout=30.0,
        ) as client:
            response = await client.get(
                f"/api/v1/sessions/{self._session_id}/context-status",
                params={"agent_id": self._agent_id},
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["context_window_tokens"], 128_000)
        self.assertEqual(payload["max_output_tokens"], 8_192)
        self.assertIsInstance(payload["safety_margin_tokens"], int)
        self.assertIsInstance(payload["usable_request_tokens"], int)
        self.assertGreater(payload["safety_margin_tokens"], 0)
        self.assertGreater(payload["usable_request_tokens"], 0)
        self.assertTrue(payload["compaction_enabled"])
        self.assertFalse(payload["checkpoint_exists"])
        self.assertFalse(payload["checkpoint_active"])
        self.assertTrue(payload["manual_compaction_available"])
        self.assertEqual(payload["manual_compaction_reason"], "available")

    async def test_context_status_keeps_budget_fields_null_when_window_is_unknown(
        self,
    ) -> None:
        await self._stream(
            {
                "agent_id": self._agent_id,
                "session_id": self._session_id,
                "message": "hello",
                "capabilities": [],
            }
        )
        await get_context().event_persister.flush(
            self._session_id,
            agent_id=self._agent_id,
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE llm_models "
                    "SET context_window_tokens = NULL, max_output_tokens = NULL "
                    "WHERE id = :model_id"
                ),
                {"model_id": self._model_a_id},
            )

        async with AsyncClient(
            transport=self._transport,
            base_url="http://test",
            timeout=30.0,
        ) as client:
            response = await client.get(
                f"/api/v1/sessions/{self._session_id}/context-status",
                params={"agent_id": self._agent_id},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsNone(payload["safety_margin_tokens"])
        self.assertIsNone(payload["usable_request_tokens"])
        self.assertFalse(payload["compaction_enabled"])
        self.assertEqual(payload["compaction_reason"], "context_window_unknown")
        self.assertFalse(payload["manual_compaction_available"])
        self.assertEqual(payload["manual_compaction_reason"], "context_window_unknown")

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("unused"),
        ):
            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
                timeout=30.0,
            ) as client:
                compact = await client.post(
                    f"/api/v1/sessions/{self._session_id}/compact",
                    params={"agent_id": self._agent_id},
                    json={},
                )
        self.assertEqual(compact.status_code, 422)
        self.assertEqual(compact.json()["detail"]["code"], "context_window_unknown")

    async def test_manual_compaction_stream_updates_status_and_history_marker(self) -> None:
        history = []
        for turn in range(3):
            history.extend(
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                content=f"第 {turn} 轮项目记录：" + "必须保留这项背景。" * 500
                            )
                        ]
                    ),
                    ModelResponse(
                        parts=[TextPart(content=f"第 {turn} 轮决定：" + "确认当前方案。" * 500)]
                    ),
                ]
            )
        await get_context().store.append(
            self._session_id,
            history,
            agent_id=self._agent_id,
            user_id="integration-test-user",
        )

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("保留既有决定、约束与未决事项。"),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    response = await client.post(
                        f"/api/v1/sessions/{self._session_id}/compact",
                        params={"agent_id": self._agent_id},
                        json={"model_id": self._model_b_id},
                    )
        self.assertEqual(response.status_code, 200, response.text)
        events = parse_sse_events(response.text)
        result = events[-1]
        self.assertEqual(result["type"], "context_compaction_result")
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("context_status", result)

        async with AsyncClient(
            transport=self._transport,
            base_url="http://test",
            timeout=30.0,
        ) as client:
            history_response = await client.get(
                f"/api/v1/sessions/{self._session_id}/history",
                params={
                    "agent_id": self._agent_id,
                    "model_id": self._model_b_id,
                },
            )
            context_status_response = await client.get(
                f"/api/v1/sessions/{self._session_id}/context-status",
                params={
                    "agent_id": self._agent_id,
                    "model_id": self._model_b_id,
                },
            )
        self.assertEqual(history_response.status_code, 200, history_response.text)
        self.assertEqual(context_status_response.status_code, 200, context_status_response.text)
        context_status = context_status_response.json()
        self.assertTrue(context_status["checkpoint_active"])
        self.assertTrue(context_status["manual_compaction_available"])
        self.assertEqual(context_status["manual_compaction_reason"], "available")
        payload = history_response.json()
        self.assertEqual(payload["model_message_count"], len(history))
        self.assertIsNotNone(payload["context_checkpoint"])
        self.assertEqual(payload["context_checkpoint"]["row_version"], 1)

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("不会再次调用"),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    repeated = await client.post(
                        f"/api/v1/sessions/{self._session_id}/compact",
                        params={"agent_id": self._agent_id},
                        json={"model_id": self._model_b_id},
                    )
        repeated_result = parse_sse_events(repeated.text)[-1]
        self.assertEqual(repeated_result["status"], "noop")
        self.assertEqual(repeated_result["reason"], "no_reduction")
        self.assertNotIn("context_status", repeated_result)

        async with AsyncClient(
            transport=self._transport,
            base_url="http://test",
            timeout=30.0,
        ) as client:
            other_model_history = await client.get(
                f"/api/v1/sessions/{self._session_id}/history",
                params={"agent_id": self._agent_id},
            )
        self.assertIsNone(other_model_history.json()["context_checkpoint"])

    async def test_manual_compaction_rejects_pending_tool_interaction(self) -> None:
        await get_context().store.append(
            "manual-pending",
            [
                ModelRequest(parts=[UserPromptPart(content="请确认")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "builtin__confirm",
                            {"message": "继续？"},
                            tool_call_id="pending-confirm",
                        )
                    ]
                ),
            ],
            agent_id=self._agent_id,
            user_id="integration-test-user",
        )
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("unused"),
        ):
            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
                timeout=30.0,
            ) as client:
                response = await client.post(
                    "/api/v1/sessions/manual-pending/compact",
                    params={"agent_id": self._agent_id},
                    json={},
                )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "pending_deferred")


if __name__ == "__main__":
    unittest.main()
