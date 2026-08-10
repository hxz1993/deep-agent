# 上下文治理研究结论

> 状态：持续维护的研究依据
>
> 当前运行基线：Pydantic AI 2.26.0、Pydantic AI Harness 0.18.0

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

## Harness 0.18 原生压缩复用边界

Harness `SummarizingCompaction` 不是只能作为 Agent 内 Capability 使用。公开的 `compact_now()` 可以在
Run 外接收一组 `ModelMessage`，调用同一个摘要策略并返回压缩后的消息，因此它具备成为 DeepAgent
“摘要内核”的条件。0.18 还提供了几项已经解决真实问题的语义：

- `incremental=True`：把上一份摘要作为锚点，只合并新事实，避免每次从头重写造成递减失真。
- `preserve_first_user_message` / `keep_user_messages`：保留原始任务或受预算约束的近期用户问题。
- `pin()`：标记压缩后仍必须保留的小型持久状态。
- `receipts`：在被改写的模型历史中记录压缩发生过。
- `compact_now()`：让 `/compact` 一类 Run 外入口复用相同策略。

但它不能直接整体替换 DeepAgent 当前压缩器。Harness 的职责是改写“本次模型将看到的消息历史”，
并不知道 DeepAgent 的以下产品约束：

- `chat_messages` 是完整原始事实源，Checkpoint 只是可失效、可覆盖的派生投影。
- 自动和主动压缩要共用包含 Instructions、工具 Schema、输出预留和安全余量的完整请求预算。
- Harness 0.18 的压缩触发 Token 读数尚不包含工具 Schema，`SummarizingCompaction` 也不为摘要调用
  设置输出上限；DeepAgent 必须继续计算真实 MCP Schema 成本并保留动态摘要输出上限。
- 主动压缩要提供阶段 SSE、`noop/failed/completed` 结果和历史边界展示。
- Checkpoint 保存前必须完成目标复检、原始历史 Hash 与 `row_version` CAS；Pending、取消、失败和
  Sliding Window 均不得覆盖旧 Checkpoint。
- Context Reference 的 `metadata` 不能进入摘要，模型或策略变化要让旧投影失效。

v0.3.16 的最终结论是不整体接入 `compact_now(SummarizingCompaction)`：公开 API 虽能生成摘要，
但仍不具备工具 Schema 完整请求预算、动态摘要输出上限、DeepAgent 主动压缩 SSE、目标复检以及
Checkpoint Hash/CAS 事务语义。为了避免“框架摘要 + 产品事务”形成两层难以验证的控制流，当前
继续复用 Harness 的 Clamp、文件读取去重和工具结果清理等公开确定性 tiers；语义摘要保留在
DeepAgent 内部，并将自动/主动入口收敛到同一投影流程。若 Harness 后续公开接口能覆盖上述差异，
再以黑盒等价测试重新评估，不调用其私有函数。

### Harness 0.18 后续采用项

- **ConversationSearch**：登记为 `CTX-102`。实现读取现有 `chat_messages` 的自定义 `HistorySource`，
  让模型在摘要遗漏精确路径、ID 或决定时使用 `search_conversation_history` 拉回片段；不启用
  `SnapshotHistorySource + StepPersistence` 第二套历史。默认只查当前会话，跨会话必须另做
  user/App/Space 授权和审计。
- **SpendLimits**：登记为 `USAGE-101`。Run 级 Token 硬上限优先使用 Pydantic AI `UsageLimits`；
  会话级 Token 上限和 80% 告警评估 Harness `SpendLimits`。私有 DeepSeek 的美元价格不可靠，第一版
  不按美元阻断；跨 Worker、用户/App/Space 日预算需要 Redis 或同等级共享计数 Store。
- **原生摘要内核评估**：登记为 `CTX-103`。目标是减少自研摘要逻辑，而不是移除产品 Checkpoint。

### Pydantic AI 2.26 原生能力采用项

- **请求输出上限**：`MODEL-001` 优先实施。把 `llm_models.max_output_tokens` 映射到普通与流式请求的
  `ModelSettings.max_tokens`，目录字段才能从“预算参考”变成真实 Provider 请求约束；未配置时保持
  Provider 默认行为。
- **UsageLimits**：`USAGE-101` 同时评估 `tool_calls_limit` 和
  `per_request_input_tokens_limit`。它们分别阻止单 Run 工具循环失控和单次超大输入，但不能替代
  DeepAgent 包含工具 Schema、输出预留和安全余量的前置窗口预算。
- **运行关联与观测**：`OBS-101` 使用稳定、作用域化的 `conversation_id` 关联同一 Agent Session，
  每次发送或 Deferred 续跑使用独立 `run_id`；只有接入脱敏后的 Instrumentation 导出端才有价值，
  不能把 ID 当作历史托管、业务审计或多 Pod Cancel 协调。
- **Provider 特性**：`MODEL-101` 仅对明确支持的 Provider 使用 Prompt Cache / `CachePoint`；
  `MODEL-102` 评估 `RaiseContentFilterError`，把安全拦截与正常空回答区分；二者均需统一 REST、SSE、
  History 和 Console 语义。
- **显式后备与 Thinking**：`MODEL-201` 的 `FallbackModel` 只能由 Agent 明确配置，不能静默跨模型、
  跨地域回退；`MODEL-202` 以后用 Core 的统一 Thinking 设置替换零散 Provider 参数，但仍需模型目录
  能力声明与前端/历史协议配套。
- **工具返回 Schema**：`TOOL-203` 只对模型确需理解复杂返回结构的少量工具评估
  `IncludeToolReturnSchemas`；它会增加 Schema Token，不能对全量 MCP 工具启用。

## 配置管理结论

全局运行策略仍使用环境变量和现有配置加载机制，不新增在线编辑数据库表。会话、消息和
Checkpoint 属于运行状态，继续持久化到数据库。上下文压缩默认开启，只在模型没有
`context_window_tokens` 时关闭，不增加功能开关。

## 暂不引入的能力

- Memory：只有形成明确跨会话记忆产品边界后再设计范围、审计和删除。
- Usage Ledger：只有成本、计费或跨会话统计需求出现时再建调用账本。
- StepPersistence：现有 SQL 会话与 Pending 恢复仍是事实源；ConversationSearch 通过自定义
  `HistorySource` 读取该事实源，不为搜索复制一套快照。
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
