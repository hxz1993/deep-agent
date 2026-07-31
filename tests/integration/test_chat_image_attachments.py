from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model

_CHAT_JSON = {"capabilities": []}
_IMAGE_ATTACHMENT = {
    "id": "platform-file-1",
    "name": "photo.png",
    "mime": "image/png",
}


class ChatImageAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
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

    async def asyncTearDown(self) -> None:
        self._no_title.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _stream_events(self, *, model: TestModel, json_body: dict) -> list[dict]:
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={**_CHAT_JSON, **json_body},
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        body = "".join([chunk async for chunk in response.aiter_text()])
        return parse_sse_events(body)

    async def _history(self, session_id: str) -> dict:
        await get_context().event_persister.flush(session_id)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    async def _list_sessions(self) -> dict:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/sessions")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_image_attachment_history_and_session_list_survive_after_stream(
        self,
        mock_download: AsyncMock,
    ) -> None:
        mock_download.return_value = b"\x89PNG\r\n\x1a\n"
        row = await get_context().stores.llm_model.create_unique(
            LlmModelCreate(
                name="vision-history",
                model="private:vision-history",
                input_modalities=["text", "image"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
        )
        reply = "vision reply"
        events = await self._stream_events(
            model=text_only_model(reply),
            json_body={
                "message": "这张图是什么",
                "attachments": [_IMAGE_ATTACHMENT],
                "model_id": row.id,
            },
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        session_id = events[0]["session_id"]

        history = await self._history(session_id)
        user_messages = [m for m in history["messages"] if m.get("role") == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(user_messages[0]["content"], "这张图是什么")
        self.assertEqual(len(user_messages[0]["attachments"]), 1)
        self.assertEqual(user_messages[0]["attachments"][0]["id"], "platform-file-1")

        sessions = await self._list_sessions()
        self.assertEqual(sessions["total"], 1)
        self.assertEqual(sessions["items"][0]["session_id"], session_id)
        self.assertIn("这张图是什么", sessions["items"][0]["user_prompt"] or "")

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_non_vision_model_image_chat_returns_clear_error(
        self,
        mock_download: AsyncMock,
    ) -> None:
        row = await get_context().stores.llm_model.create_unique(
            LlmModelCreate(
                name="text-only-error",
                model="private:text-only-error",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
        )
        mock_download.return_value = b"\x89PNG\r\n\x1a\n"
        events = await self._stream_events(
            model=text_only_model("cannot see images"),
            json_body={
                "message": "这张图是什么",
                "attachments": [_IMAGE_ATTACHMENT],
                "model_id": row.id,
            },
        )
        error = next(ev for ev in events if ev.get("type") == "error")
        self.assertIn("不支持图片输入", error["message"])
        mock_download.assert_not_awaited()

        session_id = events[0]["session_id"]
        history = await self._history(session_id)
        self.assertEqual(
            len([m for m in history["messages"] if m.get("role") == "user"]),
            1,
        )

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_db_text_only_override_skips_image_download(
        self,
        mock_download: AsyncMock,
    ) -> None:
        """DB user override text-only should win over name heuristics (e.g. kimi-k2.6)."""
        stores = get_context().stores
        row = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="kimi-text-only",
                model="kimi-k2.6",
                base_url="https://api.moonshot.cn",
                input_modalities=["text"],
                context_window_tokens=262_144,
                max_output_tokens=8_192,
            ),
        )
        events = await self._stream_events(
            model=text_only_model("text only"),
            json_body={
                "message": "这张图是什么",
                "attachments": [_IMAGE_ATTACHMENT],
                "model_id": row.id,
            },
        )
        error = next(ev for ev in events if ev.get("type") == "error")
        self.assertIn("不支持图片输入", error["message"])
        mock_download.assert_not_awaited()

    @patch("deep_agent.agent.attachments._download_file", new_callable=AsyncMock)
    async def test_db_vision_modalities_enable_image_download(
        self,
        mock_download: AsyncMock,
    ) -> None:
        """DB modalities with image should download bytes for image attachments."""
        mock_download.return_value = b"\x89PNG\r\n\x1a\n"
        stores = get_context().stores
        row = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="vision-db",
                model="private:vision-model",
                input_modalities=["text", "image"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            ),
        )
        await self._stream_events(
            model=text_only_model("vision reply"),
            json_body={
                "message": "描述图片",
                "attachments": [_IMAGE_ATTACHMENT],
                "model_id": row.id,
            },
        )
        mock_download.assert_awaited_once()
