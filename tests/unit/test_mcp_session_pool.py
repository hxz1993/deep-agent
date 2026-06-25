from __future__ import annotations

import contextlib
import unittest
from unittest.mock import AsyncMock, patch

from mcp.shared.exceptions import McpError

from deep_agent.capabilities.mcp.session_pool import McpSessionPool


def _mcp_connection_closed() -> McpError:
    return McpError(type("ErrorData", (), {"message": "Connection closed"})())


@contextlib.contextmanager
def _mock_shared_registry(pool: McpSessionPool):
    with (
        patch.object(
            pool._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ),
        patch.object(pool._shared_registry, "start_touch", new=AsyncMock()),
        patch.object(pool._shared_registry, "stop_touch", new=AsyncMock()),
    ):
        yield


class McpSessionPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_acquires_create_new_clients(self) -> None:
        pool = McpSessionPool()
        created: list[AsyncMock] = []

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            created.append(toolset)
            return toolset

        with _mock_shared_registry(pool):
            first = await pool.acquire("text_tools", "session-a", factory)
            await pool.release("text_tools", "session-a")
            second = await pool.acquire("text_tools", "session-a", factory)
            await pool.release("text_tools", "session-a")

        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)
        first.__aexit__.assert_awaited_once()
        second.__aexit__.assert_awaited_once()

    async def test_different_sessions_get_different_toolsets(self) -> None:
        pool = McpSessionPool()

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with _mock_shared_registry(pool):
            first = await pool.acquire("text_tools", "session-a", factory)
            second = await pool.acquire("text_tools", "session-b", factory)
            await pool.release("text_tools", "session-a")
            await pool.release("text_tools", "session-b")

        self.assertIsNot(first, second)

    async def test_lease_closes_client_on_exit(self) -> None:
        pool = McpSessionPool()
        toolset = AsyncMock()
        toolset.__aenter__ = AsyncMock(return_value=toolset)
        toolset.__aexit__ = AsyncMock(return_value=None)

        with _mock_shared_registry(pool):
            async with pool.lease("text_tools", "session-a", lambda _url: toolset):
                self.assertIn(("text_tools", "session-a"), pool._entries)

            self.assertNotIn(("text_tools", "session-a"), pool._entries)

        toolset.__aexit__.assert_awaited_once()

    async def test_acquire_retries_recoverable_connect_failure(self) -> None:
        pool = McpSessionPool(mcp_acquire_retries=1)
        attempts = {"count": 0}

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            attempts["count"] += 1
            if attempts["count"] == 1:
                toolset.__aenter__ = AsyncMock(side_effect=_mcp_connection_closed())
            else:
                toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with _mock_shared_registry(pool):
            toolset = await pool.acquire("text_tools", "session-a", factory)
            await pool.release("text_tools", "session-a")

        self.assertEqual(attempts["count"], 2)
        self.assertIsNotNone(toolset)

    async def test_acquire_does_not_retry_non_recoverable_errors(self) -> None:
        pool = McpSessionPool(mcp_acquire_retries=2)
        attempts = {"count": 0}

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            attempts["count"] += 1
            toolset.__aenter__ = AsyncMock(side_effect=ValueError("bad config"))
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with _mock_shared_registry(pool):
            with self.assertRaises(ValueError):
                await pool.acquire("text_tools", "session-a", factory)

        self.assertEqual(attempts["count"], 1)


if __name__ == "__main__":
    unittest.main()
