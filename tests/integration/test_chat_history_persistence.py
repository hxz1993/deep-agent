from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.integration.helpers.models import build_slow_stream_model, text_only_model, tool_call_model

_CHAT_JSON = {"capabilities": []}


def _assistant_texts(history: dict) -> list[str]:
    texts: list[str] = []
    for msg in history.get("messages") or []:
        if msg.get("role") != "assistant":
            continue
        for part in msg.get("parts") or []:
            if part.get("type") == "text" and part.get("content"):
                texts.append(part["content"])
    return texts


def _streamed_text(events: list[dict]) -> str:
    return "".join(ev.get("delta") or "" for ev in events if ev.get("type") == "text")


def _assistant_messages(history: dict) -> list[dict]:
    return [m for m in history.get("messages") or [] if m.get("role") == "assistant"]


def _tool_call_ids(history: dict) -> list[str]:
    ids: list[str] = []
    for msg in _assistant_messages(history):
        for part in msg.get("parts") or []:
            if part.get("type") == "tool_call" and part.get("tool_call_id"):
                ids.append(part["tool_call_id"])
    return ids


class ChatHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self._engine, session_factory = await make_sqlite_stores()
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
        self._transport = ASGITransport(app=self._app)

    async def asyncTearDown(self) -> None:
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
        self.assertEqual(final["model_message_count"], data["model_message_count"])
        self.assertGreaterEqual(data["model_message_count"], 2)
        self.assertEqual(_assistant_texts(data), [reply])

    async def test_http_sync_chat_persists_assistant(self) -> None:
        reply = "sync path reply"
        with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model(reply)):
            with mock_request_disconnect():
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/chat",
                        json={**_CHAT_JSON, "message": "sync hello"},
                    )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"], reply)

        data = await self._history(body["session_id"])
        self.assertGreaterEqual(data["model_message_count"], 2)
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
        self.assertEqual(data["model_message_count"], 4)
        self.assertEqual(_assistant_texts(data), expected)
        users = [m["content"] for m in data["messages"] if m.get("role") == "user"]
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
                        json={**_CHAT_JSON, "message": "stream please"},
                    ) as response:
                        body = "".join([chunk async for chunk in response.aiter_text()])

        events = parse_sse_events(body)
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(_streamed_text(events), expected)

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(data["model_message_count"], 2)
        self.assertEqual(_assistant_texts(data), [expected])

    async def test_text_only_model_skips_auto_tool_calls(self) -> None:
        events = await self._stream_events(
            model=text_only_model("plain text"),
            json_body={"message": "no tools please"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertNotIn("tool_call", {ev.get("type") for ev in events})

    async def test_test_model_default_calls_mounted_tools(self) -> None:
        """TestModel defaults to call_tools='all'; with UI tools mounted that yields pending HITL."""
        events = await self._stream_events(
            model=TestModel(custom_output_text="never returned as final"),
            json_body={"message": "triggers tools"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        self.assertIsNone(final.get("output"))
        self.assertTrue(final.get("deferred", {}).get("calls"))

    async def test_tool_call_model_persists_deferred_tool_call_in_history(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("confirm_action", text="after confirm"),
            json_body={"message": "need confirm"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")

        session_id = events[0]["session_id"]
        data = await self._history(session_id)
        self.assertGreaterEqual(data["model_message_count"], 2)
        assistants = [m for m in data["messages"] if m.get("role") == "assistant"]
        self.assertEqual(len(assistants), 1)
        tool_calls = [p for p in assistants[0]["parts"] if p.get("type") == "tool_call"]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["tool"], "confirm_action")

    async def test_hitl_stream_then_history_exactly_one_assistant(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("confirm_action", text="done after confirm"),
            json_body={"message": "请确认"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertEqual(final["model_message_count"], data["model_message_count"])
        self.assertEqual(data["model_message_count"], 2)
        assistants = _assistant_messages(data)
        self.assertEqual(len(assistants), 1)
        self.assertEqual(len(_tool_call_ids(data)), 1)

    async def test_hitl_stream_history_resume_deferred_completes(self) -> None:
        events = await self._stream_events(
            model=tool_call_model("confirm_action", text="time is noon"),
            json_body={"message": "现在几点"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        session_id = events[0]["session_id"]
        tool_call_id = final["deferred"]["calls"][0]["tool_call_id"]

        pending_history = await self._history(session_id)
        self.assertEqual(len(_assistant_messages(pending_history)), 1)

        resume_events = await self._stream_events(
            model=tool_call_model("confirm_action", text="time is noon"),
            json_body={
                "session_id": session_id,
                "deferred_tool_results": [
                    {"tool_call_id": tool_call_id, "result": {"confirmed": True}},
                ],
            },
        )
        resume_final = next(ev for ev in reversed(resume_events) if ev.get("type") == "final")
        self.assertEqual(resume_final["status"], "completed")
        self.assertNotIn("error", {ev.get("type") for ev in resume_events})

        done_history = await self._history(session_id)
        self.assertGreaterEqual(done_history["model_message_count"], 3)
        self.assertGreaterEqual(len(_assistant_messages(done_history)), 2)

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
        self.assertEqual(data["model_message_count"], 2)
        self.assertEqual(len(_assistant_messages(data)), 1)
        self.assertEqual(_assistant_texts(data), ["single reply"])
        self.assertEqual(final["model_message_count"], data["model_message_count"])


if __name__ == "__main__":
    unittest.main()
