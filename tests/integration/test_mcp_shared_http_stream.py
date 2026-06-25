from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.test import TestModel

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.options import OPTION
from tests.helpers.sqlite_stores import make_sqlite_stores


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


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _cleanup_registry_dir(runtime_dir: Path) -> None:
    for path in runtime_dir.glob("builtin-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _terminate_pid(int(record.get("pid") or 0))


class McpSharedHttpStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_runtime = OPTION.agent.mcp_runtime_dir
        self._prev_timeout = float(OPTION.agent.mcp_init_timeout)
        OPTION.agent.mcp_runtime_dir = self._tmpdir.name
        OPTION.agent.mcp_init_timeout = 15.0

    async def asyncTearDown(self) -> None:
        _cleanup_registry_dir(Path(self._tmpdir.name))
        OPTION.agent.mcp_runtime_dir = self._prev_runtime
        OPTION.agent.mcp_init_timeout = float(self._prev_timeout)
        self._tmpdir.cleanup()
        reset_context()

    async def test_chat_stream_completes_with_shared_http_text_tools(self) -> None:
        registry = McpSharedRegistry(runtime_dir=Path(self._tmpdir.name))
        manager = McpLeaseManager(shared_registry=registry)
        stores, engine, session_factory = await make_sqlite_stores()
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=manager,
                db_engine=engine,
                db_session_factory=session_factory,
            )
        )

        app = FastAPI()
        app.include_router(api_router)
        test_model = TestModel(
            call_tools=["text_tools_reverse"],
            custom_output_text="shared-http-ok",
        )

        with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/api/v1/chat/stream",
                    json={
                        "message": "reverse abc",
                        "capabilities": ["mcp:text_tools"],
                    },
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join([chunk async for chunk in response.aiter_text()])

        events = _parse_sse_events(body)
        self.assertTrue(events, "expected SSE events")
        self.assertEqual(events[0]["type"], "session")
        final = next((ev for ev in reversed(events) if ev.get("type") == "final"), None)
        self.assertIsNotNone(final, f"missing final event in: {events!r}")
        assert final is not None
        self.assertEqual(final.get("status"), "completed")
        self.assertEqual(final.get("output"), "shared-http-ok")

        registry_json = Path(self._tmpdir.name) / "builtin-text_tools.json"
        self.assertTrue(registry_json.is_file(), "shared MCP registry file should exist")
        record = json.loads(registry_json.read_text(encoding="utf-8"))
        self.assertIn("/mcp", record["url"])


if __name__ == "__main__":
    unittest.main()
