from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import FunctionModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import text_only_model


def _context_ref(
    *,
    key: str = "current_selection",
    label: str | None = "销售预算!B2:C4",
) -> dict:
    return {
        "key": key,
        "type": "deeptable.selection",
        "schema_version": "2.0",
        "label": label,
        "payload": {"cells": [{"address": "B2", "value": 42}]},
        "metadata": {"source": "browser", "secret": "history-only"},
    }


class ChatContextReferenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        self._model = await stores.llm_model.create_unique(
            LlmModelCreate(
                name="context-ref-model",
                model="private:context-ref-model",
                input_modalities=["text"],
                context_window_tokens=128_000,
                max_output_tokens=8_192,
            )
        )
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

    async def _history(self, session_id: str) -> dict:
        await get_context().event_persister.flush(session_id)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_context_only_stream_persists_structured_history_and_safe_title(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("已分析当前选区"),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={
                            "model_id": self._model.id,
                            "capabilities": [],
                            "context_refs": [_context_ref()],
                        },
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])
        events = parse_sse_events(raw)
        session_id = events[0]["session_id"]
        final = next(event for event in events if event.get("type") == "final")
        self.assertEqual(final["status"], "completed")

        history = await self._history(session_id)
        user = next(message for message in history["messages"] if message["role"] == "user")
        self.assertIsNone(user["content"])
        self.assertEqual(user["context_refs"], [_context_ref()])
        self.assertNotIn("<context_references>", str(history))

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            sessions = (await client.get("/api/v1/sessions")).json()
        item = next(row for row in sessions["items"] if row["session_id"] == session_id)
        self.assertEqual(item["user_prompt"], "[上下文] 销售预算!B2:C4")

    async def test_normal_chat_accepts_text_attachment_and_context_together(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("ok"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "message": "结合附件解释选区",
                        "model_id": self._model.id,
                        "capabilities": [],
                        "attachments": [
                            {
                                "id": "file-1",
                                "name": "note.txt",
                                "mime": "text/plain",
                            }
                        ],
                        "context_refs": [_context_ref()],
                    },
                )
        self.assertEqual(response.status_code, 200)
        history = await self._history(response.json()["session_id"])
        user = next(message for message in history["messages"] if message["role"] == "user")
        self.assertEqual(user["content"], "结合附件解释选区")
        self.assertEqual(user["attachments"][0]["id"], "file-1")
        self.assertEqual(user["context_refs"][0]["key"], "current_selection")

    async def test_manual_and_auto_rename_preserve_refs_and_never_read_payload(self) -> None:
        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=text_only_model("ok"),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                created = await client.post(
                    "/api/v1/chat",
                    json={
                        "model_id": self._model.id,
                        "capabilities": [],
                        "context_refs": [_context_ref()],
                    },
                )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session_id"]
        before = await self._history(session_id)

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            manual = await client.patch(
                f"/api/v1/sessions/{session_id}",
                json={"title": "手动选区分析"},
            )
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(manual.json()["title"], "手动选区分析")
        self.assertEqual((await self._history(session_id))["messages"], before["messages"])

        title_mock = AsyncMock(return_value="销售预算分析")
        with patch("deep_agent.agent.title_generator.generate_title", title_mock):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                renamed = await client.patch(
                    f"/api/v1/sessions/{session_id}",
                    json={"auto": True},
                )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "销售预算分析")
        title_mock.assert_awaited_once()
        kwargs = title_mock.await_args.kwargs
        self.assertEqual(kwargs["user_text"], "[上下文] 销售预算!B2:C4")
        self.assertNotIn("cells", str(kwargs))
        self.assertNotIn("history-only", str(kwargs))
        self.assertEqual((await self._history(session_id))["messages"], before["messages"])

    async def test_deferred_results_and_context_refs_are_rejected_before_writing(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat/stream",
                json={
                    "session_id": "deferred-context-conflict",
                    "model_id": self._model.id,
                    "context_refs": [_context_ref()],
                    "deferred_tool_results": [
                        {"tool_call_id": "call-1", "result": {"ok": True}}
                    ],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("mutually exclusive", response.text)
        self.assertEqual(
            await get_context().store.get_history("deferred-context-conflict"),
            [],
        )

    async def test_duplicate_keys_are_rejected_by_openapi_schema(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "model_id": self._model.id,
                    "context_refs": [_context_ref(), _context_ref()],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("keys must be unique", response.text)

    async def test_invalid_utf8_json_is_reported_as_422(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                content=b"\xff",
                headers={"content-type": "application/json"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_json_encoding")

    async def test_provider_failure_keeps_the_raw_context_reference(self) -> None:
        async def fail_provider(_messages, _info):
            if False:  # pragma: no cover - keeps this function an async generator
                yield ""
            raise RuntimeError("provider unavailable")

        with patch(
            "deep_agent.agent.factory._resolve_model",
            return_value=FunctionModel(stream_function=fail_provider),
        ):
            with mock_request_disconnect():
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={
                            "session_id": "context-ref-provider-failure",
                            "model_id": self._model.id,
                            "capabilities": [],
                            "message": "分析失败后仍要保留引用",
                            "context_refs": [_context_ref()],
                        },
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        raw = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(raw)
        self.assertEqual(events[-1]["type"], "error")
        history = await self._history("context-ref-provider-failure")
        user = next(message for message in history["messages"] if message["role"] == "user")
        self.assertEqual(user["context_refs"], [_context_ref()])


if __name__ == "__main__":
    unittest.main()
