# 05 · 工具优化

> 首次研究：2026-07-29
>
> 当前状态：完成 Pydantic AI Core/Harness 工具能力映射；产品工具包统一方案进入 v0.3.2 实施

[`tool-optimization-design.md`](tool-optimization-design.md) 统一说明 Function、MCP、
External/Deferred、Provider Native、Output Tool 和 Toolset 的边界，以及 DeepAgent 当前
实现和推荐演进顺序。

当前最高优先级是无合法附件时隐藏附件工具，以及稳定协议名与 Console `display_name`
分离。结构化输出、执行审批、大工具集与 Provider Native Tool 继续分阶段设计。实际待办、
优先级和状态只维护在 [`../TODO.md`](../TODO.md)。

产品配置统一采用“分类属性 → 工具包 → 工具”。Agent 在 v0.3.2 仍然整包选择，UI 工具也不再
按 `ui:<tool_name>` 单项分配；旧编码只由 Alembic 002 一次性迁移。工具协议名、SSE、Pending
和历史格式保持不变。完整数据、接口和运行装配方案见
[`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)。Output Tool
不属于工具包，也不加入 `agents.capabilities`，而由独立输出契约管理。

结构化最终输出已经拆为独立课题，Output、Deferred、Harness 与业务 Tool Result 的详细边界见
[`../09-structured-output/structured-output-design.md`](../09-structured-output/structured-output-design.md)。
