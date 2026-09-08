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
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import (
    make_sqlite_stores,
    seed_default_agent,
)
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.helpers.models import build_slow_stream_model, text_only_model, tool_call_model

_CHAT_JSON = {"capabilities": []}
_CONFIRM_CAPABILITIES = ["ui:builtin"]


def _assistant_texts(history: dict) -> list[str]:
    return [
        item["text"]
        for item in history.get("items") or []
        if item.get("kind") == "assistant_message" and item.get("text")
    ]


def _streamed_text(events: list[dict]) -> str:
    return "".join(ev.get("delta") or "" for ev in events if ev.get("type") == "text")


def _assistant_messages(history: dict) -> list[dict]:
    return [
        item
        for item in history.get("items") or []
        if item.get("kind") == "assistant_message"
    ]


def _message_items(history: dict) -> list[dict]:
    return [
        item
        for item in history.get("items") or []
        if item.get("kind") in {"user_message", "assistant_message"}
    ]


def _tool_call_ids(history: dict) -> list[str]:
    return [
        item["call_id"]
        for item in history.get("items") or []
        if item.get("kind") in {"tool", "plan"} and item.get("call_id")
    ]


class ChatHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
        self._agent_id = await seed_default_agent(stores, name="history-agent")
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
            "deep_agent.agent.runner.AgentRunner._generate_model_title",
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
                        json={**_CHAT_JSON, "agent_id": self._agent_id, **json_body},
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

    async def test_http_stream_complete_history_matches_final_output(self) -> None:
        reply = "exact integration reply"
        events = await self._stream_events(
            model=text_only_model(reply),
            json_body={"message": "hello"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"], reply)

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [reply])

    async def test_http_sync_chat_persists_assistant(self) -> None:
        reply = "sync path reply"
        with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model(reply)):
            with mock_request_disconnect():
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/chat",
                        json={
                            **_CHAT_JSON,
                            "agent_id": self._agent_id,
                            "message": "sync hello",
                        },
                    )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"], reply)

        data = await self._history(body["session_id"])
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [reply])

    async def test_http_stream_multi_turn_retains_all_assistant_messages(self) -> None:
        session_id: str | None = None
        expected = ["first-turn-reply", "second-turn-reply"]
        for idx, reply in enumerate(expected):
            json_body: dict = {"message": f"turn-{idx + 1}"}
            if session_id is not None:
                json_body["session_id"] = session_id
            events = await self._stream_events(
                model=text_only_model(reply),
                json_body=json_body,
            )
            final = next(ev for ev in reversed(events) if ev.get("type") == "final")
            self.assertEqual(final["status"], "completed")
            session_id = events[0]["session_id"]

        assert session_id is not None
        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 4)
        self.assertEqual(_assistant_texts(data), expected)
        users = [
            item["text"]
            for item in data["items"]
            if item.get("kind") == "user_message"
        ]
        self.assertEqual(users, ["turn-1", "turn-2"])

    async def test_http_stream_deltas_match_persisted_history_text(self) -> None:
        chunks = ["alpha-", "beta-", "gamma-"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.03)
        expected = "".join(chunks)

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
                        json={
                            **_CHAT_JSON,
                            "agent_id": self._agent_id,
                            "message": "stream please",
                        },
                    ) as response:
                        body = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(body)
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(_streamed_text(events), expected)

        self.assertNotIn(
            "conversation_item_upsert",
            {event.get("type") for event in events},
        )

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(data)), 2)
        self.assertEqual(_assistant_texts(data), [expected])

    async def test_text_only_model_skips_auto_tool_calls(self) -> None:
        events = await self._stream_events(
            model=text_only_model("plain text"),
            json_body={"message": "no tools please"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertNotIn("tool_call", {ev.get("type") for ev in events})

    async def test_test_model_default_has_no_ui_tools_without_selection(self) -> None:
        events = await self._stream_events(
            model=TestModel(call_tools=[], custom_output_text="plain final"),
            json_body={"message": "no UI tools selected"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["output"], "plain final")
        called_tools = {
            event.get("tool")
            for event in events
            if event.get("type") == "tool_call"
        }
        self.assertTrue(called_tools.isdisjoint({"builtin__choice", "builtin__confirm"}))

    async def test_test_model_calls_explicitly_selected_builtin_ui_tools(self) -> None:
        events = await self._stream_events(
            model=TestModel(
                call_tools=["builtin__choice", "builtin__confirm"],
                custom_output_text="never returned as final",
            ),
            json_body={
                "message": "triggers selected UI tools",
                "capabilities": _CONFIRM_CAPABILITIES,
            },
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        self.assertEqual(
            [call["tool"] for call in final["deferred"]["calls"]],
            ["builtin__choice", "builtin__confirm"],
        )

    async def test_tool_call_model_persists_deferred_tool_call_in_history(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="after confirm"),
            json_body={"message": "need confirm", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 1)
        assistants = _assistant_messages(data)
        self.assertEqual(len(assistants), 0)
        tool_calls = [item for item in data["items"] if item.get("kind") == "tool"]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["tool_name"], "builtin__confirm")
        self.assertEqual(tool_calls[0]["status"], "awaiting_input")

    async def test_hitl_stream_then_history_exactly_one_assistant(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="done after confirm"),
            json_body={"message": "请确认", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 1)
        assistants = _assistant_messages(data)
        self.assertEqual(len(assistants), 0)
        self.assertEqual(len(_tool_call_ids(data)), 1)

    async def test_hitl_stream_history_resume_deferred_completes(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="time is noon"),
            json_body={"message": "现在几点", "capabilities": _CONFIRM_CAPABILITIES},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]
        tool_call_id = final["deferred"]["calls"][0]["tool_call_id"]

        pending_history = await self._history(session_id)
        self.assertEqual(len(_assistant_messages(pending_history)), 0)
        pending_tool = next(item for item in pending_history["items"] if item["kind"] == "tool")
        self.assertEqual(pending_tool["status"], "awaiting_input")

        resume_events = await self._stream_events(
            model=tool_call_model("builtin__confirm", text="time is noon"),
            json_body={
                "session_id": session_id,
                "capabilities": _CONFIRM_CAPABILITIES,
                "deferred_tool_results": [
                    {"tool_call_id": tool_call_id, "result": {"confirmed": True}},
                ],
            },
        )
        resume_final = next(ev for ev in reversed(resume_events) if ev.get("type") == "final")
        self.assertEqual(resume_final["status"], "completed")
        self.assertNotIn("error", {ev.get("type") for ev in resume_events})

        done_history = await self._history(session_id)
        self.assertGreaterEqual(len(_message_items(done_history)), 2)
        self.assertGreaterEqual(len(_assistant_messages(done_history)), 1)
        completed_tool = next(item for item in done_history["items"] if item["kind"] == "tool")
        self.assertEqual(completed_tool["status"], "completed")

    async def test_stream_complete_raw_history_not_duplicated(self) -> None:
        """user_message -> stream done -> GET history must not duplicate assistant turns."""
        events = await self._stream_events(
            model=text_only_model("single reply"),
            json_body={"message": "hello"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertEqual(len(_message_items(data)), 2)
        self.assertEqual(len(_assistant_messages(data)), 1)
        self.assertEqual(_assistant_texts(data), ["single reply"])


if __name__ == "__main__":
    unittest.main()
