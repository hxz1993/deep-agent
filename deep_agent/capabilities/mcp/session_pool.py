from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry
from deep_agent.options import OPTION

McpToolsetFactory = Callable[[str], Any]

_ACQUIRE_RETRY_DELAY_SECONDS = 0.5


@dataclass
class _PoolEntry:
    toolset: Any
    run_refs: int = 0


class McpSessionPool:
    """Worker-local MCP client coordinator for shared-HTTP builtin servers.

    HTTP clients are opened per lease and closed when the last concurrent user
    releases. Pod-wide server processes are coordinated via
    :class:`McpSharedRegistry` (registry touch + in-server TTL watcher).
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
        self._entries: dict[tuple[str, str], _PoolEntry] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
        *,
        spawn_config: dict[str, Any] | None = None,
    ) -> Any:
        key = (server_name, session_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.run_refs += 1
                return entry.toolset

        connect_url = await self._shared_registry.ensure_server(
            server_name,
            spawn_config,
        )
        await self._shared_registry.start_touch(server_name)

        try:
            toolset = await self._connect_with_retry(
                server_name,
                session_id,
                factory,
                connect_url=connect_url,
            )
        except Exception:
            await self._shared_registry.stop_touch(server_name)
            raise

        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                await self._safe_close(toolset)
                await self._shared_registry.stop_touch(server_name)
                entry.run_refs += 1
                return entry.toolset

            entry = _PoolEntry(toolset=toolset, run_refs=1)
            self._entries[key] = entry
            logger.info(
                "Connected MCP client for {!r} session {!r} at {}",
                server_name,
                session_id,
                connect_url,
            )
            return entry.toolset

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

    async def release(self, server_name: str, session_id: str) -> None:
        key = (server_name, session_id)
        entry: _PoolEntry | None = None
        async with self._lock:
            active = self._entries.get(key)
            if active is None:
                return
            active.run_refs -= 1
            if active.run_refs <= 0:
                entry = self._entries.pop(key)

        if entry is not None:
            await self._close_toolset(server_name, session_id, entry.toolset)
            await self._shared_registry.stop_touch(server_name)

    @contextlib.asynccontextmanager
    async def lease(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
        *,
        spawn_config: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        key = (server_name, session_id)
        evict = False
        toolset = await self.acquire(
            server_name,
            session_id,
            factory,
            spawn_config=spawn_config,
        )
        try:
            yield toolset
        except Exception as exc:
            if is_recoverable_mcp_disconnect(exc):
                evict = True
            raise
        finally:
            if evict:
                await self._evict_key(key)
            else:
                await self.release(server_name, session_id)

    async def _evict_key(self, key: tuple[str, str]) -> None:
        server_name, session_id = key
        entry: _PoolEntry | None = None
        async with self._lock:
            active = self._entries.get(key)
            if active is not None:
                active.run_refs = max(0, active.run_refs - 1)
                if active.run_refs <= 0:
                    entry = self._entries.pop(key)
        if entry is not None:
            await self._close_toolset(server_name, session_id, entry.toolset)
            await self._shared_registry.stop_touch(server_name)

    async def close_all(self) -> None:
        keys = list(self._entries)
        for server_name, session_id in keys:
            entry: _PoolEntry | None = None
            async with self._lock:
                entry = self._entries.pop((server_name, session_id), None)
            if entry is not None:
                await self._close_toolset(server_name, session_id, entry.toolset)
                await self._shared_registry.stop_touch(server_name)
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


def get_mcp_session_pool() -> McpSessionPool:
    from deep_agent.core.context import get_context

    return get_context().mcp_session_pool
