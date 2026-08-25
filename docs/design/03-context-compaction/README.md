# 03 · 上下文压缩

> 首次研究：2026-07-22
>
> 当前状态：v0.6.0已重构为Event Store内的单条Compaction生命周期事件；Alembic 008不再维护独立压缩表。

- [`context-compaction-design.md`](context-compaction-design.md)：当前预算、结构化摘要、Tool/Context
  Evidence、超大结果裁剪、SSE、并发和失败闭合规则。
- [`../18-session-trajectory/README.md`](../18-session-trajectory/README.md)：Event Store、Turn/Run、API、
  Plan和Console轨迹的统一数据源。

旧研究结论已经吸收到当前设计；过期的独立投影、批量清空工具结果和旧History方案不再保留为实施文档。
