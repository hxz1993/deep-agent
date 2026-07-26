# 设计

本目录保存方案、技术审计、RFC、待实施计划和交互原型。这里的内容用于评审和后续开发，不应直接当作当前产品说明。

| 文档 | 状态与用途 |
| --- | --- |
| [`pydantic-ai-native-capabilities-and-deepagent-usage-audit.md`](pydantic-ai-native-capabilities-and-deepagent-usage-audit.md) | 技术审计；评估 Pydantic AI / Harness 能力采用路线 |
| [`context-compaction-implementation-plan.md`](context-compaction-implementation-plan.md) | 待评审方案；基于 Harness 0.10 实测设计 DeepAgent 上下文压缩 |
| [`ui-tools-extension-proposals.md`](ui-tools-extension-proposals.md) | **已废弃历史方案**；MCP UI 发现未继续采用 |
| [`rfc/0001-ui-tools-mcp-discovery-and-api.md`](rfc/0001-ui-tools-mcp-discovery-and-api.md) | **已废弃 RFC**；相关机制在数据库版本 `019` 移除 |
| [`ui-tools-mcp-discovery-sync-refactor-plan.md`](ui-tools-mcp-discovery-sync-refactor-plan.md) | **已完成并被替代**；最终方案是彻底移除发现与同步 |
| [`prototypes/`](prototypes/README.md) | 控制台等交互原型 |

当前行为以 [`../guides/ui-tools-external.md`](../guides/ui-tools-external.md) 和 [`../guides/mcp-resources.md`](../guides/mcp-resources.md) 为准。
