from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext, ToolDefinition, messages
from pydantic_ai.exceptions import ModelRetry, RunCancelled, ToolFailed
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator

from deep_agent.agent.deps import AgentDeps
from deep_agent.mcp_request_context import resolve_mcp_headers
from deep_agent.schemas.mcp import McpToolSnapshot


_MCP_ARGS_VALIDATOR = SchemaValidator(
    {
        "type": "dict",
        "keys_schema": {"type": "str"},
        "values_schema": {"type": "any"},
    }
)


def _contains_cancellation(exc: BaseException) -> bool:
    if isinstance(exc, (RunCancelled, asyncio.CancelledError)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains_cancellation(item) for item in exc.exceptions)
    return False


@dataclass
class SnapshotMcpToolset(AbstractToolset[AgentDeps]):
    """Expose an inspected URL MCP catalog and connect only on the first call."""

    server_name: str
    config: dict[str, Any]
    snapshot: McpToolSnapshot
    init_timeout: float
    max_retries: int
    request_headers: dict[str, str] | None = None
    _inner: MCPToolset[AgentDeps] | None = field(default=None, init=False, repr=False)
    _exit_stack: AsyncExitStack | None = field(default=None, init=False, repr=False)
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _connection_failure: str | None = field(default=None, init=False, repr=False)

    @property
    def id(self) -> str:
        return f"mcp-snapshot:{self.server_name}"

    async def for_run(self, ctx: RunContext[AgentDeps]) -> SnapshotMcpToolset:
        return SnapshotMcpToolset(
            server_name=self.server_name,
            config=self.config,
            snapshot=self.snapshot,
            init_timeout=self.init_timeout,
            max_retries=self.max_retries,
            request_headers=dict(ctx.deps.request_headers) or None,
        )

    async def __aexit__(self, *args: Any) -> bool | None:
        await self._close_connection()
        return None

    async def get_instructions(
        self,
        ctx: RunContext[AgentDeps],
    ) -> messages.InstructionPart | None:
        del ctx
        if not self.snapshot.server_instructions:
            return None
        return messages.InstructionPart(
            content=self.snapshot.server_instructions,
            dynamic=False,
        )

    async def get_tools(
        self,
        ctx: RunContext[AgentDeps],
    ) -> dict[str, ToolsetTool[AgentDeps]]:
        tools: dict[str, ToolsetTool[AgentDeps]] = {}
        for item in self.snapshot.definitions:
            tool_def = ToolDefinition(
                name=item.name,
                description=item.description,
                parameters_json_schema=item.input_schema,
                return_schema=item.output_schema,
                metadata={
                    "annotations": item.annotations,
                    "task": item.task_support in {"optional", "required"},
                },
            )
            tools[item.name] = ToolsetTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=self.max_retries if self.max_retries is not None else ctx.max_retries,
                args_validator=_MCP_ARGS_VALIDATOR,
            )
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        if self._connection_failure is not None:
            raise ToolFailed(self._connection_failure)

        try:
            inner = await self._ensure_connection()
            return await inner.call_tool(name, tool_args, ctx, tool)
        except BaseExceptionGroup as exc:
            if _contains_cancellation(exc):
                raise
            await self._fail_connection(exc)
        except (RunCancelled, asyncio.CancelledError):
            raise
        except (ModelRetry, ToolFailed):
            raise
        except Exception as exc:
            await self._fail_connection(exc)

        raise AssertionError("unreachable")

    async def _ensure_connection(self) -> MCPToolset[AgentDeps]:
        if self._inner is not None:
            return self._inner
        async with self._connect_lock:
            if self._inner is not None:
                return self._inner
            if self._connection_failure is not None:
                raise ToolFailed(self._connection_failure)

            headers = resolve_mcp_headers(
                "url",
                self.config,
                self.request_headers,
            )
            inner = MCPToolset(
                self.config["url"],
                id=self.server_name,
                headers=headers,
                include_instructions=False,
                init_timeout=self.init_timeout,
                max_retries=self.max_retries,
            )
            stack = AsyncExitStack()
            try:
                await stack.enter_async_context(inner)
            except BaseException:
                await stack.aclose()
                raise
            self._inner = inner
            self._exit_stack = stack
            return inner

    async def _close_connection(self) -> None:
        stack, self._exit_stack = self._exit_stack, None
        self._inner = None
        if stack is not None:
            await stack.aclose()

    async def _fail_connection(self, exc: BaseException) -> None:
        await self._close_connection()
        error_type = type(exc).__name__
        self._connection_failure = (
            f"MCP server {self.server_name!r} is temporarily unavailable ({error_type})"
        )
        raise ToolFailed(self._connection_failure) from exc
