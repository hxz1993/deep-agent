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
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入；复杂任务由模型自行决定是否调用",
                description="创建或整体替换当前Run的有序任务计划。",
            ),
            SystemToolDescription(
                name="read_plan",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入",
                description="读取当前Run的计划步骤、稳定ID和完成进度。",
            ),
            SystemToolDescription(
                name="add_task",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入",
                description="向当前计划追加一个待处理步骤。",
            ),
            SystemToolDescription(
                name="update_task_status",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入",
                description="按步骤ID更新一个任务的状态。",
            ),
            SystemToolDescription(
                name="update_task_statuses",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入",
                description="以原子批次更新多个任务状态。",
            ),
            SystemToolDescription(
                name="remove_task",
                source="Pydantic AI Harness",
                injection_condition="所有 Agent Run自动注入",
                description="删除不再需要或误建的计划步骤。",
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
                source="Pydantic AI Harness",
                injection_condition="Agent 已配置至少一个当前可用的 Skill",
                description="按需加载一个已授权 Skill 的完整 SKILL.md 说明。",
            ),
            SystemToolDescription(
                name="list_skill_resources",
                source="DeepAgent",
                injection_condition="Agent 已配置至少一个含 Reference 或 Asset 的可用 Skill",
                description="列出已授权 Skill 可供模型读取的 Reference 和 Asset。",
            ),
            SystemToolDescription(
                name="execute_skill_script",
                source="DeepAgent",
                injection_condition="Agent 已配置含脚本 Skill并启用 Python 沙箱",
                description="使用当前 Python 沙箱执行已授权 Skill 的固定脚本。",
            ),
        ),
    ),
    SystemToolDomain(
        code="runtime:file",
        label="文件",
        description="按需读取当前会话附件或 Agent 已授权 Skill 中的文件。",
        tools=(
            SystemToolDescription(
                name="read_file",
                source="DeepAgent",
                injection_condition="当前会话存在可读附件，或 Agent 已配置含可读资源的 Skill",
                description="统一分页读取文本、Word 和 Excel 文件。",
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
                source="DeepAgent",
                injection_condition="Agent 已选择检测到 Resource 能力的可用 URL MCP",
                description="列出指定 MCP Server 当前提供的 Resource。",
            ),
            SystemToolDescription(
                name="list_mcp_resource_templates",
                source="DeepAgent",
                injection_condition="Agent 已选择检测到 Resource 能力的可用 URL MCP",
                description="列出指定 MCP Server 当前提供的 Resource Template。",
            ),
            SystemToolDescription(
                name="read_mcp_resource",
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
