from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.schemas.ui_tools import UiToolCreate
from tests.integration.helpers.chat import aclose_test_context, configure_test_app_async, mock_request_disconnect, parse_sse_events, testing_data_stores_async


async def _seed_manual_ui_tool(stores, *, name: str, metadata: dict) -> None:
    await stores.ui_tool.create_manual(
        UiToolCreate(
            name=name,
            description="Collect user input via client UI.",
            parameters_json_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
            widget="text",
            result_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            metadata=metadata,
            enabled=True,
        )
    )


@asynccontextmanager
async def ui_tool_stream_app(
    *,
    tool_name: str,
    metadata: dict,
) -> AsyncIterator[tuple[ASGITransport, TestModel]]:
    """AsyncClient transport for /chat/stream; TestClient.stream blocks indefinitely."""
    stores, engine, session_factory = await testing_data_stores_async(mcp_rows=[])
    await _seed_manual_ui_tool(stores, name=tool_name, metadata=metadata)

    app = FastAPI()
    app.include_router(api_router)
    await configure_test_app_async(
        app,
        mcp_url="http://unused",
        stores=stores,
        db_engine=engine,
        db_session_factory=session_factory,
    )

    test_model = TestModel(call_tools=[tool_name], custom_output_text="done")
    transport = ASGITransport(app=app)
    try:
        yield transport, test_model
    finally:
        await aclose_test_context(engine=engine)


class ChatUiToolMetadataStreamTests(unittest.IsolatedAsyncioTestCase):
    """POST /api/v1/chat/stream → SSE tool_call / final.deferred carry UI tool metadata."""

    async def test_chat_stream_sse_includes_metadata_on_tool_call_and_deferred(self) -> None:
        tool_name = "myapp__collect_note"
        metadata = {"form_version": "1", "icon": "calendar"}

        async with ui_tool_stream_app(tool_name=tool_name, metadata=metadata) as (transport, test_model):
            with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
                with mock_request_disconnect():
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                        timeout=30.0,
                    ) as client:
                        async with client.stream(
                            "POST",
                            "/api/v1/chat/stream",
                            json={"message": "请用户填写备注"},
                        ) as response:
                            self.assertEqual(response.status_code, 200)
                            body = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(body)
        tool_calls = [ev for ev in events if ev.get("type") == "tool_call"]
        self.assertTrue(tool_calls, f"expected tool_call events, got: {events!r}")

        last_call = tool_calls[-1]
        self.assertEqual(last_call["tool"], tool_name)
        self.assertEqual(last_call["widget"], "text")
        self.assertEqual(last_call["metadata"], metadata)
        self.assertEqual(last_call["source"], "manual")

        final = next((ev for ev in reversed(events) if ev.get("type") == "final"), None)
        self.assertIsNotNone(final, f"missing final in: {events!r}")
        assert final is not None
        self.assertEqual(final["status"], "pending")
        deferred_call = final["deferred"]["calls"][0]
        self.assertEqual(deferred_call["metadata"], metadata)
        self.assertEqual(deferred_call["widget"], "text")


if __name__ == "__main__":
    unittest.main()
