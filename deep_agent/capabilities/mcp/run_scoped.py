from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.toolsets.abstract import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.prefixed import PrefixedToolset
from typing_extensions import Self

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.errors import is_recoverable_mcp_disconnect
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager, get_mcp_lease_manager
from deep_agent.capabilities.mcp.request_context import merge_mcp_headers
from deep_agent.db.session_scope import runtime_session_key


@dataclass
class RunScopedMcpToolset(AbstractToolset[AgentDeps]):
    """MCP toolset activated per pydantic_ai run.

    Builtin servers are launched once per pod as shared HTTP processes.
    Each run opens a short-lived HTTP client; the shared server is kept alive
    via registry touch while any worker holds an active lease.
    """

    server_name: str
    config: dict[str, Any]
    lease_manager: McpLeaseManager | None = field(default=None, repr=False, compare=False)
    _session_id: str | None = field(default=None, repr=False, compare=False)
    _request_headers: Mapping[str, str] | None = field(default=None, repr=False, compare=False)
    _inner: AbstractToolset[AgentDeps] | None = field(default=None, repr=False, compare=False)
    _exit_stack: AsyncExitStack | None = field(default=None, repr=False, compare=False)

    @property
    def id(self) -> str | None:
        return f"mcp-run:{self.server_name}"

    def prefixed(self, prefix: str) -> PrefixedToolset[AgentDeps]:
        return PrefixedToolset(self, prefix)

    async def for_run(self, ctx: RunContext[AgentDeps]) -> AbstractToolset[AgentDeps]:
        request_headers = dict(ctx.deps.request_headers or {})
        request_headers["x-session-id"] = ctx.deps.session_id
        pool_key = runtime_session_key(ctx.deps.session_id, agent_id=ctx.deps.agent_id)
        return replace(
            self,
            _session_id=pool_key,
            _request_headers=request_headers,
            _inner=None,
            _exit_stack=None,
        )

    def _build_inner_toolset(self, connect_url: str) -> AbstractToolset[AgentDeps]:
        from deep_agent.capabilities.mcp.toolset_builder import build_raw_url_mcp_toolset

        static_headers = dict(self.config.get("headers") or {})
        request_headers = dict(self._request_headers) if self._request_headers else None
        merged = merge_mcp_headers(static_headers, request_headers) or {}
        return build_raw_url_mcp_toolset(
            self.server_name,
            connect_url,
            static_headers=merged,
            init_timeout=self.config.get("init_timeout"),
        )

    def _lease_manager(self) -> McpLeaseManager:
        return (
            self.lease_manager
            if self.lease_manager is not None
            else get_mcp_lease_manager()
        )

    async def __aenter__(self) -> Self:
        if self._session_id is None:
            return self
        self._exit_stack = AsyncExitStack()
        self._inner = await self._exit_stack.enter_async_context(
            self._lease_manager().lease(
                self.server_name,
                self._session_id,
                self._build_inner_toolset,
                spawn_config=dict(self.config),
            )
        )
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if self._session_id is None:
            return None
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._inner = None
        return None

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
            raise RuntimeError("Run-scoped MCP toolset is not active for this run")

        for attempt in range(2):
            try:
                return await self._inner.call_tool(name, tool_args, ctx, tool)
            except Exception as exc:
                if (
                    attempt == 0
                    and self._session_id is not None
                    and is_recoverable_mcp_disconnect(exc)
                ):
                    if self._exit_stack is not None:
                        await self._exit_stack.aclose()
                    self._exit_stack = AsyncExitStack()
                    self._inner = await self._exit_stack.enter_async_context(
                        self._lease_manager().lease(
                            self.server_name,
                            self._session_id,
                            self._build_inner_toolset,
                            spawn_config=dict(self.config),
                        )
                    )
                    continue
                raise
