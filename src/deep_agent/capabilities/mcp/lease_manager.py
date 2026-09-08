from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger

from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry
from deep_agent.options import OPTION

McpToolsetFactory = Callable[[str], Any]

_ACQUIRE_RETRY_DELAY_SECONDS = 0.5
# ensure_server 耗时 ≥ 这个阈值才记 INFO；主要用于追踪 spawn 卡顿。
_ENSURE_SERVER_SLOW_THRESHOLD_SECONDS = 5.0


class McpLeaseManager:
    """Worker-local MCP client coordinator for shared-HTTP builtin servers.

    每次 lease 打开一个短链 HTTP client，作用域退出时关闭。Pod 级别的子服务进程由
    :class:`McpSharedRegistry` 协调（registry touch + 进程内 TTL watcher）。
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
        connect_url = await self._ensure_server_with_retry(
            server_name,
            spawn_config,
            session_id=session_id,
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

    async def _ensure_server_with_retry(
        self,
        server_name: str,
        spawn_config: dict[str, Any] | None,
        *,
        session_id: str,
    ) -> str:
        """对 ensure_server 做瞬时锁竞争 retry。

        复用现有 ``mcp_acquire_retries``（默认 1）作为总尝试次数。
        预算耗尽仍按原行为抛 TimeoutError。
        """
        max_retries = max(0, self._mcp_acquire_retries)

        for attempt in range(max_retries + 1):
            t0 = time.monotonic()
            try:
                url = await self._shared_registry.ensure_server(
                    server_name,
                    spawn_config,
                )
            except TimeoutError:
                elapsed = time.monotonic() - t0
                if attempt >= max_retries:
                    logger.warning(
                        "MCP ensure_server exhausted retries for {!r} "
                        "session={!r} attempts={} last_attempt={:.2f}s",
                        server_name,
                        session_id,
                        max_retries + 1,
                        elapsed,
                    )
                    raise
                logger.warning(
                    "MCP ensure_server timeout for {!r} session={!r} "
                    "attempt={}/{} waited={:.2f}s; retrying in {:.2f}s",
                    server_name,
                    session_id,
                    attempt + 1,
                    max_retries + 1,
                    elapsed,
                    _ACQUIRE_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_ACQUIRE_RETRY_DELAY_SECONDS)
                continue

            elapsed = time.monotonic() - t0
            if elapsed >= _ENSURE_SERVER_SLOW_THRESHOLD_SECONDS or attempt > 0:
                logger.info(
                    "MCP ensure_server {!r} session={!r} attempt={}/{} took {:.2f}s url={}",
                    server_name,
                    session_id,
                    attempt + 1,
                    max_retries + 1,
                    elapsed,
                    url,
                )
            return url

        # 不可达：循环只通过内部 return/raise 退出；类型检查 + 防御。
        raise AssertionError("retry loop fell through")  # pragma: no cover

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
