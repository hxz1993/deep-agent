# 设计课题

本目录按课题首次研究时间排序。编号越大，表示课题越新；每个课题目录统一收纳方案、
技术审计、RFC 和相关交互原型，避免不同阶段的材料混在同一层级。

| 序号 | 课题 | 时间 | 内容与状态 |
| --- | --- | --- | --- |
| `01` | [`UI 工具`](01-ui-tools/README.md) | 2026-06 起 | UI 工具扩展、MCP 发现 RFC 和后续废弃决策 |
| `02` | [`运维控制台`](02-operations-console/README.md) | 2026-07 起 | DeepAgent 一体化控制台交互原型 |
| `03` | [`上下文压缩`](03-context-compaction/README.md) | 2026-07 起 | AI Lab 结论、Pydantic AI/Harness 审计和压缩实施方案 |
| `04` | [`模型目录`](04-model-catalog/README.md) | 2026-07 起 | 0.2.2 模型目录数据库化、模型运行属性与刷新方案 |
| `05` | [`工具优化`](05-tool-optimization/README.md) | 2026-07 起 | Pydantic AI 工具体系、DeepAgent 当前映射、前端表单工具与 Harness 工具治理 |

设计文档记录研究过程和方案边界，不直接替代当前产品说明。当前行为仍以
[`../guides/`](../guides/README.md) 和实际代码为准。
