from __future__ import annotations

from pydantic_ai import AbstractToolset
from pydantic_ai_harness import CodeMode

from deep_agent.agent.deps import AgentDeps
from deep_agent.code_mode.toolset import HybridCodeModeToolset


class HybridCodeMode(CodeMode[AgentDeps]):
    """Keep authorized MCP tools native while also exposing them to Monty."""

    def get_wrapper_toolset(
        self,
        toolset: AbstractToolset[AgentDeps],
    ) -> AbstractToolset[AgentDeps] | None:
        return HybridCodeModeToolset(
            wrapped=toolset,
            tool_selector=self.tools,
            max_retries=self.max_retries,
            max_tool_calls=self.max_tool_calls,
            resource_limits=self.resource_limits,
            dynamic_catalog=self.dynamic_catalog,
            os_access=self.os_access,
            mount=self.mount,
        )
