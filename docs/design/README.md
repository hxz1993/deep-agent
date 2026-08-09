# 设计课题

本目录按课题首次研究时间排序。编号越大，表示课题越新。每个课题只保留当前有效设计、
必要研究依据和少量历史决策，跨课题未完成事项统一登记在 [`TODO.md`](TODO.md)。

| 序号   | 课题                                              | 首次研究 | 当前状态 |
|------|-------------------------------------------------| --- | --- |
| `01` | [`UI 工具`](01-ui-tools/README.md)                | 2026-06 | builtin/manual 已落地；MCP 自动发现方案已关闭 |
| `02` | [`运维控制台`](02-operations-console/README.md)      | 2026-07 | 一体化 Console 已落地，保留交互原型 |
| `03` | [`上下文压缩`](03-context-compaction/README.md)      | 2026-07 | 自动与主动主模型压缩已落地；当前有效 Checkpoint 可在 Console 恢复 |
| `04` | [`模型目录`](04-model-catalog/README.md)            | 2026-07 | 数据库目录与手工刷新已落地，结构并入 v0.3.0 基线 001 |
| `05` | [`工具优化`](05-tool-optimization/README.md)        | 2026-07 | 完成工具体系映射；大型 MCP 检索拆入 13 课题 |
| `06` | [`附件解析`](06-attachment-parsing/README.md)       | 2026-07 | 第一版按需读取与压缩协同已落地 |
| `07` | [`Python Sandbox`](07-python-sandbox/README.md) | 2026-07 起 | A 已落地（subprocess-dev + soft guardrails）；B 探针代码已有、节点选型未完成；C 阻塞于 B |
| `08` | [`Artifact 交互语义`](08-artifact-export/README.md) | 2026-08 | 骨架：句柄语义为主；下载中心等为可选 sink |
| `09` | [`结构化最终输出`](09-structured-output/README.md) | 2026-08 | 0805 实验实现已归档；产品边界未锁定，当前暂缓正式实施 |
| `10` | [`上下文引用`](10-context-reference/README.md) | 2026-08 | v0.3.5 已完成 DeepAgent 公共协议；业务前端迁移待后续跨仓实施 |
| `11` | [`工具包与工具目录`](11-tool-packages/README.md) | 2026-08 | v0.3.2 已完成工具包目录、UI 工具包、运行 Toolset 与 Console 统一改造 |
| `12` | [`Agent Skill`](12-agent-skills/README.md) | 2026-08 | 当前仅有最小 Python Skill；选定 Harness 0.18 渐进披露基础，线上安装与素材治理待实施 |
| `13` | [`工具检索与渐进披露`](13-tool-discovery/README.md) | 2026-08 | v0.3.12 已完成词法 Top 3/Top 5、附件条件过滤和 Pydantic AI 渐进披露 |

## 文档命名

- `README.md`：课题入口、当前状态和文档导航。
- `*-research.md`：上游项目、框架能力和实验依据。
- `*-design.md`：当前认可的架构、数据和运行链路。
- `*-history.md`：已废弃方案及废弃原因，不再作为实施依据。
- `TODO.md`：唯一跨课题待办清单；课题文档不再维护独立待办副本。

## 阅读规则

- 当前使用和接口行为先看 [`../guides/`](../guides/README.md) 与源码。
- 设计文档用于说明决策背景、稳定边界和后续方向，不替代运行说明。
- 历史细节可从 Git 记录恢复，不在当前目录重复保留多份过期全文。
