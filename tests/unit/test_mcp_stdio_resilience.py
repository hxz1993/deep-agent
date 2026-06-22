from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from mcp.shared.exceptions import McpError
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.serializing import SerializingToolset
from deep_agent.capabilities.mcp.session_scoped import SessionScopedMcpToolset
from deep_agent.capabilities.mcp.session_pool import McpSessionPool, get_mcp_session_pool
from deep_agent.core.context import AppContext, init_context, reset_context


def _mcp_connection_closed() -> McpError:
    return McpError(type("ErrorData", (), {"message": "Connection closed"})())


class McpRecoverableDisconnectTests(unittest.TestCase):
    def test_connection_closed_is_recoverable(self) -> None:
        self.assertTrue(is_recoverable_mcp_disconnect(_mcp_connection_closed()))

    def test_unrelated_errors_are_not_recoverable(self) -> None:
        self.assertFalse(is_recoverable_mcp_disconnect(ValueError("bad tool args")))


class SerializingToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_serializes_concurrent_call_tool(self) -> None:
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        wrapped = AsyncMock()
        ctx = RunContext(deps=AgentDeps(session_id="test"), model=TestModel(), usage=RunUsage())
        tool = ToolsetTool(
            toolset=wrapped,
            tool_def=AsyncMock(),
            max_retries=0,
            args_validator=AsyncMock(),
        )

        async def slow_call_tool(*_args, **_kwargs) -> str:
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1
            return "ok"

        wrapped.call_tool = AsyncMock(side_effect=slow_call_tool)
        toolset = SerializingToolset(wrapped)

        await asyncio.gather(
            toolset.call_tool("a", {}, ctx, tool),
            toolset.call_tool("b", {}, ctx, tool),
            toolset.call_tool("c", {}, ctx, tool),
        )

        self.assertEqual(max_active, 1)
        self.assertEqual(wrapped.call_tool.await_count, 3)


class SessionScopedMcpRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_context(AppContext.for_testing(mcp_session_pool=McpSessionPool()))

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

        created = [first_inner, second_inner]
        factory_calls = {"count": 0}

        def factory() -> AsyncMock:
            toolset = created[factory_calls["count"]]
            factory_calls["count"] += 1
            return toolset

        pool = get_mcp_session_pool()
        pool._connect_with_retry = AsyncMock(side_effect=lambda _name, _sid, factory: factory())  # type: ignore[method-assign]

        toolset = SessionScopedMcpToolset(
            server_name="deepcube",
            config={"command": "python", "args": ["server.py"]},
            pool=pool,
            _session_id="session-a",
        )
        toolset._build_inner_toolset = factory  # type: ignore[method-assign]
        toolset._inner = first_inner

        ctx = RunContext(deps=AgentDeps(session_id="session-a"), model=TestModel(), usage=RunUsage())
        tool = ToolsetTool(
            toolset=toolset,
            tool_def=AsyncMock(),
            max_retries=0,
            args_validator=AsyncMock(),
        )

        with unittest.mock.patch.object(pool, "reset_entry", new=AsyncMock()) as reset_entry:
            with unittest.mock.patch.object(pool, "acquire", new=AsyncMock(return_value=second_inner)) as acquire:
                result = await toolset.call_tool("deepcube_ping", {}, ctx, tool)

        self.assertEqual(result, "recovered")
        reset_entry.assert_awaited_once_with("deepcube", "session-a")
        acquire.assert_awaited_once()
        second_inner.call_tool.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
