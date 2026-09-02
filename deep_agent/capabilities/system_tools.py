from __future__ import annotations

from deep_agent.schemas.system_tool import SystemToolDescription, SystemToolDomain

__all__ = ["list_system_tool_domains"]


_SYSTEM_TOOL_DOMAINS = (
    SystemToolDomain(
        code="runtime:planning",
        label="任务规划",
        description="由模型按需创建和维护当前单轮任务的执行计划。",
        tools=(
            SystemToolDescription(
                name="write_plan",
                display_name="任务计划",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入；复杂任务由模型自行决定是否调用",
                description="创建或整体替换当前Run的有序任务计划。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:conversation-history",
        label="会话历史",
        description="搜索并精确读取当前用户和 Agent 权限范围内的历史事件。",
        tools=(
            SystemToolDescription(
                name="search_conversation_history",
                display_name="搜索会话历史",
                source="DeepAgent",
                injection_condition="所有已鉴权 Agent Run 自动注入",
                description="支持中文、路径、工具名和 Call ID 的当前或跨会话历史搜索。",
            ),
            SystemToolDescription(
                name="read_conversation_event",
                display_name="读取会话事件",
                source="DeepAgent",
                injection_condition="所有已鉴权 Agent Run 自动注入",
                description="按 Event ID 或当前会话 Call ID 精确读取原始事件证据。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:skill",
        label="Skill",
        description="按需加载 Agent 已授权的 Skill 说明和资源。",
        tools=(
            SystemToolDescription(
                name="load_capability",
                display_name="加载 Skill",
                source="Pydantic AI Harness",
                injection_condition="Agent 已配置至少一个当前可用的 Skill",
                description="按需加载一个已授权 Skill 的完整 SKILL.md 说明。",
            ),
            SystemToolDescription(
                name="list_skill_resources",
                display_name="查看 Skill 资源",
                source="DeepAgent",
                injection_condition="Agent 已配置至少一个含 Reference 或 Asset 的可用 Skill",
                description="列出已授权 Skill 可供模型读取的 Reference 和 Asset。",
            ),
            SystemToolDescription(
                name="execute_skill_script",
                display_name="执行 Skill 脚本",
                source="DeepAgent",
                injection_condition="Agent 已配置含脚本 Skill并启用 Python 沙箱",
                description="使用当前 Python 沙箱执行已授权 Skill 的固定脚本。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:file",
        label="文件",
        description="按需读取文件，或将用户需要的文件发布到DeepFOS下载中心。",
        tools=(
            SystemToolDescription(
                name="read_file",
                display_name="读取文件",
                source="DeepAgent",
                injection_condition="当前会话存在可读附件，或 Agent 已配置含可读资源的 Skill",
                description="统一分页读取文本、Word 和 Excel 文件。",
            ),
            SystemToolDescription(
                name="export_file_to_download_center",
                display_name="发布到下载中心",
                source="DeepAgent",
                injection_condition="当前请求具备用户、应用和空间上下文",
                description="将新生成的文本文件或当前会话平台文件发布到下载中心。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:tool-result",
        label="工具结果",
        description="按需读取已外置到平台文件服务的完整工具结果。",
        tools=(
            SystemToolDescription(
                name="read_tool_result",
                display_name="读取完整工具结果",
                source="Pydantic AI Harness",
                injection_condition="Agent Run启用大工具结果治理",
                description="按行分页或关键词读取本会话已外置的完整工具结果。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:mcp-tool-search",
        label="MCP 工具发现",
        description="在已授权但延迟披露的 MCP 工具中查找当前任务需要的工具。",
        tools=(
            SystemToolDescription(
                name="search_tools",
                display_name="查找可用工具",
                source="Pydantic AI",
                injection_condition="已授权 MCP 存在尚未完整披露的工具",
                description="搜索延迟披露的 MCP 工具，并在后续模型请求中加载命中工具。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:mcp-resource",
        label="MCP Resource",
        description="按需访问已授权 URL MCP 提供的 Resource 和 Resource Template。",
        tools=(
            SystemToolDescription(
                name="list_mcp_resources",
                display_name="查看 MCP 资源",
                source="DeepAgent",
                injection_condition="Agent 已选择检测到 Resource 能力的可用 URL MCP",
                description="列出指定 MCP Server 当前提供的 Resource。",
            ),
            SystemToolDescription(
                name="list_mcp_resource_templates",
                display_name="查看 MCP 资源模板",
                source="DeepAgent",
                injection_condition="Agent 已选择检测到 Resource 能力的可用 URL MCP",
                description="列出指定 MCP Server 当前提供的 Resource Template。",
            ),
            SystemToolDescription(
                name="read_mcp_resource",
                display_name="读取 MCP 资源",
                source="DeepAgent",
                injection_condition="Agent 已选择检测到 Resource 能力的可用 URL MCP",
                description="读取指定 MCP Server 中的 Resource 内容。",
            ),
        ),
    ),
)


def list_system_tool_domains() -> tuple[SystemToolDomain, ...]:
    """Return the ordered, code-owned runtime system-tool catalog."""
    return _SYSTEM_TOOL_DOMAINS
