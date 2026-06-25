from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import reset_context
from tests.helpers.testing_context import init_test_context


def _mock_toolset(url: str) -> AsyncMock:
    toolset = AsyncMock()
    toolset.__aenter__ = AsyncMock(return_value=toolset)
    toolset.__aexit__ = AsyncMock(return_value=None)
    toolset._connect_url = url
    return toolset


class McpRunScopingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_test_context(mcp_lease_manager=McpLeaseManager())

    def tearDown(self) -> None:
        reset_context()

    async def test_concurrent_leases_create_separate_clients(self) -> None:
        manager = McpLeaseManager()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            manager._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(manager._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()):
                    async with manager.lease("text_tools", "reuse-session", factory, spawn_config={}):
                        first = created[-1]
                        async with manager.lease("text_tools", "reuse-session", factory, spawn_config={}):
                            second = created[-1]

        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)

    async def test_different_sessions_get_separate_clients(self) -> None:
        manager = McpLeaseManager()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            manager._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(manager._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()):
                    async with manager.lease("text_tools", "session-a", factory, spawn_config={}):
                        client_a = created[-1]
                    async with manager.lease("text_tools", "session-b", factory, spawn_config={}):
                        client_b = created[-1]

        self.assertIsNot(client_a, client_b)
        self.assertEqual(len(created), 2)

    async def test_sequential_leases_reopen_client(self) -> None:
        manager = McpLeaseManager()
        created: list[AsyncMock] = []

        def factory(url: str) -> AsyncMock:
            toolset = _mock_toolset(url)
            created.append(toolset)
            return toolset

        with patch.object(
            manager._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ):
            with patch.object(manager._shared_registry, "start_touch", new=AsyncMock()):
                with patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()):
                    async with manager.lease("text_tools", "idle-session", factory, spawn_config={}):
                        pass
                    async with manager.lease("text_tools", "idle-session", factory, spawn_config={}):
                        pass

        self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()
