from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.toolsets.abstract import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.prefixed import PrefixedToolset
from typing_extensions import Self

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.request_context import apply_request_headers_to_stdio_config
from deep_agent.capabilities.mcp.session_pool import McpSessionPool, get_mcp_session_pool
from deep_agent.db.session_scope import runtime_session_key


@dataclass
class SessionScopedMcpToolset(AbstractToolset[AgentDeps]):
    """Session-scoped wrapper around a stdio MCP toolset.

    ``async with agent`` enters the registry copy without a session id (no-op).
    Each agent run binds ``ctx.deps.session_id`` and ``ctx.deps.request_headers``
    via ``for_run``. The first run that starts the pooled subprocess supplies
    headers for :data:`~deep_agent.lib.const.REQUEST_HEADERS_ENV`.
    """

    server_name: str
    config: dict[str, Any]
    pool: McpSessionPool = field(default_factory=get_mcp_session_pool)
    _session_id: str | None = field(default=None, repr=False, compare=False)
    _request_headers: Mapping[str, str] | None = field(default=None, repr=False, compare=False)
    _inner: AbstractToolset[AgentDeps] | None = field(default=None, repr=False, compare=False)

    @property
    def id(self) -> str | None:
        return f"mcp-session:{self.server_name}"

    def prefixed(self, prefix: str) -> PrefixedToolset[AgentDeps]:
        return PrefixedToolset(self, prefix)

    async def for_run(self, ctx: RunContext[AgentDeps]) -> AbstractToolset[AgentDeps]:
        request_headers = dict(ctx.deps.request_headers) if ctx.deps.request_headers else None
        pool_key = runtime_session_key(ctx.deps.session_id, agent_id=ctx.deps.agent_id)
        return replace(
            self,
            _session_id=pool_key,
            _request_headers=request_headers,
            _inner=None,
        )

    async def __aenter__(self) -> Self:
        if self._session_id is None:
            return self
        self._inner = await self.pool.acquire(
            self.server_name,
            self._session_id,
            self._build_inner_toolset,
        )
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if self._session_id is None:
            return None
        await self.pool.release_run(self.server_name, self._session_id)
        self._inner = None
        return None

    def _build_inner_toolset(self) -> AbstractToolset[AgentDeps]:
        from deep_agent.capabilities.mcp.toolset_builder import build_raw_stdio_mcp_toolset

        spawn_config = apply_request_headers_to_stdio_config(self.config, self._request_headers)
        return build_raw_stdio_mcp_toolset(self.server_name, spawn_config)

    async def get_instructions(
        self, ctx: RunContext[AgentDeps]
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        if self._inner is None:
            return None
        return await self._inner.get_instructions(ctx)

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        if self._inner is None:
            return {}
        return await self._inner.get_tools(ctx)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        if self._inner is None:
            raise RuntimeError("Session-scoped MCP toolset is not active for this run")
        try:
            return await self._inner.call_tool(name, tool_args, ctx, tool)
        except Exception as exc:
            if self._session_id is None or not is_recoverable_mcp_disconnect(exc):
                raise

            # Stdio MCP subprocess may die between runs; reset pooled entry and retry once.
            await self.pool.reset_entry(self.server_name, self._session_id)
            self._inner = await self.pool.acquire(
                self.server_name,
                self._session_id,
                self._build_inner_toolset,
            )
            return await self._inner.call_tool(name, tool_args, ctx, tool)
