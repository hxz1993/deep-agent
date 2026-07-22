# 设计

本目录保存方案、技术审计、RFC、待实施计划和交互原型。这里的内容用于评审和后续开发，不应直接当作当前产品说明。

| 文档 | 状态与用途 |
| --- | --- |
| [`pydantic-ai-native-capabilities-and-deepagent-usage-audit.md`](pydantic-ai-native-capabilities-and-deepagent-usage-audit.md) | 技术审计；评估 Pydantic AI / Harness 能力采用路线 |
| [`ui-tools-extension-proposals.md`](ui-tools-extension-proposals.md) | 历史方案对比；记录 UI 工具扩展的设计背景 |
| [`rfc/0001-ui-tools-mcp-discovery-and-api.md`](rfc/0001-ui-tools-mcp-discovery-and-api.md) | 已实现机制的原始 RFC 设计 |
| [`ui-tools-mcp-discovery-sync-refactor-plan.md`](ui-tools-mcp-discovery-sync-refactor-plan.md) | **待实施计划**；收缩 MCP UI 工具自动发现和刷新机制 |
| [`prototypes/`](prototypes/README.md) | 控制台等交互原型 |

关于 MCP UI 工具的后续调整，优先看 `ui-tools-mcp-discovery-sync-refactor-plan.md`。该计划的核心不是立即删除所有 UI 工具，而是让聊天运行时停止按 TTL 主动发现，收敛为注册时同步和手工同步；是否彻底删除 MCP UI 工具同步能力仍需单独确认。
