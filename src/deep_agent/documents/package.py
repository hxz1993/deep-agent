from __future__ import annotations

from pydantic_ai import FunctionToolset

from deep_agent.capabilities.registry import ToolInfo, ToolPackage

from .settings import DOCUMENT_PACKAGE_CODE


def build_document_tool_package() -> ToolPackage:
    return ToolPackage(
        code=DOCUMENT_PACKAGE_CODE, category="builtin", label="文档解析", source="builtin",
        description="通过配置的供应商解析扫描件、复杂版面和表格；按 Token 预算返回并支持当前 Run 续读。",
        toolset=FunctionToolset([]),
        tools=[ToolInfo(name="read_document", display_name="读取文档", description="专业文档解析；首次解析，后续从当前 Run 缓存读取。")],
    )
