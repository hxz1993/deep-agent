# 上下文治理研究结论

> 状态：持续维护的研究依据
>
> 当前运行基线：Pydantic AI 2.26.0、Pydantic AI Harness 0.10.0

## 总体判断

DeepAgent 不迁移到 AgentOS、Pi、Hermes、OpenCode 或其他完整 Agent 运行时。继续保留
Pydantic AI Core 与现有产品层，选择性吸收上游的上下文治理机制。

必须保留的产品资产包括：

- Agent、模型、MCP、UI 工具和辅助模型配置。
- DeepFOS 用户、App、Space 和请求 Header 边界。
- SQL 会话、完整有序历史、历史分支和取消恢复。
- Deferred HITL、SSE、附件和 Console 协议。

上游项目提供机制和证据，不替代上述产品责任。

## 上游项目的有效启发

| 项目 | 值得吸收 | 不直接照搬 |
| --- | --- | --- |
| Pydantic AI Harness | 分层压缩、长工具结果治理、可插拔 Token 估算 | 用 StepPersistence 替换现有 SQL 会话 |
| Pi Agent | AgentSession、树形会话、事件、压缩和扩展 Hook | TypeScript Coding Agent 运行时 |
| Hermes | 增量摘要、跨会话记忆、Skill 和计划 | 单租户个人 Agent 的本地配置假设 |
| OpenCode | Client/Server、SSE、权限、错误恢复和配置分层 | Coding Agent 的工具与权限模型 |
| Deep Agents | 上下文分层、工具结果外置和受限子代理 | 与 Pydantic AI 并存的第二套 Runtime |
| DeerFlow | Workspace、Artifact、Sandbox、上传和长任务 | 整体迁移其全栈结构 |
| AgentOS / Agno | Session、Run、HITL、Memory 和 Registry | 替换现有会话、SSE、配置中心和权限 |
| Learn Claude Code | 最小 Agent Loop、工具边界、压缩和恢复 | 重写 Pydantic AI 已有循环 |

## Harness 实验结论

真实历史的压力主要来自旧工具结果和附件分页内容，不是消息条数。隔离实验确认：

- `ClampOversizedMessages` 能阻止单条异常内容占满窗口。
- `ClearToolResults` 对长工具历史有显著缩减效果。
- 只做 Sliding Window 会永久丢失旧约束，不适合作为默认策略。
- Harness 摘要不会自动成为 DeepAgent 原始历史的一部分。
- Tool Call/Result 配对、Pending HITL、分支和取消必须由 DeepAgent 补齐。

因此采用“确定性裁剪优先、主模型单次摘要、Sliding Window 仅自动链路失败兜底”的组合，并把结果
保存为独立 Checkpoint 投影。

## 为什么不能只依赖 Provider usage

Provider usage 在模型响应后才产生，无法阻止当前请求超窗。预算前置判断需要统一估算器：

- 已知官方 tokenizer 时使用模型 tokenizer。
- OpenAI Compatible 私有模型不能仅因协议兼容就套用 `tiktoken`。
- 未知模型按 CJK、英文、代码、JSON、符号和复杂 Unicode 做保守估算。
- 工具 Schema、消息结构、摘要 Prompt 和媒体占位都必须计入。

历史响应中的真实 `ModelResponse.usage.output_tokens` 适合预测下一次输出；缺失时再估算。
这不需要单独 usage 表。

## 模型属性结论

数据库只保存 Provider 可声明的稳定模型事实：

- `context_window_tokens`：输入和输出共享总窗口。
- `max_output_tokens`：单次输出硬上限。
- 输入模态和能力标签。

“本轮预计输出”“安全余量”“摘要输出上限”和“可用输入预算”都是动态算法值，不要求
管理员配置，也不持久化。总窗口 1M、最大输出 384K 不等于每次都预留 384K。

## 配置管理结论

全局运行策略仍使用环境变量和现有配置加载机制，不新增在线编辑数据库表。会话、消息和
Checkpoint 属于运行状态，继续持久化到数据库。上下文压缩默认开启，只在模型没有
`context_window_tokens` 时关闭，不增加功能开关。

## 暂不引入的能力

- Memory：只有形成明确跨会话记忆产品边界后再设计范围、审计和删除。
- Usage Ledger：只有成本、计费或跨会话统计需求出现时再建调用账本。
- StepPersistence：现有 SQL 会话与 Pending 恢复仍是事实源。
- Planning/SubAgent：等待长任务与专业分工场景稳定。
- Artifact：应作为 DeepFOS 业务实体独立设计，不塞进压缩摘要。

这些方向登记在 [`../TODO.md`](../TODO.md)，不在当前压缩链路中预埋半成品。

## 证据入口

完整实验和源码索引仍保留在 `deepfos-AI/agent-labs/`：

| 主题 | 路径 |
| --- | --- |
| 上游项目能力总览 | `../../../../agent-labs/upstream-agent-references/indexes/comparison.md` |
| AgentOS 最终决策 | `../../../../agent-labs/deepagent-framework-evaluation/docs/decision-report.md` |
| AgentOS 真实验证 | `../../../../agent-labs/deepagent-framework-evaluation/docs/live-validation-results.md` |
| Harness 压缩实验 | `../../../../agent-labs/deepagent-context-compaction-evaluation/docs/test-results.md` |
| Learn Claude Code 对照 | `../../../../agent-labs/learn-claude-code/DEEPAGENT_STUDY.md` |
| Harness Engineering 对照 | `../../../../agent-labs/harness-engineering/DEEPAGENT_STUDY.md` |

新研究的原始数据继续放在 AI Lab；只有稳定、可复用且影响 DeepAgent 决策的结论同步到
本文件。
