# 13 · 工具检索与渐进披露

> 首次研究：2026-08-07
>
> 当前状态：完成框架、AI Labs 和候选算法调研；完成 Pydantic AI 本地回退黑盒验证；
> 推荐方案已形成，语义检索后端待产品与部署决策

本课题解决大型 MCP 工具包的 Schema 每轮全部进入模型上下文，导致 Token 成本、工具误选和
首轮延迟显著增加的问题。

本课题不改变 Agent 的 ToolPackage 授权方式，也不要求管理员逐个勾选 MCP 工具。核心做法是：

```text
Agent 已授权工具包
→ 确定性过滤当前不可用工具
→ 小型工具直接披露
→ 大型 MCP 进入可检索目录
→ 自动命中少量候选 + 模型按需继续搜索
→ 只向模型加载本轮需要的完整 Tool Schema
```

文档导航：

- [`tool-discovery-research.md`](tool-discovery-research.md)：Pydantic AI、Provider、AI Labs 与其他框架实现对比，以及实际验证结果。
- [`tool-discovery-design.md`](tool-discovery-design.md)：DeepAgent 推荐架构、运行链路、状态、安全、缓存与 Console 设计。
- [`tool-discovery-evaluation.md`](tool-discovery-evaluation.md)：中文业务数据集、离线指标、端到端验证和发布门槛。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：工具体系总览与请求级过滤边界。
- [`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)：ToolPackage 配置和授权模型。
- [`../TODO.md`](../TODO.md)：统一待办，当前对应 `TOOL-201`。

本轮只完成研究、黑盒验证和设计文档，不修改后端、Console、数据库或 Alembic。
