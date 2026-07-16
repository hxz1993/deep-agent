from __future__ import annotations

import contextlib
import unittest
from unittest.mock import AsyncMock, patch

from mcp.shared.exceptions import McpError

from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager


def _mcp_connection_closed() -> McpError:
    return McpError(type("ErrorData", (), {"message": "Connection closed"})())


@contextlib.contextmanager
def _mock_shared_registry(manager: McpLeaseManager):
    with (
        patch.object(
            manager._shared_registry,
            "ensure_server",
            new=AsyncMock(return_value="http://127.0.0.1:19000/mcp"),
        ),
        patch.object(manager._shared_registry, "start_touch", new=AsyncMock()),
        patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()),
    ):
        yield


class McpLeaseManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_leases_create_new_clients(self) -> None:
        manager = McpLeaseManager()
        created: list[AsyncMock] = []

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            created.append(toolset)
            return toolset

        with _mock_shared_registry(manager):
            async with manager.lease("text_tools", "session-a", factory):
                first = created[-1]
            async with manager.lease("text_tools", "session-a", factory):
                second = created[-1]

        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)
        first.__aexit__.assert_awaited_once()
        second.__aexit__.assert_awaited_once()

    async def test_different_sessions_get_different_toolsets(self) -> None:
        manager = McpLeaseManager()
        seen: list[AsyncMock] = []

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            toolset.__aenter__ = AsyncMock(return_value=toolset)
            toolset.__aexit__ = AsyncMock(return_value=None)
            seen.append(toolset)
            return toolset

        with _mock_shared_registry(manager):
            async with manager.lease("text_tools", "session-a", factory):
                first = seen[-1]
            async with manager.lease("text_tools", "session-b", factory):
                second = seen[-1]

        self.assertIsNot(first, second)

    async def test_lease_closes_client_on_exit(self) -> None:
        manager = McpLeaseManager()
        toolset = AsyncMock()
        toolset.__aenter__ = AsyncMock(return_value=toolset)
        toolset.__aexit__ = AsyncMock(return_value=None)

        with _mock_shared_registry(manager):
            async with manager.lease("text_tools", "session-a", lambda _url: toolset):
                pass

        toolset.__aexit__.assert_awaited_once()

    async def test_lease_retries_recoverable_connect_failure(self) -> None:
        manager = McpLeaseManager(mcp_acquire_retries=1)
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

        with _mock_shared_registry(manager):
            async with manager.lease("text_tools", "session-a", factory) as toolset:
                self.assertIsNotNone(toolset)

        self.assertEqual(attempts["count"], 2)

    async def test_lease_does_not_retry_non_recoverable_errors(self) -> None:
        manager = McpLeaseManager(mcp_acquire_retries=2)
        attempts = {"count": 0}

        def factory(_url: str) -> AsyncMock:
            toolset = AsyncMock()
            attempts["count"] += 1
            toolset.__aenter__ = AsyncMock(side_effect=ValueError("bad config"))
            toolset.__aexit__ = AsyncMock(return_value=None)
            return toolset

        with _mock_shared_registry(manager):
            with self.assertRaises(ValueError):
                async with manager.lease("text_tools", "session-a", factory):
                    pass

        self.assertEqual(attempts["count"], 1)

    async def test_lease_retries_ensure_server_timeout(self) -> None:
        """Transient lock-contention timeouts from ensure_server are retried.

        Simulates the 2026-07-16 10:09 incident pattern: the first ensure_server
        call times out (worker A held the shared lock), the second succeeds
        (worker A released by then). The lease must reach the client connect
        phase rather than surface the timeout to the user.
        """
        manager = McpLeaseManager(mcp_acquire_retries=1)
        ensure_calls = {"count": 0}

        async def flaky_ensure(*_args: object, **_kwargs: object) -> str:
            ensure_calls["count"] += 1
            if ensure_calls["count"] == 1:
                raise TimeoutError(
                    "Timed out acquiring MCP shared lock for 'text_tools' "
                    "(/tmp/deep-agent-mcp/builtin-text_tools.lock)"
                )
            return "http://127.0.0.1:19001/mcp"

        toolset = AsyncMock()
        toolset.__aenter__ = AsyncMock(return_value=toolset)
        toolset.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(
                manager._shared_registry,
                "ensure_server",
                new=AsyncMock(side_effect=flaky_ensure),
            ),
            patch.object(manager._shared_registry, "start_touch", new=AsyncMock()),
            patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()),
        ):
            async with manager.lease(
                "text_tools", "session-a", lambda _url: toolset
            ) as leased:
                self.assertIs(leased, toolset)

        self.assertEqual(ensure_calls["count"], 2)
        toolset.__aexit__.assert_awaited_once()

    async def test_lease_re_raises_ensure_server_timeout_after_exhaustion(self) -> None:
        """When ensure_server keeps timing out, lease surfaces TimeoutError.

        With ``mcp_acquire_retries=1`` we get 2 attempts total; both fail ⇒
        bubble up. Retry budget must be respected (not retried forever).
        """
        manager = McpLeaseManager(mcp_acquire_retries=1)
        ensure_calls = {"count": 0}

        async def always_timeout(*_args: object, **_kwargs: object) -> str:
            ensure_calls["count"] += 1
            raise TimeoutError(
                "Timed out acquiring MCP shared lock for 'text_tools'"
            )

        with (
            patch.object(
                manager._shared_registry,
                "ensure_server",
                new=AsyncMock(side_effect=always_timeout),
            ),
            patch.object(manager._shared_registry, "start_touch", new=AsyncMock()),
            patch.object(manager._shared_registry, "stop_touch", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                async with manager.lease(
                    "text_tools", "session-a", lambda _url: AsyncMock()
                ):
                    pass

        # 2 total attempts (initial + 1 retry); start_touch/stop_touch never called
        # because ensure_server never returned a URL.
        self.assertEqual(ensure_calls["count"], 2)



if __name__ == "__main__":
    unittest.main()
