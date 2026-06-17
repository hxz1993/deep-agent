from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.toolsets.abstract import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.prefixed import PrefixedToolset
from typing_extensions import Self

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.session_pool import McpSessionPool, get_mcp_session_pool


@dataclass
class SessionScopedMcpToolset(AbstractToolset[AgentDeps]):
    """Session-scoped wrapper around a stdio MCP toolset.

    ``async with agent`` enters the registry copy without a session id (no-op).
    Each agent run binds ``ctx.deps.session_id`` via ``for_run`` and reuses the
    same subprocess for that session across multiple messages.
    """

    server_name: str
    config: dict[str, Any]
    pool: McpSessionPool = field(default_factory=get_mcp_session_pool)
    _session_id: str | None = field(default=None, repr=False, compare=False)
    _inner: AbstractToolset[AgentDeps] | None = field(default=None, repr=False, compare=False)

    @property
    def id(self) -> str | None:
        return f"mcp-session:{self.server_name}"

    def prefixed(self, prefix: str) -> PrefixedToolset[AgentDeps]:
        return PrefixedToolset(self, prefix)

    async def for_run(self, ctx: RunContext[AgentDeps]) -> AbstractToolset[AgentDeps]:
        return replace(self, _session_id=ctx.deps.session_id, _inner=None)

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

        return build_raw_stdio_mcp_toolset(self.server_name, self.config)

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
