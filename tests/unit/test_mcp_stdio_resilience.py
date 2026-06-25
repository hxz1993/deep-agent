from __future__ import annotations

import contextlib
import unittest
from unittest.mock import AsyncMock

from mcp.shared.exceptions import McpError
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.session_scoped import SessionScopedMcpToolset
from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.core.context import reset_context
from tests.helpers.testing_context import init_test_context


def _mcp_connection_closed() -> McpError:
    return McpError(type("ErrorData", (), {"message": "Connection closed"})())


class McpRecoverableDisconnectTests(unittest.TestCase):
    def test_connection_closed_is_recoverable(self) -> None:
        self.assertTrue(is_recoverable_mcp_disconnect(_mcp_connection_closed()))

    def test_unrelated_errors_are_not_recoverable(self) -> None:
        self.assertFalse(is_recoverable_mcp_disconnect(ValueError("bad tool args")))


class SessionScopedMcpRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_test_context(mcp_session_pool=McpSessionPool())

    def tearDown(self) -> None:
        reset_context()

    async def test_call_tool_retries_after_connection_closed(self) -> None:
        first_inner = AsyncMock()
        first_inner.__aenter__ = AsyncMock(return_value=first_inner)
        first_inner.__aexit__ = AsyncMock(return_value=None)
        first_inner.get_tools = AsyncMock(return_value={})
        first_inner.call_tool = AsyncMock(side_effect=_mcp_connection_closed())

        second_inner = AsyncMock()
        second_inner.__aenter__ = AsyncMock(return_value=second_inner)
        second_inner.__aexit__ = AsyncMock(return_value=None)
        second_inner.get_tools = AsyncMock(return_value={})
        second_inner.call_tool = AsyncMock(return_value="recovered")

        inners = [first_inner, second_inner]
        lease_calls = {"count": 0}

        @contextlib.asynccontextmanager
        async def fake_lease(*_args, **_kwargs):
            inner = inners[lease_calls["count"]]
            lease_calls["count"] += 1
            yield inner

        pool = McpSessionPool()
        pool.lease = fake_lease  # type: ignore[method-assign]

        toolset = SessionScopedMcpToolset(
            server_name="deepcube",
            config={"command": "python", "args": ["server.py"]},
            pool=pool,
            _session_id="session-a",
        )

        ctx = RunContext(deps=AgentDeps(session_id="session-a"), model=TestModel(), usage=RunUsage())
        tool = ToolsetTool(
            toolset=toolset,
            tool_def=AsyncMock(),
            max_retries=0,
            args_validator=AsyncMock(),
        )

        async with toolset:
            result = await toolset.call_tool("deepcube_ping", {}, ctx, tool)

        self.assertEqual(result, "recovered")
        self.assertEqual(lease_calls["count"], 2)
        second_inner.call_tool.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
