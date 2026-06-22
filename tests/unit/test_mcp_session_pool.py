from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.core.config import Settings, get_settings


class McpSessionPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_toolset_for_same_session(self) -> None:
        pool = McpSessionPool()
        created: list[object] = []

        def factory() -> object:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            created.append(toolset)
            return toolset

        first = await pool.acquire("echo", "session-a", factory)
        await pool.release_run("echo", "session-a")
        second = await pool.acquire("echo", "session-a", factory)

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)

    async def test_different_sessions_get_different_toolsets(self) -> None:
        pool = McpSessionPool()

        def factory() -> object:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        first = await pool.acquire("echo", "session-a", factory)
        second = await pool.acquire("echo", "session-b", factory)

        self.assertIsNot(first, second)

    async def test_close_session_tears_down_subprocess(self) -> None:
        pool = McpSessionPool()
        toolset = AsyncMock()
        toolset.__aenter__ = AsyncMock(return_value=toolset)
        toolset.__aexit__ = AsyncMock(return_value=None)

        await pool.acquire("echo", "session-a", lambda: toolset)
        await pool.release_run("echo", "session-a")
        await pool.close_session("session-a")

        toolset.__aexit__.assert_awaited_once()

    async def test_acquire_retries_recoverable_connect_failure(self) -> None:
        pool = McpSessionPool()
        settings = replace(get_settings(), mcp_acquire_retries=1)
        attempts = {"count": 0}

        def factory() -> object:
            toolset = AsyncMock()
            attempts["count"] += 1
            if attempts["count"] == 1:
                toolset.__aenter__ = AsyncMock(
                    side_effect=RuntimeError("Client failed to connect: Failed to initialize server session")
                )
            else:
                toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with patch("deep_agent.capabilities.mcp.session_pool.get_settings", return_value=settings):
            toolset = await pool.acquire("deepcube", "session-a", factory)

        self.assertEqual(attempts["count"], 2)
        self.assertIsNotNone(toolset)

    async def test_acquire_does_not_retry_non_recoverable_errors(self) -> None:
        pool = McpSessionPool()
        settings = replace(get_settings(), mcp_acquire_retries=2)
        attempts = {"count": 0}

        def factory() -> object:
            toolset = AsyncMock()
            attempts["count"] += 1
            toolset.__aenter__ = AsyncMock(side_effect=ValueError("bad config"))
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with patch("deep_agent.capabilities.mcp.session_pool.get_settings", return_value=settings):
            with self.assertRaises(ValueError):
                await pool.acquire("echo", "session-a", factory)

        self.assertEqual(attempts["count"], 1)


if __name__ == "__main__":
    unittest.main()
