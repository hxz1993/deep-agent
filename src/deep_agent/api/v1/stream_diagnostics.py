"""Observe the existing SSE transport without consuming extra ASGI messages."""
from __future__ import annotations

import asyncio
import time

from sse_starlette.sse import AppStatus, EventSourceResponse

from deep_agent.agent.observability import log_event


def transport_cancel_reason() -> str:
    # SSE's shutdown watcher cancels streams before application lifespan closes
    # the registry. Read its existing flag; do not install a signal handler.
    return "server_shutdown" if AppStatus.should_exit else "task_cancelled_unknown"


class DiagnosticEventSourceResponse(EventSourceResponse):
    """Public ASGI boundary wrapper, compatible with the existing SSE versions."""

    def __init__(self, content, *, fields: dict, active_run, **kwargs):
        super().__init__(content, **kwargs)
        self._diagnostic_fields = fields
        self._diagnostic_active_run = active_run

    async def __call__(self, scope, receive, send):
        started = time.monotonic()
        reason = "stream_completed"
        body_finished = False

        def observe(value):
            nonlocal reason
            reason = value
            try:
                entry = self._diagnostic_active_run()
                if entry is not None:
                    entry.observe_cancellation(value)
            except Exception:
                pass

        async def observed_receive():
            message = await receive()
            if message.get("type") == "http.disconnect" and not body_finished:
                observe("http_disconnect")
            return message

        async def observed_send(message):
            nonlocal body_finished
            try:
                await send(message)
            except OSError:
                observe("transport_send_error")
                raise
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                body_finished = True

        try:
            await super().__call__(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            if reason == "stream_completed":
                observe(transport_cancel_reason())
            raise
        except BaseException:
            if reason == "stream_completed":
                reason = "stream_error"
            raise
        finally:
            if not body_finished and reason == "stream_completed":
                reason = transport_cancel_reason()
            try:
                log_event("agent_stream_closed", **self._diagnostic_fields,
                          reason=reason, duration_ms=round((time.monotonic() - started) * 1000))
            except Exception:
                pass
