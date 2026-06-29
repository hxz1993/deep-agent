from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest.mock import patch

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
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import DisconnectFlag, mock_request_disconnect, parse_sse_buffer, parse_sse_events
from tests.integration.helpers.models import build_slow_stream_model

_CHAT_JSON = {"capabilities": []}


def _last_assistant(history: dict) -> dict | None:
    for msg in reversed(history.get("messages") or []):
        if msg.get("role") == "assistant":
            return msg
    return None


def _tool_call_parts(msg: dict) -> list[dict]:
    return [p for p in msg.get("parts") or [] if p.get("type") == "tool_call"]


class ChatStreamCancelAndHistoryTests(unittest.IsolatedAsyncioTestCase):
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
        test_model = TestModel(call_tools=["confirm_action"], custom_output_text="done after confirm")
        events = await self._stream_events(
            model=test_model,
            json_body={"message": "请用户确认"},
        )
        session_id = events[0]["session_id"]
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "pending")
        tool_call_id = final["deferred"]["calls"][0]["tool_call_id"]
        return session_id, tool_call_id

    async def test_e1_disconnect_stops_early_and_partial_history(self) -> None:
        chunks = ["chunk-one-", "chunk-two-", "chunk-three-", "chunk-four-", "chunk-five-"]
        model, tracker = build_slow_stream_model(chunks=chunks, delay_s=0.25)
        session_id = "e1-cancel-session"

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            runner = await build_runner(get_context(), capabilities=[])
            disconnect_flag = DisconnectFlag()
            deps = AgentDeps(session_id=session_id)
            events: list[dict] = []
            async for ev in runner.run_stream(
                session_id,
                "slow stream please",
                deps,
                should_cancel=disconnect_flag,
            ):
                events.append(ev)
                if ev.get("type") == "text" and ev.get("delta"):
                    disconnect_flag.disconnected = True

            self.assertGreater(tracker.yielded_chunks, 0)
            self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
            self.assertEqual(events[-1]["type"], "cancelled")
            self.assertNotIn("final", {ev.get("type") for ev in events})

            async with AsyncClient(
                transport=self._transport,
                base_url="http://test",
                timeout=30.0,
            ) as client:
                hist = await client.get(f"/api/v1/sessions/{session_id}/history")
            self.assertEqual(hist.status_code, 200)
            data = hist.json()
            self.assertGreaterEqual(data["model_message_count"], 2)
            assistant = _last_assistant(data)
            self.assertIsNotNone(assistant)
            assert assistant is not None
            text_parts = [p for p in assistant["parts"] if p.get("type") == "text"]
            self.assertTrue(text_parts)
            self.assertIn("chunk-one", text_parts[0]["content"])

    async def test_e2_deferred_tool_call_persists_in_history_and_resumes(self) -> None:
        session_id, tool_call_id = await self._run_hitl_pending()

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            hist = await client.get(f"/api/v1/sessions/{session_id}/history")
        data = hist.json()
        assistant = _last_assistant(data)
        self.assertIsNotNone(assistant)
        assert assistant is not None
        calls = _tool_call_parts(assistant)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "confirm_action")
        self.assertEqual(calls[0]["tool_call_id"], tool_call_id)

        test_model = TestModel(call_tools=["confirm_action"], custom_output_text="resumed ok")
        events = await self._stream_events(
            model=test_model,
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
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertTrue(any(ev.get("type") == "tool_result" for ev in events))

    async def test_e3_new_message_after_deferred_without_confirm(self) -> None:
        session_id, _ = await self._run_hitl_pending()

        test_model = TestModel(custom_output_text="continued without confirm")
        events = await self._stream_events(
            model=test_model,
            json_body={"session_id": session_id, "message": "跳过确认继续"},
        )
        final = next(ev for ev in reversed(events) if ev.get("type") == "final")
        self.assertEqual(final["status"], "completed")
        self.assertNotIn("error", {ev.get("type") for ev in events})

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            hist = await client.get(f"/api/v1/sessions/{session_id}/history")
        messages = hist.json()["messages"]
        assistants = [m for m in messages if m.get("role") == "assistant"]
        self.assertGreaterEqual(len(assistants), 2)

    async def test_e4_immediate_followup_after_disconnect(self) -> None:
        chunks = ["only-", "second-", "third-"]
        model, _tracker = build_slow_stream_model(chunks=chunks, delay_s=0.05)

        session_id: str | None = None
        body = ""
        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect() as disconnect_flag:
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
                                    disconnect_flag.disconnected = True
                                    await response.aclose()
                                    break
                            else:
                                continue
                            break

                    await asyncio.sleep(0.4)
                    follow = TestModel(custom_output_text="second turn")
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
            with mock_request_disconnect() as disconnect_flag:
                session_id: str | None = None
                events: list[dict] = []

                async def _disconnect_after_first_chunk() -> None:
                    await asyncio.sleep(0.8)
                    disconnect_flag.disconnected = True

                disconnect_task = asyncio.create_task(_disconnect_after_first_chunk())
                try:
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

                        await asyncio.sleep(0.3)
                        cancelled = next((ev for ev in events if ev.get("type") == "cancelled"), None)
                        hist = await client.get(f"/api/v1/sessions/{session_id}/history")
                        data = hist.json()
                        if cancelled is not None:
                            self.assertEqual(cancelled["model_message_count"], data["model_message_count"])
                        self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
                finally:
                    disconnect_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await disconnect_task

    async def test_e6_branch_point_on_cancel(self) -> None:
        chunks = ["branch-"]
        model, _ = build_slow_stream_model(chunks=chunks, delay_s=0.05)

        with patch("deep_agent.agent.factory._resolve_model", return_value=model):
            with mock_request_disconnect() as disconnect_flag:
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
                                disconnect_flag.disconnected = True
                                await response.aclose()
                                break

                cancelled = next((ev for ev in events if ev.get("type") == "cancelled"), None)
                if cancelled is not None:
                    self.assertEqual(cancelled.get("user_branch_point"), 0)

    async def test_e7_old_deferred_invalid_after_new_message(self) -> None:
        # repair injects ToolDenied in-memory; DB still shows pending tool_call for display.
        # Submitting deferred_tool_results for the abandoned call must not succeed.
        session_id, tool_call_id = await self._run_hitl_pending()

        test_model = TestModel(custom_output_text="skipped")
        await self._stream_events(
            model=test_model,
            json_body={"session_id": session_id, "message": "放弃旧确认"},
        )

        resume_model = TestModel(custom_output_text="should not run")
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

    async def test_e8_sync_chat_after_deferred_without_confirm(self) -> None:
        session_id, _ = await self._run_hitl_pending()
        test_model = TestModel(custom_output_text="sync ok")
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
