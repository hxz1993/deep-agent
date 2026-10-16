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
                display_name="网络搜索",
                description="搜索公开互联网并返回来源。",
            ),
            ToolInfo(
                name="web_fetch",
                display_name="获取网页内容",
                description="按全局抓取配置获取公开 HTTP(S) 网页正文及来源；图片链接需另行查看。",
            ),
            ToolInfo(
                name="web_image_view",
                display_name="查看网络图片",
                description="让视觉模型读取公开 HTTP(S) 图片。",
            ),
        ],
    )
