from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import AbstractToolset, RunContext
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai_harness import CodeMode

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.toolset import HybridCodeModeToolset
from deep_agent.ptc.limits import (
    PTC_BUILTIN_METADATA_KEY, check_result_size, nested_collector,
)


class HybridCodeMode(CodeMode[AgentDeps]):
    """Keep authorized MCP tools native while also exposing them to Monty."""

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        collector = nested_collector(ctx, call)
        if collector is None:
            return await handler(args)
        import asyncio
        from pydantic_ai import ToolDenied

        collector.begin(call.tool_call_id, call.tool_name, args)
        try:
            result = await handler(args)
            self._collect_files(ctx, tool_def, result, collector)
            check_result_size(result)
        except asyncio.CancelledError:
            collector.end(call.tool_call_id, "interrupted", error="CancelledError")
            raise
        except Exception as exc:
            # Never persist arbitrary exception reprs or internal payloads.
            collector.end(call.tool_call_id, "failed", error={
                "type": type(exc).__name__, "message": str(exc)[:4096],
            })
            raise
        else:
            if isinstance(result, ToolDenied):
                collector.end(call.tool_call_id, "denied", error=result.message)
            else:
                from pydantic_ai.messages import ToolReturn
                value = result.return_value if isinstance(result, ToolReturn) else result
                collector.end(call.tool_call_id, "completed", value)
            return result

    @staticmethod
    def _collect_files(ctx, tool_def, result, collector):
        # This bit is set only by our builtin assembly wrapper. MCP wrappers
        # explicitly remove it, even if an upstream server forges metadata.
        if (tool_def.metadata or {}).get(PTC_BUILTIN_METADATA_KEY) is not True:
            return
        from pydantic_ai.messages import ToolReturn
        from deep_agent.agent.tool_output_limits import _generated_file_refs
        from deep_agent.files.export_schema import ExportFileResult

        refs = []
        if tool_def.name in {
            "execute_python", "execute_skill_script", "export_file_to_download_center",
        } and isinstance(result, ToolReturn):
            for ref in _generated_file_refs(result.metadata or {}):
                known = ctx.deps.session_file_artifacts.get(ref["file_id"])
                if known is not None:
                    refs.append(dict(known))
        elif tool_def.name == "export_file_to_download_center" and isinstance(result, ExportFileResult):
            # Republishing must preserve the original file identity and hash.
            known = ctx.deps.session_file_artifacts.get(result.file_id)
            if known is not None and known.get("kind") == "sandbox_output":
                refs.append(dict(known))
        for ref in refs:
            collector.generated_files[ref["file_id"]] = ref

    def _announce_newly_discovered(self, ctx: RunContext[AgentDeps], names: Sequence[str]) -> None:
        # Harness 0.26's local/native search hooks both route through this method.
        # Preserve its run-scoped de-duplication, changing only the announcement:
        # unlike code-only mode, discovery is not an instruction to enter Monty.
        fresh = [name for name in names if name not in self._announced_tools]
        if not fresh:
            return
        self._announced_tools.update(fresh)
        listing = ", ".join(f"`{name}`" for name in fresh)
        ctx.enqueue(SystemPromptPart(content=f"New tool schemas are available: {listing}."))

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
