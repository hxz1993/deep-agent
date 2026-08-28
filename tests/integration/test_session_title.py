"""First-turn title event and SSE integration tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.agent.title_generator import TitleGenerationResult
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.helpers.sqlite_stores import get_default_test_provider_id, make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model


class SessionTitleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, self._session_factory = await make_sqlite_stores()
        model_row = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=await get_default_test_provider_id(stores),
                name="chat-model",
                model="test:chat-model",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            )
        )
        self._model_id = model_row.id
        override_model = await stores.llm_configuration.create_model(
            LlmModelCreate(
                provider_id=await get_default_test_provider_id(stores),
                name="title-override-model",
                model="test:title-override-model",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            )
        )
        self._override_model_id = override_model.id
        self._stores = stores
        self._agent_id = (
            await stores.agent.create_unique(
                AgentCreate(name="title-agent", model_id=model_row.id, prompt="helpful assistant")
            )
        ).id
        self._app = FastAPI()
        install_test_user_header(self._app)
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=self._session_factory,
            )
        )
        self._transport = ASGITransport(app=self._app)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _stream(
        self,
        *,
        message: str,
        session_id: str | None = None,
        model_id: str | None = None,
        context_refs: list[dict] | None = None,
        generated_title: TitleGenerationResult | None = TitleGenerationResult(
            title="分析预算差异", image_used=False
        ),
    ) -> tuple[str, list[dict], AsyncMock]:
        body = {"message": message, "agent_id": self._agent_id, "capabilities": []}
        if session_id:
            body["session_id"] = session_id
        if model_id:
            body["model_id"] = model_id
        if context_refs:
            body["context_refs"] = context_refs
        title_mock = AsyncMock(return_value=generated_title)
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("好的，我来处理。"),
        ), patch("deep_agent.agent.title_generator.generate_title", title_mock), mock_request_disconnect():
            async with AsyncClient(
                transport=self._transport, base_url="http://test", timeout=30.0
            ) as client:
                async with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
                    self.assertEqual(response.status_code, 200, await response.aread())
                    raw = "".join([chunk async for chunk in response.aiter_text()])
        events = parse_sse_events(raw)
        resolved = next(event["session_id"] for event in events if event["type"] == "session")
        await get_context().event_persister.flush(resolved, agent_id=self._agent_id)
        return resolved, events, title_mock

    async def _chat(
        self,
        *,
        message: str,
        generated_title: TitleGenerationResult | None,
    ) -> tuple[dict, AsyncMock]:
        title_mock = AsyncMock(return_value=generated_title)
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("好的，我来处理。"),
        ), patch("deep_agent.agent.title_generator.generate_title", title_mock):
            async with AsyncClient(
                transport=self._transport, base_url="http://test", timeout=30.0
            ) as client:
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "message": message,
                        "agent_id": self._agent_id,
                        "capabilities": [],
                    },
                )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), title_mock

    async def test_first_turn_emits_fallback_and_model_title_before_final(self) -> None:
        session_id, events, title_mock = await self._stream(message="请分析本季度预算差异")
        title_events = [
            item for item in events
            if item["type"] == "session_event_appended"
            and item.get("event", {}).get("event_type") == "session/title"
        ]
        self.assertEqual([item["event"]["payload"]["source"] for item in title_events], ["fallback", "model"])
        title_projections = [item for item in events if item["type"] == "session_title"]
        self.assertEqual([item["source"] for item in title_projections], ["fallback", "model"])
        self.assertEqual(title_projections[-1]["title"], "分析预算差异")
        self.assertLess(events.index(title_events[-1]), next(i for i, item in enumerate(events) if item["type"] == "final"))
        title_mock.assert_awaited_once()
        call = title_mock.await_args.kwargs
        self.assertIsInstance(call["model"], TestModel)
        self.assertEqual(call["user_text"], "请分析本季度预算差异")
        self.assertEqual(title_events[-1]["event"]["payload"]["model_name"], "test:chat-model")

        detail = await self._stores.coordinator.get_session_title(
            session_id, agent_id=self._agent_id
        )
        self.assertEqual(detail, "分析预算差异")
        stored = await self._stores.coordinator.list_events(session_id, event_type="session/title")
        self.assertEqual([row.payload["source"] for row in stored], ["fallback", "model"])

    async def test_non_stream_response_waits_for_bounded_title_task(self) -> None:
        body, title_mock = await self._chat(
            message="请分析销售趋势",
            generated_title=TitleGenerationResult("销售趋势分析", False),
        )
        title_mock.assert_awaited_once()
        self.assertEqual(
            await self._stores.coordinator.get_session_title(
                body["session_id"], agent_id=self._agent_id
            ),
            "销售趋势分析",
        )

    async def test_stream_uses_history_as_persisted_conversation_view(self) -> None:
        session_id, events, _title_mock = await self._stream(message="核对历史投影")
        self.assertNotIn(
            "conversation_item_upsert",
            {event.get("type") for event in events},
        )
        async with AsyncClient(
            transport=self._transport, base_url="http://test", timeout=30.0
        ) as client:
            response = await client.get(
                f"/api/v1/sessions/{session_id}/history",
                params={"agent_id": self._agent_id},
            )
        self.assertEqual(response.status_code, 200, response.text)
        history_items = response.json()["items"]
        self.assertTrue(history_items)
        self.assertEqual(history_items[0]["kind"], "user_message")

    async def test_context_only_turn_is_used_by_title_projection(self) -> None:
        context_refs = [
            {
                "key": "selection-1",
                "type": "deeptable.selection",
                "schema_version": "1",
                "label": "华东预算区域",
                "payload": {"id": "region-east", "name": "华东", "count": 8},
                "metadata": {"secret": "must-not-reach-title"},
            }
        ]
        _, _, title_mock = await self._stream(
            message="",
            context_refs=context_refs,
            generated_title=TitleGenerationResult("华东预算分析", False),
        )
        title_mock.assert_awaited_once()
        call = title_mock.await_args.kwargs
        self.assertEqual(call["user_text"], "")
        self.assertEqual(call["context_refs"][0].label, "华东预算区域")

    async def test_request_model_override_is_used_for_title_metadata(self) -> None:
        _, events, title_mock = await self._stream(
            message="使用覆盖模型生成标题",
            model_id=self._override_model_id,
        )
        title_mock.assert_awaited_once()
        title_event = next(
            item["event"]
            for item in events
            if item["type"] == "session_event_appended"
            and item.get("event", {}).get("event_type") == "session/title"
            and item["event"]["payload"].get("source") == "model"
        )
        self.assertEqual(title_event["payload"]["model_id"], self._override_model_id)
        self.assertEqual(title_event["payload"]["model_name"], "test:title-override-model")

    async def test_model_failure_keeps_fallback_and_is_not_retried(self) -> None:
        session_id, _, first_mock = await self._stream(
            message="请解释 Python 装饰器", generated_title=None
        )
        self.assertEqual(
            await self._stores.coordinator.get_session_title(session_id, agent_id=self._agent_id),
            "请解释 Python 装饰器",
        )
        first_mock.assert_awaited_once()

        _, _, second_mock = await self._stream(
            message="继续举例",
            session_id=session_id,
            generated_title=TitleGenerationResult("新标题", False),
        )
        second_mock.assert_not_awaited()
        self.assertEqual(
            await self._stores.coordinator.get_session_title(
                session_id, agent_id=self._agent_id
            ),
            "请解释 Python 装饰器",
        )

    async def test_model_title_cannot_overwrite_later_manual_rename(self) -> None:
        session_id, _, _ = await self._stream(
            message="请分析现金流",
            generated_title=None,
        )
        fallback_event = (
            await self._stores.coordinator.list_events(
                session_id, event_type="session/title"
            )
        )[0]
        manual = await self._stores.coordinator.apply_session_title(
            session_id,
            agent_id=self._agent_id,
            user_id=TEST_USER_ID,
            title="人工现金流标题",
            source="user",
            source_event_id=fallback_event.payload["source_event_id"],
            turn_id=fallback_event.turn_id,
            run_id=fallback_event.run_id,
        )
        self.assertIsNotNone(manual)
        stale_model = await self._stores.coordinator.apply_session_title(
            session_id,
            agent_id=self._agent_id,
            user_id=TEST_USER_ID,
            title="迟到的模型标题",
            source="model",
            source_event_id=fallback_event.payload["source_event_id"],
            turn_id=fallback_event.turn_id,
            run_id=fallback_event.run_id,
            expected_event_id=fallback_event.event_id,
        )
        self.assertIsNone(stale_model)
        self.assertEqual(
            await self._stores.coordinator.get_session_title(
                session_id, agent_id=self._agent_id
            ),
            "人工现金流标题",
        )

    async def test_auxiliary_model_routes_are_removed(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            self.assertEqual((await client.get("/api/v1/auxiliary-models")).status_code, 404)
            self.assertEqual((await client.patch("/api/v1/auxiliary-models/title", json={})).status_code, 404)


if __name__ == "__main__":
    unittest.main()
