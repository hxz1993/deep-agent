# 05 · 工具优化

> 首次研究：2026-07-29
>
> 当前状态：完成 Pydantic AI Core/Harness 工具能力映射与 v0.3.2 工具包统一；大型 MCP 工具检索已拆为独立课题

[`tool-optimization-design.md`](tool-optimization-design.md) 统一说明 Function、MCP、
External/Deferred、Provider Native、Output Tool 和 Toolset 的边界，以及 DeepAgent 当前
实现和推荐演进顺序。

当前最高优先级是大型 MCP 工具包的检索与渐进披露。Agent 仍按 ToolPackage 授权，检索只能在
已授权包内工作，不要求管理员逐个选择工具。少量内置工具采用更直接的条件过滤：会话没有合法
附件时隐藏整个附件读取包，有附件时暴露现有三个读取工具，不再按后缀拆分可见性。实际待办、
优先级和状态只维护在 [`../TODO.md`](../TODO.md)。框架对比、中文检索黑盒验证、混合语义算法、
渐进披露链路和待决策项见
[`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)。

产品配置统一采用“分类属性 → 工具包 → 工具”。Agent 在 v0.3.2 仍然整包选择，UI 工具也不再
按 `ui:<tool_name>` 单项分配；旧编码只由 Alembic 002 一次性迁移。工具协议名、SSE、Pending
和历史格式保持不变。完整数据、接口和运行装配方案见
[`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)。Output Tool
不属于工具包，也不加入 `agents.capabilities`，而由独立输出契约管理。

结构化最终输出已经拆为独立课题，Output、Deferred、Harness 与业务 Tool Result 的详细边界见
[`../09-structured-output/structured-output-design.md`](../09-structured-output/structured-output-design.md)。
