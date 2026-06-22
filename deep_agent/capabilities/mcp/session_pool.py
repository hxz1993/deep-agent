from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.core.config import get_settings
from deep_agent.core.logging import get_logger

logger = get_logger(__name__)

McpToolsetFactory = Callable[[], Any]

_ACQUIRE_RETRY_DELAY_SECONDS = 0.5


@dataclass
class _SessionMcpEntry:
    toolset: Any
    run_refs: int = 0
    last_used: float = field(default_factory=time.monotonic)


class McpSessionPool:
    """Keep stdio MCP subprocesses alive for the lifetime of a chat session.

    Agent runs acquire a pooled toolset on enter and release on exit without
    tearing down the subprocess. The subprocess is destroyed when the session
    is deleted or the pool is shut down.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _SessionMcpEntry] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
    ) -> Any:
        key = (server_name, session_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.run_refs += 1
                entry.last_used = time.monotonic()
                return entry.toolset

        toolset = await self._connect_with_retry(server_name, session_id, factory)

        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                await self._safe_close(toolset)
                entry.run_refs += 1
                entry.last_used = time.monotonic()
                return entry.toolset

            entry = _SessionMcpEntry(toolset=toolset)
            self._entries[key] = entry
            entry.run_refs += 1
            entry.last_used = time.monotonic()
            logger.info(
                "Started MCP server %r for session %r",
                server_name,
                session_id,
            )
            return entry.toolset

    async def _connect_with_retry(
        self,
        server_name: str,
        session_id: str,
        factory: McpToolsetFactory,
    ) -> Any:
        settings = get_settings()
        max_retries = max(0, settings.mcp_acquire_retries)
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            toolset = factory()
            try:
                await toolset.__aenter__()
            except Exception as exc:
                last_exc = exc
                await self._safe_close(toolset)
                if attempt >= max_retries or not is_recoverable_mcp_disconnect(exc):
                    raise
                logger.warning(
                    "MCP connect failed for %r session %r (attempt %d/%d): %s",
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
                    "MCP server %r connected for session %r after retry %d",
                    server_name,
                    session_id,
                    attempt,
                )
            return toolset

        assert last_exc is not None
        raise last_exc

    async def release_run(self, server_name: str, session_id: str) -> None:
        key = (server_name, session_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.run_refs = max(0, entry.run_refs - 1)
            entry.last_used = time.monotonic()

    async def reset_entry(self, server_name: str, session_id: str) -> None:
        """Force-close one pooled stdio MCP subprocess entry."""
        await self._close_key((server_name, session_id))

    async def close_session(self, session_id: str) -> None:
        keys = [key for key in self._entries if key[1] == session_id]
        for key in keys:
            await self._close_key(key)

    async def close_all(self) -> None:
        keys = list(self._entries)
        for key in keys:
            await self._close_key(key)

    async def _close_key(self, key: tuple[str, str]) -> None:
        async with self._lock:
            entry = self._entries.pop(key, None)
        if entry is None:
            return
        try:
            await entry.toolset.__aexit__(None, None, None)
        except Exception:
            logger.exception("Failed to close MCP server %r for session %r", key[0], key[1])
        else:
            logger.info("Closed MCP server %r for session %r", key[0], key[1])

    @staticmethod
    async def _safe_close(toolset: Any) -> None:
        try:
            await toolset.__aexit__(None, None, None)
        except Exception:
            logger.debug("Ignored error while closing failed MCP connect attempt", exc_info=True)


def get_mcp_session_pool() -> McpSessionPool:
    from deep_agent.core.context import get_context

    return get_context().mcp_session_pool
