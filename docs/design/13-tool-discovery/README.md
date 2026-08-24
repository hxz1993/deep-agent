# 13 · 工具检索与渐进披露

> 首次研究：2026-08-07
>
> 当前状态：v0.3.12 已实施第一版；当前使用本地词法检索与 Pydantic AI 2.31 渐进披露，
> 不部署 Embedding、不增加数据库结构

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
- [`../TODO.md`](../TODO.md)：统一待办，`TOOL-103`、`TOOL-201` 已在 v0.3.12 关闭。

当前实现锁定为：MCP 候选不超过 10 个时全部直接披露；超过 10 个时首轮词法预选最多 Top 3，
模型通过 `search_tools` 最多加载 Top 5；其余延迟工具名称按 Server 放入动态 Instructions。
检索只处理 Agent 已授权且当前身份可用的 MCP 工具。无合法会话附件时隐藏整个附件 Toolset。
Shadow 是测试目录下的脱敏离线评估，不进入生产请求链路。
