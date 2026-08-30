from __future__ import annotations

from pydantic_ai.toolsets import FunctionToolset

from deep_agent.capabilities.registry import ToolInfo, ToolPackage
from deep_agent.web.settings import WEB_PACKAGE_CODE


def build_web_tool_package() -> ToolPackage:
    """Return static product metadata; the concrete Toolset is request-scoped."""
    return ToolPackage(
        code=WEB_PACKAGE_CODE,
        category="builtin",
        label="Web",
        source="builtin",
        description="搜索公开互联网并安全抓取公开网页。",
        toolset=FunctionToolset([]),
        tools=[
            ToolInfo(
                name="web_search",
                display_name="Web Search",
                description="搜索公开互联网并返回来源。",
            ),
            ToolInfo(
                name="web_fetch",
                display_name="Web Fetch",
                description="抓取公开 HTTP(S) 网页并转换为受限 Markdown。",
            ),
        ],
    )
