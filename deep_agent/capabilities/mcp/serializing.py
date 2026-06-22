from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from deep_agent.agent.deps import AgentDeps


@dataclass
class SerializingToolset(WrapperToolset[AgentDeps]):
    """Serialize ``call_tool`` on one stdio MCP connection.

    Pydantic AI may invoke multiple tools concurrently; stdio JSON-RPC is not
    safe for overlapping requests on a single subprocess pipe.
    """

    _call_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        async with self._call_lock:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
