# 05 · 工具优化

> 首次研究：2026-07-29
>
> 当前状态：完成 Pydantic AI Core/Harness 工具能力映射；协议展示分离和附件可见性已具备实施边界

[`tool-optimization-design.md`](tool-optimization-design.md) 统一说明 Function、MCP、
External/Deferred、Provider Native、Output Tool 和 Toolset 的边界，以及 DeepAgent 当前
实现和推荐演进顺序。

当前最高优先级是无合法附件时隐藏附件工具，以及稳定协议名与 Console `display_name`
分离。结构化输出、执行审批、大工具集与 Provider Native Tool 继续分阶段设计。实际待办、
优先级和状态只维护在 [`../TODO.md`](../TODO.md)。
