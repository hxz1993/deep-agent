from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.limits import PTC_BUILTIN_METADATA_KEY
from deep_agent.ptc.file_payload import (
    CODE_MODE_HYBRID,
    CODE_MODE_METADATA_KEY,
    CODE_MODE_ORIGINAL_NAME_KEY,
    CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY,
)

@dataclass(kw_only=True)
class _BuiltinTool(ToolsetTool[AgentDeps]):
    source_tool: ToolsetTool[AgentDeps]


@dataclass
class BuiltinPtcToolset(WrapperToolset[AgentDeps]):
    """Expose exact tools from one trusted host toolset through both call routes.

    Install only at the trusted builtin assembly sites, never around the combined
    catalog: an unrelated MCP or UI tool must not gain access by sharing a name.
    The wrapped toolset continues to prepare definitions and enforce permissions.
    """

    names: frozenset[str] = frozenset()

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        tools = await self.wrapped.get_tools(ctx)
        result = {}
        for name, tool in tools.items():
            if name not in self.names:
                result[name] = tool
                continue
            metadata = dict(tool.tool_def.metadata or {})
            metadata[CODE_MODE_METADATA_KEY] = CODE_MODE_HYBRID
            metadata[CODE_MODE_ORIGINAL_NAME_KEY] = name
            metadata[CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY] = False
            metadata[PTC_BUILTIN_METADATA_KEY] = True
            result[name] = _BuiltinTool(
                toolset=self,
                tool_def=replace(tool.tool_def, metadata=metadata),
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source_tool=tool,
            )
        return result

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        source = tool.source_tool if isinstance(tool, _BuiltinTool) else tool
        return await self.wrapped.call_tool(name, tool_args, ctx, source)
