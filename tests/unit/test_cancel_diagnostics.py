from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from sse_starlette.sse import AppStatus

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.api.v1.stream_diagnostics import DiagnosticEventSourceResponse
from deep_agent.diagnostics.schema import EVENTS, safe_fields


class CancelDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def entry(self):
        registry = ActiveRunRegistry()
        entry = registry.register(agent_id="agent", session_id="session", user_id="owner",
                                  run_id="run", conversation_id="conversation")
        return registry, entry

    async def test_registry_first_source_owner_check_shutdown_and_sink_failure(self):
        registry, entry = self.entry()
        with patch("deep_agent.agent.active_runs.log_event") as emit:
            self.assertFalse(registry.request_cancel(agent_id="agent", session_id="session", user_id="other"))
            emit.assert_not_called()
            self.assertTrue(registry.request_cancel(agent_id="agent", session_id="session", user_id="owner"))
            entry.observe_cancellation("http_disconnect")
            self.assertEqual(entry.cancel_reason, "cancel_api")
            self.assertEqual(emit.call_count, 1)
            self.assertEqual(emit.call_args.kwargs["run_id"], "run")
        registry, entry = self.entry()
        with patch("deep_agent.agent.active_runs.log_event", side_effect=RuntimeError("broken sink")):
            registry.cancel_all()
        self.assertEqual(entry.cancel_reason, "server_shutdown")
        self.assertTrue(entry.cancellation_token.cancelled)
        self.assertEqual(registry.active_count(), 0)

    async def exercise_transport(self, mode, *, broken_sink=False):
        _, entry = self.entry()
        incoming = asyncio.Queue()
        ready, done = asyncio.Event(), asyncio.Event()
        rows, frames = [], []

        async def stream():
            try:
                yield {"event": "text", "data": "synthetic"}
                ready.set()
                if mode != "complete":
                    await asyncio.Event().wait()
            finally:
                done.set()

        async def send(frame):
            frames.append(frame)
            if mode == "send_error" and frame["type"] == "http.response.body":
                raise OSError("private connection detail")

        def emit(event, **fields):
            if broken_sink:
                raise RuntimeError("broken sink")
            rows.append((event, fields))

        response = DiagnosticEventSourceResponse(stream(), fields={"session_id": "session", "run_id": "run"},
                                                 active_run=lambda: entry)
        with patch("deep_agent.api.v1.stream_diagnostics.log_event", side_effect=emit):
            task = asyncio.create_task(response({"type": "http"}, incoming.get, send))
            try:
                if mode not in {"send_error", "complete"}:
                    await asyncio.wait_for(ready.wait(), 3)
                    if mode == "disconnect":
                        incoming.put_nowait({"type": "http.disconnect"})
                    else:
                        task.cancel()
                result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await response.body_iterator.aclose()
        self.assertTrue(done.is_set())
        if mode in {"complete", "disconnect"}:
            self.assertIsNone(result[0])
        elif mode == "send_error":
            self.assertIsInstance(result[0], BaseExceptionGroup)
        else:
            self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertNotIn("private", str(rows))
        return entry, rows, frames

    async def test_real_sse_disconnect_observed_before_task_cancellation(self):
        entry, rows, _ = await self.exercise_transport("disconnect")
        self.assertEqual(entry.cancel_reason, "http_disconnect")
        self.assertEqual(rows[-1][1]["reason"], "http_disconnect")

    async def test_normal_completion_is_not_a_cancellation(self):
        entry, rows, frames = await self.exercise_transport("complete")
        self.assertIsNone(entry.cancel_reason)
        self.assertEqual(rows[-1][1]["reason"], "stream_completed")
        self.assertFalse(frames[-1]["more_body"])

    async def test_unknown_cancel_not_misreported_as_disconnect(self):
        entry, rows, _ = await self.exercise_transport("cancel")
        self.assertEqual(entry.cancel_reason, "task_cancelled_unknown")
        self.assertEqual(rows[-1][1]["reason"], "task_cancelled_unknown")

    async def test_shutdown_flag_and_send_failure(self):
        # Set shutdown only when interpreting task cancellation, not before the
        # SSE watcher starts (which would bypass the model/stream entirely).
        with patch("deep_agent.api.v1.stream_diagnostics.transport_cancel_reason", return_value="server_shutdown"):
            entry, rows, _ = await self.exercise_transport("cancel")
        self.assertEqual(entry.cancel_reason, "server_shutdown")
        entry, rows, _ = await self.exercise_transport("send_error")
        self.assertEqual(entry.cancel_reason, "transport_send_error")
        self.assertEqual(rows[-1][1]["reason"], "transport_send_error")

    async def test_broken_sink_preserves_transport_completion_and_disconnect(self):
        for mode in ("complete", "disconnect", "cancel", "send_error"):
            await self.exercise_transport(mode, broken_sink=True)

    async def test_safe_registered_fields_and_actual_shutdown_flag(self):
        from deep_agent.api.v1.stream_diagnostics import transport_cancel_reason
        with patch.object(AppStatus, "should_exit", True):
            self.assertEqual(transport_cancel_reason(), "server_shutdown")
        for event in ("agent_cancel_observed", "agent_cancel_api_received", "agent_stream_closed"):
            self.assertIn(event, EVENTS)
        self.assertEqual(safe_fields({"reason": "http_disconnect", "run_id": "run", "cookie": "secret",
                                     "body": "private", "exception": RuntimeError("private")}),
                         {"reason": "http_disconnect", "run_id": "run"})
