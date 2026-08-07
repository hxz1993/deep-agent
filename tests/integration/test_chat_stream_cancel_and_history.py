from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.api.v1 import api_router
from deep_agent.api.v1.deps import build_runner
from deep_agent.core.context import get_context
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, init_context, reset_context
from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.helpers.sqlite_stores import ensure_title_uses_main_model, make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_buffer, parse_sse_events
from tests.integration.helpers.models import build_slow_stream_model, text_only_model, tool_call_model

_CHAT_JSON = {"capabilities": []}
_CONFIRM_CAPABILITIES = ["ui:builtin"]


def _last_assistant(history: dict) -> dict | None:
    for msg in reversed(history.get("messages") or []):
        if msg.get("role") == "assistant":
            return msg
    return None


def _tool_call_parts(msg: dict) -> list[dict]:
    return [p for p in msg.get("parts") or [] if p.get("type") == "tool_call"]


def _tool_call_ids(history: dict) -> list[str]:
    ids: list[str] = []
    for msg in history.get("messages") or []:
        if msg.get("role") != "assistant":
            continue
        for part in msg.get("parts") or []:
            if part.get("type") == "tool_call" and part.get("tool_call_id"):
                ids.append(part["tool_call_id"])
    return ids


def _tool_result_ids(history: dict) -> list[str]:
    ids: list[str] = []
    for msg in history.get("messages") or []:
        if msg.get("role") != "assistant":
            continue
        for part in msg.get("parts") or []:
            if part.get("type") == "tool_result" and part.get("tool_call_id"):
                ids.append(part["tool_call_id"])
    return ids


class ChatStreamCancelAndHistoryTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self) -> None:
        self._no_title.stop()
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def _stream_events(
        self,
        *,
        model: TestModel,
        json_body: dict,
    ) -> list[dict]:
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

    async def _run_hitl_pending(self) -> tuple[str, str]:
        test_model = tool_call_model("builtin__confirm", text="done after confirm")
        events = await self._stream_events(
            model=test_model,
            json_body={
                "message": "请用户确认",
                "capabilities": _CONFIRM_CAPABILITIES,
            },
        )
        session_id = events[0]["session_id"]
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        tool_call_id = final["deferred"]["calls"][0]["tool_call_id"]
        return session_id, tool_call_id

    async def _history(self, session_id: str) -> dict:
        await get_context().event_persister.flush(session_id)
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    async def test_e0_http_stream_complete_persists_assistant(self) -> None:
        test_model = text_only_model("hello from integration")
        events = await self._stream_events(
            model=test_model,
            json_body={"message": "say hello"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        session_id = events[0]["session_id"]

        data = await self._history(session_id)
        self.assertGreaterEqual(data["model_message_count"], 2)
        assistant = _last_assistant(data)
        self.assertIsNotNone(assistant)
        assert assistant is not None
        text_parts = [p for p in assistant["parts"] if p.get("type") == "text"]
        self.assertEqual(text_parts[0]["content"], "hello from integration")

    async def test_e1_http_abort_persists_partial_assistant_history(self) -> None:
        """Client closes SSE; history must retain streamed assistant text."""
        chunks = ["chunk-one-", "chunk-two-", "chunk-three-", "chunk-four-", "chunk-five-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.25)

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                session_id: str | None = None
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={**_CHAT_JSON, "message": "slow stream please"},
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            events, buffer = parse_sse_buffer(buffer)
                            for ev in events:
                                if ev.get("type") == "session":
                                    session_id = ev["session_id"]
                                if ev.get("type") == "text" and ev.get("delta"):
                                    await response.aclose()
                                    break
                            else:
                                continue
                            break

                    await get_context().event_persister.flush(session_id)
                    data = await self._history(session_id)

                self.assertIsNotNone(session_id)
                self.assertGreater(tracker.yielded_chunks, 0)
                self.assertGreaterEqual(
                    data["model_message_count"],
                    2,
                    msg="history must include assistant after stream stop (not user-only)",
                )
                assistant = _last_assistant(data)
                self.assertIsNotNone(assistant)
                assert assistant is not None
                text_parts = [p for p in assistant["parts"] if p.get("type") == "text"]
                self.assertTrue(text_parts)
                self.assertIn("chunk-one", text_parts[0]["content"])

    async def test_e1_runner_cancel_persists_partial_history(self) -> None:
        chunks = ["chunk-one-", "chunk-two-", "chunk-three-", "chunk-four-", "chunk-five-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.25)
        session_id = "e1-cancel-session"

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            runner = await build_runner(get_context(), capabilities=[])
            deps = AgentDeps(session_id=session_id, user=TEST_USER_ID)
            events: list[dict] = []
            agen = runner.run_stream(
                session_id,
                "slow stream please",
                deps,
                persister=get_context().event_persister,
            )
            async for ev in agen:
                events.append(ev)
                if ev.get("type") == "text" and ev.get("delta"):
                    await agen.aclose()
                    break

            await get_context().event_persister.flush(session_id)

            self.assertGreater(tracker.yielded_chunks, 0)
            self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
            if events and events[-1].get("type") == "cancelled":
                self.assertNotIn("final", {ev.get("type") for ev in events})

            data = await self._history(session_id)
            self.assertGreaterEqual(data["model_message_count"], 2)
            assistant = _last_assistant(data)
            self.assertIsNotNone(assistant)
            assert assistant is not None
            text_parts = [p for p in assistant["parts"] if p.get("type") == "text"]
            self.assertTrue(text_parts)
            self.assertIn("chunk-one", text_parts[0]["content"])

    async def test_e1_cancel_endpoint_uses_official_token_and_is_idempotent(self) -> None:
        chunks = ["first-", "second-", "third-", "fourth-", "fifth-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.25)
        session_id = "e1-official-cancel-session"

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            runner = await build_runner(get_context(), capabilities=[])
            deps = AgentDeps(session_id=session_id, user=TEST_USER_ID)
            streamed: list[dict] = []

            async def consume() -> None:
                async for event in runner.run_stream(
                    session_id,
                    "generate slowly",
                    deps,
                    persister=get_context().event_persister,
                ):
                    streamed.append(event)

            task = asyncio.create_task(consume())
            for _ in range(100):
                if get_context().active_runs.get(agent_id=None, session_id=session_id):
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(
                get_context().active_runs.get(agent_id=None, session_id=session_id)
            )
            for _ in range(100):
                if any(event.get("type") == "text" for event in streamed):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(any(event.get("type") == "text" for event in streamed))

            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(f"/api/v1/sessions/{session_id}/cancel")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "cancel_requested")

                await asyncio.wait_for(task, timeout=5)
                await get_context().event_persister.flush(session_id)
                repeated = await client.post(f"/api/v1/sessions/{session_id}/cancel")
                self.assertEqual(repeated.status_code, 200)
                self.assertEqual(repeated.json()["status"], "not_running")

            self.assertIn("cancelled", {event.get("type") for event in streamed})
            self.assertNotIn("final", {event.get("type") for event in streamed})
            self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
            self.assertIsNone(
                get_context().active_runs.get(agent_id=None, session_id=session_id)
            )
            history = await self._history(session_id)
            assistant = _last_assistant(history)
            self.assertIsNotNone(assistant)

    async def test_cancel_endpoint_rejects_malformed_agent_id(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/sessions/missing/cancel",
                params={"agent_id": "not-a-uuid"},
            )
        self.assertEqual(response.status_code, 422)

    async def test_e2_deferred_tool_call_persists_in_history_and_resumes(self) -> None:
        session_id, tool_call_id = await self._run_hitl_pending()

        data = await self._history(session_id)
        assistant = _last_assistant(data)
        self.assertIsNotNone(assistant)
        assert assistant is not None
        calls = _tool_call_parts(assistant)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "builtin__confirm")
        self.assertEqual(calls[0]["tool_call_id"], tool_call_id)

        test_model = tool_call_model("builtin__confirm", text="resumed ok")
        events = await self._stream_events(
            model=test_model,
            json_body={
                "session_id": session_id,
                "capabilities": _CONFIRM_CAPABILITIES,
                "deferred_tool_results": [
                    {
                        "tool_call_id": tool_call_id,
                        "result": {"confirmed": True},
                    }
                ],
            },
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertTrue(any(ev.get("type") == "tool_result" for ev in events))

    async def test_e3_new_message_after_deferred_without_confirm(self) -> None:
        session_id, _ = await self._run_hitl_pending()

        test_model = text_only_model("continued without confirm")
        events = await self._stream_events(
            model=test_model,
            json_body={"session_id": session_id, "message": "跳过确认继续"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertNotIn("error", {ev.get("type") for ev in events})

        data = await self._history(session_id)
        messages = data["messages"]
        assistants = [m for m in messages if m.get("role") == "assistant"]
        self.assertGreaterEqual(len(assistants), 2)

    async def test_e4_immediate_followup_after_disconnect(self) -> None:
        chunks = ["only-", "second-", "third-"]
        model, _tracker = build_slow_stream_model(chunks=chunks, delay_s=0.05)

        session_id: str | None = None
        body = ""
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
                        json={**_CHAT_JSON, "message": "first"},
                    ) as response:
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            events, buffer = parse_sse_buffer(buffer)
                            for ev in events:
                                if ev.get("type") == "session":
                                    session_id = ev["session_id"]
                                if ev.get("type") == "text":
                                    await response.aclose()
                                    break
                            else:
                                continue
                            break

                    await asyncio.sleep(0.4)
                    follow = text_only_model("second turn")
                    with patch("deep_agent.agent.factory._resolve_model", return_value=follow):
                        with mock_request_disconnect():
                            async with client.stream(
                                "POST",
                                "/api/v1/chat/stream",
                                json={
                                    **_CHAT_JSON,
                                    "session_id": session_id,
                                    "message": "second",
                                },
                            ) as response2:
                                body = "".join([c async for c in response2.aiter_text()])
        events = parse_sse_events(body)
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")

    async def test_e5_cancelled_meta_matches_history(self) -> None:
        chunks = ["meta-", "more-", "third-", "fourth-", "fifth-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.25)

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                session_id: str | None = None
                events: list[dict] = []
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={**_CHAT_JSON, "message": "meta test"},
                    ) as response:
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            parsed, buffer = parse_sse_buffer(buffer)
                            for ev in parsed:
                                events.append(ev)
                                if ev.get("type") == "session":
                                    session_id = ev["session_id"]
                                if ev.get("type") == "text" and ev.get("delta"):
                                    await response.aclose()
                                    break
                            else:
                                continue
                            break

                    await get_context().event_persister.flush(session_id)
                    cancelled = next((ev for ev in events if ev.get("type") == "cancelled"), None)
                    data = await self._history(session_id)
                    if cancelled is not None:
                        self.assertEqual(cancelled["model_message_count"], data["model_message_count"])
                        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
                    else:
                        self.assertGreaterEqual(tracker.yielded_chunks, 1)

    async def test_e6_branch_point_on_cancel(self) -> None:
        chunks = ["branch-"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.05)

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect():
                events: list[dict] = []
                async with AsyncClient(
                    transport=self._transport,
                    base_url="http://test",
                    timeout=30.0,
                ) as client:
                    async with client.stream(
                        "POST",
                        "/api/v1/chat/stream",
                        json={**_CHAT_JSON, "message": "branch"},
                    ) as response:
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            parsed, buffer = parse_sse_buffer(buffer)
                            events.extend(parsed)
                            if any(ev.get("type") == "text" for ev in parsed):
                                await response.aclose()
                                break

                cancelled = next((ev for ev in events if ev.get("type") == "cancelled"), None)
                if cancelled is not None:
                    self.assertEqual(cancelled.get("user_branch_point"), 0)

    async def test_e7_old_deferred_invalid_after_new_message(self) -> None:
        # repair injects ToolDenied in-memory; DB still shows pending tool_call for display.
        # Submitting deferred_tool_results for the abandoned call must not succeed.
        session_id, tool_call_id = await self._run_hitl_pending()

        test_model = text_only_model("skipped")
        await self._stream_events(
            model=test_model,
            json_body={"session_id": session_id, "message": "放弃旧确认"},
        )

        resume_model = text_only_model("should not run")
        events = await self._stream_events(
            model=resume_model,
            json_body={
                "session_id": session_id,
                "deferred_tool_results": [
                    {
                        "tool_call_id": tool_call_id,
                        "result": {"confirmed": True},
                    }
                ],
            },
        )
        types = {ev.get("type") for ev in events}
        final = next((ev for ev in reversed(events) if ev.get("type") == "final"), None)
        if final is not None:
            self.assertNotEqual(final.get("status"), "completed")
        else:
            self.assertIn("error", types)

    async def test_e9_deferred_resume_cancel_no_duplicate_tool_call(self) -> None:
        """HITL confirm then cancel resume stream must not duplicate tool_call in DB."""
        session_id, tool_call_id = await self._run_hitl_pending()
        resume_model = text_only_model("continued after confirm")

        with patch("deep_agent.agent.factory._resolve_model", return_value=resume_model):
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
                            "session_id": session_id,
                            "deferred_tool_results": [
                                {
                                    "tool_call_id": tool_call_id,
                                    "result": {"confirmed": True},
                                }
                            ],
                        },
                    ) as response:
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            parsed, buffer = parse_sse_buffer(buffer)
                            for ev in parsed:
                                if ev.get("type") in ("tool_call", "tool_result", "text"):
                                    await response.aclose()
                                    break
                            else:
                                continue
                            break

                    await get_context().event_persister.flush(session_id)
                    data = await self._history(session_id)

        call_ids = _tool_call_ids(data)
        result_ids = _tool_result_ids(data)
        self.assertEqual(call_ids, [tool_call_id])
        self.assertEqual(len(call_ids), len(set(call_ids)))
        self.assertIn(tool_call_id, result_ids)

        follow = await self._stream_events(
            model=text_only_model("third turn ok"),
            json_body={"session_id": session_id, "message": "继续聊"},
        )
        self.assertNotIn("error", {ev.get("type") for ev in follow})
        follow_final = next(ev for ev in reversed(follow) if ev.get("type") == "final")
        self.assertEqual(follow_final["status"], "completed")

    async def test_e8_sync_chat_after_deferred_without_confirm(self) -> None:
        session_id, _ = await self._run_hitl_pending()
        test_model = text_only_model("sync ok")
        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            with mock_request_disconnect():
                async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/chat",
                        json={
                            **_CHAT_JSON,
                            "session_id": session_id,
                            "message": "sync skip",
                        },
                    )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"], "sync ok")


if __name__ == "__main__":
    unittest.main()
