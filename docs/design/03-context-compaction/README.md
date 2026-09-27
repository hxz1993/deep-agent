# 03 · 上下文压缩

> 首次研究：2026-07-22
>
> 当前状态：v0.11.6 D01 改造保留 Event Store 单条 Compaction 生命周期事件；数据库保持 007，无新增表。

- [`context-compaction-design.md`](context-compaction-design.md)：当前预算、交接摘要、受保护请求、来源索引、有界回读、SSE、并发和失败闭合规则。
- [`../18-session-trajectory/README.md`](../18-session-trajectory/README.md)：Event Store、Turn/Run、API、
  Plan和Console轨迹的统一数据源。

旧研究结论已经吸收到当前设计；过期的独立投影、批量清空工具结果和旧History方案不再保留为实施文档。

验收与限制见 [D01 报告](../../reports/20260926-d01-context-compaction/README.md)。
