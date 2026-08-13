# Pydantic AI 与 Harness 能力采用基线

> 首次审计：2026-08-10
>
> 固定基线：`pydantic-ai-slim 2.26.0`、`pydantic-ai-harness 0.18.0`
>
> 状态：当前有效的框架能力采用台账

本课题回答一个长期问题：Pydantic AI 或 Harness 已经提供的能力，DeepAgent 是否应直接复用、
部分复用、保留为未来选项，还是明确不采用。

以后升级框架版本时，不再从全部历史 TODO 重新调研。只需要：

1. 对比新版本相对本基线新增或改变的公开能力。
2. 检查它是否能替换 DeepAgent 的现有自研逻辑。
3. 将结论更新到能力台账。
4. 只有确定要实施或具备明确触发条件的事项才进入统一 TODO。

## 当前结论

- Pydantic AI Core 是 Agent Loop、Model、Toolset、Tool Search、Deferred、结构化输出、取消、
  Usage 和生命周期 Hook 的首选实现层。
- Harness 是官方能力库，适合复用 Skill 渐进披露、确定性上下文整理、工具结果治理、Guardrail、
  会话检索等横切能力，但不替代 DeepAgent 的产品配置、租户授权和数据库事实源。
- DeepAgent 继续拥有 SQL 会话、Context Reference、Checkpoint、SSE、ToolPackage、MCP 配置、
  UI Deferred 持久化和多租户边界。
- v0.3.17 已完成最小运行观测：官方 Conversation/Run 标识、脱敏结构化日志、缓存 Usage和
  Console 当前 Run；OTLP 后端、正式诊断中心和 Evaluation 仍按独立需求推进。
- v0.4.0 已采用Harness `ToolOutputLimits` + `Spill`，通过平台文件Store外置大工具结果，并保留
  原生 `read_tool_result`；没有复制读取状态机，也没有新增Artifact表。
- 压缩后搜索原始会话已暂缓；只有真实长程任务出现摘要后关键事实无法找回时才重新启动。
  其余能力按条件保留，不预埋半成品。

完整清单见
[`framework-capabilities-baseline.md`](framework-capabilities-baseline.md)，实施状态见
[`../TODO.md`](../TODO.md)。
