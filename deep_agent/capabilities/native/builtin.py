from __future__ import annotations

from datetime import datetime, timezone

from pydantic_ai import FunctionToolset

from deep_agent.capabilities.registry import Capability, ToolInfo


def build_native_capability() -> Capability:
    """Build the built-in native toolset.

    To add a native tool: define a function below and decorate it. Its type
    hints become the tool's JSON schema automatically. No other file changes.
    """
    toolset = FunctionToolset(
        instructions="需要确认当前时间时，可调用 get_current_time 获取 UTC 时间。"
    )

    @toolset.tool_plain
    def get_current_time() -> str:
        """Return the current date and time in UTC as an ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()

    return Capability(
        name="native",
        kind="native",
        toolset=toolset,
        label="通用工具",
        source="builtin",
        description="随 DeepAgent 发布的进程内通用工具。",
        tools=[
            ToolInfo("get_current_time", "获取当前 UTC 时间。"),
        ],
    )
