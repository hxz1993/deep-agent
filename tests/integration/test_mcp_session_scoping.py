from __future__ import annotations

import json
import os
import sys
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.session_pool import McpSessionPool, get_mcp_session_pool
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.core.config import get_settings

PID_ECHO_SERVER = Path(__file__).resolve().parent / "fixtures" / "pid_echo_server.py"
PID_TOOL_NAME = "pid_echo_echo_process_id"


def _run_context(session_id: str) -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id=session_id),
        model=TestModel(),
        usage=RunUsage(),
    )


def _build_pid_toolset(record_path: Path):
    settings = replace(get_settings(), mcp_stdio_session_scope=True)
    with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.get_settings", return_value=settings):
        return build_mcp_toolset(
            "pid_echo",
            "stdio",
            {
                "command": sys.executable,
                "args": [str(PID_ECHO_SERVER)],
                "env": {**os.environ, "MCP_PID_RECORD": str(record_path)},
            },
        )


class McpSessionScopingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_context(AppContext.for_testing(mcp_session_pool=McpSessionPool()))

    def tearDown(self) -> None:
        reset_context()

    async def _call_pid_tool(self, toolset, session_id: str) -> None:
        ctx = _run_context(session_id)
        active = await toolset.for_run(ctx)
        async with active:
            tools = await active.get_tools(ctx)
            if PID_TOOL_NAME not in tools:
                raise AssertionError(f"Expected tool {PID_TOOL_NAME!r}, got {sorted(tools)}")
            await active.call_tool(PID_TOOL_NAME, {}, ctx, tools[PID_TOOL_NAME])

    async def test_same_session_reuses_mcp_subprocess(self) -> None:
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        record_path = Path(path)
        toolset = _build_pid_toolset(record_path)

        await self._call_pid_tool(toolset, "reuse-session")
        await self._call_pid_tool(toolset, "reuse-session")

        recorded = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(len(recorded), 2)
        self.assertEqual(recorded[0], recorded[1])

    async def test_different_sessions_use_different_subprocesses(self) -> None:
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        record_path = Path(path)
        toolset = _build_pid_toolset(record_path)

        await self._call_pid_tool(toolset, "session-a")
        await self._call_pid_tool(toolset, "session-b")

        recorded = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(len(recorded), 2)
        self.assertNotEqual(recorded[0], recorded[1])

    async def test_close_session_stops_pooled_subprocess(self) -> None:
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        record_path = Path(path)
        toolset = _build_pid_toolset(record_path)
        pool = get_mcp_session_pool()

        await self._call_pid_tool(toolset, "close-session")
        first_pid = json.loads(record_path.read_text(encoding="utf-8"))[0]

        await pool.close_session("close-session")
        record_path.write_text("[]", encoding="utf-8")

        await self._call_pid_tool(toolset, "close-session")
        second_pid = json.loads(record_path.read_text(encoding="utf-8"))[0]
        self.assertNotEqual(first_pid, second_pid)


if __name__ == "__main__":
    unittest.main()
