from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.core.context import reset_context
from tests.helpers.testing_context import init_test_context


def _mock_toolset(url: str) -> AsyncMock:
    toolset = AsyncMock()
    toolset.__aenter__ = AsyncMock(return_value=toolset)
    toolset.__aexit__ = AsyncMock(return_value=None)
    toolset._connect_url = url
    return toolset


class McpSessionScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_test_context(mcp_session_pool=McpSessionPool())

    def tearDown(self) -> None:
        reset_context()

    async def test_concurrent_acquires_share_one_client(self) -> None:
        pool = McpSessionPool()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            pool._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(pool._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(pool._shared_registry, "stop_touch", new=AsyncMock()):
                    first = await pool.acquire("text_tools", "reuse-session", factory, spawn_config={})
                    second = await pool.acquire("text_tools", "reuse-session", factory, spawn_config={})
                    await pool.release("text_tools", "reuse-session")
                    await pool.release("text_tools", "reuse-session")

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)

    async def test_different_sessions_get_separate_clients(self) -> None:
        pool = McpSessionPool()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            pool._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(pool._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(pool._shared_registry, "stop_touch", new=AsyncMock()):
                    client_a = await pool.acquire("text_tools", "session-a", factory, spawn_config={})
                    client_b = await pool.acquire("text_tools", "session-b", factory, spawn_config={})
                    await pool.release("text_tools", "session-a")
                    await pool.release("text_tools", "session-b")

        self.assertIsNot(client_a, client_b)
        self.assertEqual(len(created), 2)

    async def test_release_closes_client_immediately(self) -> None:
        pool = McpSessionPool()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            pool._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(pool._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(pool._shared_registry, "stop_touch", new=AsyncMock()):
                    await pool.acquire("text_tools", "idle-session", factory, spawn_config={})
                    await pool.release("text_tools", "idle-session")
                    await pool.acquire("text_tools", "idle-session", factory, spawn_config={})
                    await pool.release("text_tools", "idle-session")

        self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()
