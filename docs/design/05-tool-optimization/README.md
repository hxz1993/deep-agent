# 05 · 工具优化

> 首次研究：2026-07-29
>
> 当前状态：完成工具能力映射、v0.3.2 工具包统一、v0.3.12 MCP检索及 v0.3.17 选择性 Return Schema

[`tool-optimization-design.md`](tool-optimization-design.md) 统一说明 Function、MCP、
External/Deferred、Provider Native、Output Tool 和 Toolset 的边界，以及 DeepAgent 当前
实现和推荐演进顺序。

大型 MCP 工具包的检索与渐进披露已落地。Agent 仍按 ToolPackage 授权，检索只能在
已授权包内工作，不要求管理员逐个选择工具。文件读取采用更直接的条件注入：当前原始会话存在
合法附件，或 Agent选择了可读 Skill资源时，运行时注入统一系统工具 `read_file`；没有文件来源时
不注入。它不属于 ToolPackage，也不再按文件类型拆分多个模型工具。实际待办、
优先级和状态只维护在 [`../TODO.md`](../TODO.md)。框架对比、中文检索黑盒验证、混合语义算法、
渐进披露链路和待决策项见
[`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)。

v0.3.17 仅对 `cube_query_data`、`dimension_query_members` 两个具有可信 Output Schema 的复杂查询
工具启用 Pydantic AI `IncludeToolReturnSchemas`。Schema只随工具实际披露，不会让延迟目录提前
承担 Token成本；未提供 Output Schema 时不伪造。详细口径见
[`tool-optimization-design.md#84-tool-return-schema`](tool-optimization-design.md#84-tool-return-schema)。

产品配置统一采用“分类属性 → 工具包 → 工具”。Agent 在 v0.3.2 仍然整包选择，UI 工具也不再
按 `ui:<tool_name>` 单项分配；旧编码只由 Alembic 002 一次性迁移。工具协议名、SSE、Pending
和历史格式保持不变。完整数据、接口和运行装配方案见
[`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)。Output Tool
不属于工具包，也不加入 `agents.capabilities`，而由独立输出契约管理。

结构化最终输出已经拆为独立课题，Output、Deferred、Harness 与业务 Tool Result 的详细边界见
[`../09-structured-output/structured-output-design.md`](../09-structured-output/structured-output-design.md)。
