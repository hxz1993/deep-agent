from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.core.context import reset_context
from deep_agent.schemas.ui_tools import UiToolCreate
from tests.integration.helpers.chat import configure_test_app, mock_request_disconnect, testing_data_stores


def _parse_sse_events(raw: str) -> list[dict]:
    events: list[dict] = []
    event_type: str | None = None
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:") and event_type is not None:
            payload = json.loads(line.removeprefix("data:").strip())
            events.append({"type": event_type, **payload})
            event_type = None
    return events


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


@contextmanager
def ui_tool_stream_client(
    *,
    tool_name: str,
    metadata: dict,
) -> Iterator[TestClient]:
    """HTTP chat/stream with sqlite UI tool row; only LLM (_resolve_model) is mocked."""
    import asyncio

    stores, engine, session_factory = testing_data_stores(mcp_rows=[])
    asyncio.run(_seed_manual_ui_tool(stores, name=tool_name, metadata=metadata))

    app = FastAPI()
    app.include_router(api_router)
    configure_test_app(
        app,
        mcp_url="http://unused",
        stores=stores,
        db_engine=engine,
        db_session_factory=session_factory,
    )

    test_model = TestModel(call_tools=[tool_name], custom_output_text="done")
    try:
        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            with mock_request_disconnect():
                with TestClient(app) as client:
                    yield client
    finally:
        reset_context()


class ChatUiToolMetadataStreamTests(unittest.TestCase):
    """POST /api/v1/chat/stream → SSE tool_call / final.deferred carry UI tool metadata."""

    def test_chat_stream_sse_includes_metadata_on_tool_call_and_deferred(self) -> None:
        tool_name = "myapp__collect_note"
        metadata = {"form_version": "1", "icon": "calendar"}

        with ui_tool_stream_client(tool_name=tool_name, metadata=metadata) as client:
            with client.stream(
                "POST",
                "/api/v1/chat/stream",
                json={"message": "请用户填写备注"},
            ) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())

        events = _parse_sse_events(body)
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
