from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from deep_agent.core.logging import get_logger

logger = get_logger(__name__)

McpToolsetFactory = Callable[[], Any]


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
            if entry is None:
                toolset = factory()
                await toolset.__aenter__()
                entry = _SessionMcpEntry(toolset=toolset)
                self._entries[key] = entry
                logger.info(
                    "Started MCP server %r for session %r",
                    server_name,
                    session_id,
                )
            entry.run_refs += 1
            entry.last_used = time.monotonic()
            return entry.toolset

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


_pool: McpSessionPool | None = None


def get_mcp_session_pool() -> McpSessionPool:
    global _pool
    if _pool is None:
        _pool = McpSessionPool()
    return _pool


def set_mcp_session_pool(pool: McpSessionPool) -> None:
    global _pool
    _pool = pool


def reset_mcp_session_pool() -> None:
    global _pool
    _pool = None
