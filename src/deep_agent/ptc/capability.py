from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import AbstractToolset, RunContext
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai_harness import CodeMode

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.toolset import DISCOVERY_POLICY, HybridCodeModeToolset


class HybridCodeMode(CodeMode[AgentDeps]):
    """Keep authorized MCP tools native while also exposing them to Monty."""

    def _announce_newly_discovered(self, ctx: RunContext[AgentDeps], names: Sequence[str]) -> None:
        # Harness 0.26's local/native search hooks both route through this method.
        # Preserve its run-scoped de-duplication, changing only the announcement:
        # unlike code-only mode, discovery is not an instruction to enter Monty.
        fresh = [name for name in names if name not in self._announced_tools]
        if not fresh:
            return
        self._announced_tools.update(fresh)
        listing = ", ".join(f"`{name}`" for name in fresh)
        ctx.enqueue(SystemPromptPart(content=f"New tool schemas are available: {listing}. {DISCOVERY_POLICY}"))

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
