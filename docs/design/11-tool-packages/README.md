# 11 · 工具包与工具目录

> 首次研究：2026-08-04
>
> 当前状态：v0.3.2 已完成数据库、接口、运行装配、Console 与配置备份落地

本课题统一 DeepAgent 中长期混用的 Capability、Toolset、UI 工具和 Console 分组概念。

- [`tool-packages-design.md`](tool-packages-design.md)：完整产品模型、编码、DDL、API、运行链路、迁移和测试方案。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：Pydantic AI 工具类型、Deferred、审批和工具治理总览。
- [`../09-structured-output/structured-output-design.md`](../09-structured-output/structured-output-design.md)：Output Tool 与 Tool Package 的边界。
- [`../TODO.md`](../TODO.md)：跨课题唯一待办清单。

核心口径：Tool Package 是产品配置和 Agent 选择单位，Pydantic AI Toolset 是运行对象；分类只是
Tool Package 的元数据字段。Agent 本轮整包选择，工具协议名、SSE、历史和 Deferred 协议不变。
