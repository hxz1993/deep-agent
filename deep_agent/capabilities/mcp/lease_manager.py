from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger

from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry
from deep_agent.options import OPTION

McpToolsetFactory = Callable[[str], Any]

_ACQUIRE_RETRY_DELAY_SECONDS = 0.5


class McpLeaseManager:
    """Worker-local MCP client coordinator for shared-HTTP builtin servers.

    Each lease opens a short-lived HTTP client and closes it on exit. Pod-wide
    server processes are coordinated via :class:`McpSharedRegistry` (registry
    touch + in-server TTL watcher).
    """

    def __init__(
        self,
        *,
        mcp_acquire_retries: int | None = None,
        shared_registry: McpSharedRegistry | None = None,
    ) -> None:
        self._mcp_acquire_retries = (
            OPTION.agent.mcp_acquire_retries
            if mcp_acquire_retries is None
            else mcp_acquire_retries
        )
        self._shared_registry = shared_registry or McpSharedRegistry()

    @contextlib.asynccontextmanager
    async def lease(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
        *,
        spawn_config: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        connect_url = await self._shared_registry.ensure_server(
            server_name,
            spawn_config,
        )
        await self._shared_registry.start_touch(server_name)
        toolset: Any | None = None
        try:
            toolset = await self._connect_with_retry(
                server_name,
                session_id,
                factory,
                connect_url=connect_url,
            )
            logger.info(
                "Connected MCP client for {!r} session {!r} at {}",
                server_name,
                session_id,
                connect_url,
            )
            yield toolset
        except Exception:
            if toolset is None:
                await self._shared_registry.stop_touch(server_name)
            raise
        finally:
            if toolset is not None:
                await self._close_toolset(server_name, session_id, toolset)
                await self._shared_registry.stop_touch(server_name)

    async def _connect_with_retry(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
        *,
        connect_url: str,
    ) -> Any:
        max_retries = max(0, self._mcp_acquire_retries)
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            toolset = factory(connect_url)
            try:
                await toolset.__aenter__()
            except Exception as exc:
                last_exc = exc
                await self._safe_close(toolset)
                if attempt >= max_retries or not is_recoverable_mcp_disconnect(exc):
                    raise
                logger.warning(
                    "MCP connect failed for {!r} session {!r} (attempt {}/{}): {}",
                    server_name,
                    session_id,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                await asyncio.sleep(_ACQUIRE_RETRY_DELAY_SECONDS)
                continue

            if attempt > 0:
                logger.info(
                    "MCP server {!r} connected for session {!r} after retry {}",
                    server_name,
                    session_id,
                    attempt,
                )
            return toolset

        assert last_exc is not None
        raise last_exc

    async def close_all(self) -> None:
        await self._shared_registry.aclose()

    async def _close_toolset(
        self,
        server_name: str,
        session_id: str,
        toolset: Any,
    ) -> None:
        try:
            await toolset.__aexit__(None, None, None)
        except Exception:
            logger.exception(
                "Failed to close MCP client for {!r} session {!r}",
                server_name,
                session_id,
            )
        else:
            logger.info(
                "Closed MCP client for {!r} session {!r}",
                server_name,
                session_id,
            )

    @staticmethod
    async def _safe_close(toolset: Any) -> None:
        try:
            await toolset.__aexit__(None, None, None)
        except Exception:
            logger.opt(exception=True).debug(
                "Ignored error while closing failed MCP connect attempt"
            )


def get_mcp_lease_manager() -> McpLeaseManager:
    from deep_agent.core.context import get_context

    return get_context().mcp_lease_manager
