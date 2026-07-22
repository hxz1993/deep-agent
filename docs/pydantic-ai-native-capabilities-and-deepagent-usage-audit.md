# Pydantic AI 原生能力与 DeepAgent 当前使用情况审计

> 最后核验：2026-07-22  
> DeepAgent 本机运行版本：`pydantic-ai-slim 2.12.0`  
> Harness 审计版本：`pydantic-ai-harness 0.8.0`，仅下载 wheel 解包审计，未安装到 DeepAgent 解释器  
> 本文只做源码审计和方案判断，不代表已经引入 Harness、修改数据库或改变运行行为

## 1. 结论

### 1.1 Harness 能不能减轻 DeepAgent 的自研负担

可以，但适合补三类“Agent 运行时能力”，不适合替代整个 DeepAgent：

1. **上下文治理**：优先评估 Harness `TieredCompaction`，先清理旧工具结果，仍超预算时再总结。
2. **超长工具返回治理**：评估 `OverflowingToolOutput`，对大型 MCP、文件和查询结果做截断、摘要或外置存储。
3. **跨会话 Memory**：有明确产品需求后，使用 Harness 的命名空间和 Store 协议，避免重新实现并发写、幂等和检索工具。

Guardrails、Instrumentation、ToolSearch 也有价值，但优先级低于上下文和工具结果。

### 1.2 哪些已有能力可以替换

真正能“直接替换”的不多，因为 DeepAgent 已有功能多数是在线产品层，而 Harness 是 Capability 能力库。

| DeepAgent 能力 | 是否适合替换 | 判断 |
| --- | --- | --- |
| 自己新写一套历史压缩算法 | 可以避免 | 优先用 Harness Compaction，再补 DeepAgent 持久化适配 |
| 自己新写通用大工具结果截断/外置协议 | 可以避免大部分 | 优先复用 `OverflowingToolOutput` 的 Hook、分档和读取句柄协议 |
| SQL 会话、消息、用户隔离 | 不替换 | Harness 不提供产品会话列表、权限、标题、删除和分支 |
| SSE 协议、Console 历史渲染 | 不替换 | Harness 不提供 DeepAgent 的前后端协议 |
| Deferred HITL、Pending 恢复 | 不替换 | 继续使用 Pydantic AI Deferred 原语和 DeepAgent 持久化编排 |
| Agent、模型、MCP、UI 工具配置中心 | 不替换 | Pydantic AI/Harness 不提供产品级配置数据库和管理 UI |
| 当前 Skill 目录与 Agent 关联 | 暂不替换 | Harness 0.8.0 wheel 中没有已发布 Skills Capability |
| 工具访问和输入校验 | 可局部接入 | Harness Guardrails 可承载规则，但业务策略仍由 DeepAgent 提供 |
| 步骤日志和工具副作用账本 | 暂不替换 | StepPersistence 仅在出现崩溃恢复、子 Agent 或副作用审计需求时有增量价值 |

### 1.3 最小推荐路线

不要全量迁移，也不要先重写会话层。建议按以下顺序：

```text
第一阶段：模型上下文元数据 + Token 观测
第二阶段：固定版本试点 TieredCompaction
第三阶段：按真实需求接 Memory
最后：再判断 StepPersistence 是否值得引入
```

如果正式引入，建议把 `pydantic-ai-slim` 和 `pydantic-ai-harness` 作为一组锁定并回归验证。当前 `requirements.txt` 使用 `pydantic-ai-slim[...]>=1.0`，不能保证不同环境仍安装 `2.12.0`；Harness 又处于 0.x，不能继续只用无限制的 `>=`。

## 2. 审计依据与版本边界

### 2.1 当前实际环境

当前 Python 3.12 解释器中：

```text
pydantic-ai-slim 2.12.0
pydantic-ai 未安装
pydantic-ai-harness 未安装
```

DeepAgent 依赖为：

```text
pydantic-ai-slim[openai,anthropic,google,mcp]>=1.0
```

`pydantic-ai-slim` 已包含 Agent、Toolset、Capability、消息、Usage 等核心代码；没有安装 `pydantic-ai` 元包不影响当前能力。元包主要用于一次安装更多可选依赖，不是另一套 Agent 引擎。

### 2.2 Harness 0.8.0 的准确边界

本次直接检查了 `pydantic-ai-harness 0.8.0` wheel。该版本声明：

- Python `>=3.10`。
- 依赖 `pydantic-ai-slim>=2.1.0`，没有上限。
- Development Status 为 `Alpha`。
- 0.x 次版本可能发生破坏性变化。

0.8.0 wheel 已包含 Compaction、Overflowing Tool Output、Memory、StepPersistence、Guardrails、Planning、SubAgents、FileSystem、Shell、CodeMode、Context、DynamicWorkflow 等模块。

需要纠正一个容易误判的点：**Harness 0.8.0 wheel 没有已发布的 Skills 模块**。上游能力矩阵中的 Skills 仍属于持续建设项，不能把它当成当前可替换 DeepAgent Skill 的稳定现成能力。

## 3. DeepAgent 的 Agent 运行机制

### 3.1 模型实际看到什么

每次模型请求主要包含：

```text
Agent Instructions
+ 已选择能力附带的 Instructions
+ message_history
+ 当前用户输入和附件
+ 本轮可用 Tool Definitions
```

Tool Definition 通常包含名称、说明和 JSON Schema。模型不会直接执行 Python 或 HTTP；它返回工具名称和参数，Pydantic AI 负责校验、路由、执行和把结果加入下一次模型请求。

Pydantic AI 内部负责 Agent loop：

```text
准备模型请求
  -> 列出本轮工具
  -> 调用模型
  -> 模型返回文本或 Tool Call
  -> 校验参数并调用 Toolset
  -> 将 Tool Result 写入运行历史
  -> 再次调用模型
  -> 直到最终输出、Deferred 请求或达到 UsageLimits
```

DeepAgent 没有重写这个循环。它调用 `Agent.run()` 或 `Agent.run_stream_events()`，再把 Pydantic AI 事件转换成自己的 SSE 和数据库消息。

### 3.2 两种不同的 Capability

| 名称 | 所属 | 当前用途 |
| --- | --- | --- |
| `deep_agent.capabilities.registry.Capability` | DeepAgent 自研 | 配置和选择 Instructions/Toolset，供 Agent、Console 和数据库引用 |
| `pydantic_ai.capabilities.AbstractCapability` | Pydantic AI 原生 | 介入 Agent 生命周期，组合 Instructions、Toolset、Hooks、模型设置和历史处理 |

DeepAgent 自研 `Capability` 是产品配置包装，不参与 Pydantic AI 生命周期。当前真正使用原生 Capability 的实现只有 `DeepSeekThinkingRetry`。

新的上下文、工具治理和观测能力，应优先通过原生 Capability/Hook 扩展，避免继续往 `AgentRunner` 主流程堆条件。

### 3.3 能力装配链路

```text
数据库 Agent 配置
  -> AgentSpec
  -> CapabilityRegistry 选择 native / skill
  -> 请求级解析 mcp:* 和 ui:* 引用
  -> 组装 FunctionToolset / MCPToolset / ExternalToolset
  -> augment_instructions_with_capabilities()
  -> pydantic_ai.Agent(..., toolsets=..., capabilities=...)
  -> AgentRunner.run / run_stream
```

关键位置：

- `deep_agent/api/v1/deps.py`：从 Agent、数据库 MCP、UI 工具和请求 Header 组装运行器。
- `deep_agent/agent/factory.py`：创建 Pydantic AI `Agent`。
- `deep_agent/agent/runner.py`：读取历史、解析附件、运行 Agent、发布 SSE 和持久化新消息。
- `deep_agent/capabilities/registry.py`：DeepAgent 自研能力注册表。

### 3.4 不同能力如何进入模型

| DeepAgent 能力 | Pydantic AI 对应机制 | 模型能否直接看到 |
| --- | --- | --- |
| Native Tool | `FunctionToolset` | 看到函数工具定义 |
| Python Skill | Instructions + `FunctionToolset` | 看到 Skill 指令和工具定义 |
| Cursor Skill | Instructions 摘要 + `read_<skill>` 函数 | 先看到摘要，需要时读取正文 |
| MCP Tool | `MCPToolset`，外层增加 MCP 前缀 | 看到 `tools/list` 返回的工具定义 |
| UI Tool | `ExternalToolset` + Deferred | 看到特殊工具定义，执行由前端用户完成 |
| MCP Resource | `list_resources/read_resource` | 默认看不到，必须由宿主读取或包装为工具 |
| MCP Prompt | MCP Prompt API | 默认看不到，必须由宿主选择并注入 |

当前 DeepAgent 只在 MCP UI 工具发现中使用了 `read_resource()`；普通 Agent 尚无通用 Resource 注入机制。

## 4. 当前已使用的 Pydantic AI Core 能力

| Core 能力 | DeepAgent 使用方式 | 关键位置 |
| --- | --- | --- |
| Agent 循环与模型抽象 | `Agent` 组装 model、instructions、toolsets、capabilities 和 retries | `deep_agent/agent/factory.py` |
| 运行时依赖注入 | `RunContext[AgentDeps]` 传 session、user、agent 和请求 Header | `deep_agent/agent/deps.py` |
| 本地函数工具 | `FunctionToolset` 注册内置工具和 Python Skill | `deep_agent/capabilities/native/`、`skills/` |
| MCP 接入 | `MCPToolset`、`DynamicToolset`、`PrefixedToolset` | `deep_agent/capabilities/mcp/` |
| 前端执行工具 | `ExternalToolset` 暴露 HITL/UI 工具 | `deep_agent/capabilities/ui/` |
| 消息历史 | SQL 消息反序列化为 `ModelMessage`，作为 `message_history` | `deep_agent/agent/runner.py` |
| 流式执行 | `run_stream_events` 和 Pydantic AI 流事件 | `deep_agent/agent/runner.py` |
| 参数纠错 | `ModelRetry` 将非法 UI 参数反馈给模型 | `deep_agent/capabilities/ui/external.py` |
| 使用限制 | `UsageLimits` 控制模型请求数和可选总 Token | `deep_agent/api/v1/deps.py` |
| 延迟工具 | `DeferredToolRequests/Results`、`ToolDenied` | `deep_agent/agent/deferred.py` |
| 多模态消息 | `BinaryContent`、`TextContent`、`UserPromptPart` | 附件请求链路 |
| 原生 Capability | `DeepSeekThinkingRetry` | `deep_agent/agent/capabilities/` |

当前未使用但值得关注的 Core 能力：

| Core 能力 | 用途 | 建议 |
| --- | --- | --- |
| `ProcessHistory` | 请求前改写历史 | 若采用 Harness Compaction，不再同时自研第二套压缩器 |
| `PrepareTools` | 每轮过滤或修改工具定义 | 适合按权限、模态、任务阶段控制工具 |
| `ToolSearch` | 大工具集渐进发现 | 单 Agent 工具达到数十个后再接入 |
| `Hooks` / `Instrumentation` | 生命周期策略与观测 | 建议接入，但先定义脱敏规范 |
| `Thinking` | 跨 Provider 推理配置 | 可评估，不能直接替代 DeepSeek 输出修复 |
| `IncludeToolReturnSchemas` | 暴露返回 Schema | 只对结构稳定工具小范围开启 |
| `HandleDeferredToolCalls` | 自动处理 Deferred | 不替代现有产品化 HITL |
| `ProcessEventStream` | 处理原生事件流 | 可作为内部辅助，不替换现有 SSE 协议 |

## 5. DeepAgent 已有自研能力

### 5.1 产品会话与身份

DeepAgent 自己负责：

- `chat_sessions` / `chat_messages` SQL 持久化。
- Header 用户身份、会话归属和用户隔离。
- 会话列表、标题、删除、分支重发。
- Test/线上请求上下文和 MCP Header 转发。

这些不是 Harness 的职责，必须保留。

### 5.2 SSE、历史和中断

DeepAgent 将 Pydantic AI 事件映射成自己的 SSE 协议，通过 `EventPersister` 处理：

- 用户消息预写。
- 流式内容累计。
- 成功后只追加 `result.new_messages()`。
- 取消时把已累计内容重建为可持久化消息。
- 仅在合适边界修复悬空 Tool Call。

Harness 不提供 DeepAgent Console 所需的这套前后端协议。

### 5.3 HITL/UI 工具

DeepAgent 基于 Pydantic AI Deferred/External Tool 原语，自行实现：

- 数据库 UI 工具定义和 Agent 正向关联。
- Schema 校验和有限次 `ModelRetry`。
- 非法中间调用过滤。
- SSE 表单事件。
- Pending 历史恢复、确认、取消和继续运行。

当前机制可继续使用。MCP Elicitation/MCP Apps 更适合作为第三方 MCP 的互操作入口，不是近期替换目标。

### 5.4 Skill

- Python Skill：同时贡献 Instructions 和函数工具。
- Cursor Skill：先暴露摘要，再通过 `read_<skill>` 渐进读取正文。

其目录协议、发现和 Agent 关联由 DeepAgent 实现。Core `ToolSearch` 可改善大工具集发现，但 Harness 0.8.0 没有可直接替换这套机制的已发布 Skills Capability。

### 5.5 配置和文件能力

DeepAgent 还负责：

- Agent、模型、MCP、UI 工具和辅助模型配置中心。
- 上传、文件提取、附件历史和模态校验。
- 自动标题、Console、请求上下文和运行检查器。

这些都应保留在产品层。

## 6. 真实调用链与状态边界

### 6.1 普通流式对话

```text
POST /api/v1/chat/stream
  -> RequestUserDep 读取当前用户
  -> extract_forward_headers() 提取 MCP 白名单 Header
  -> build_runner_for_agent()
  -> 组装 MCP/UI Toolset、Agent、UsageLimits
  -> SQL get_history() 读取完整历史
  -> 附件解析为当前用户输入
  -> Agent.run_stream_events(message_history=完整历史)
  -> Pydantic AI 模型/工具循环
  -> DeepAgent 转成 SSE
  -> EventPersister 追加本轮新消息
```

### 6.2 Deferred HITL

```text
模型调用 UI Tool
  -> Pydantic AI 返回 DeferredToolRequests
  -> DeepAgent 将 tool_name / args / tool_call_id 写入历史
  -> 前端渲染 Pending 表单
  -> 用户确认或取消
  -> 下一次请求携带 deferred_tool_results
  -> DeepAgent 按 tool_call_id 构建 DeferredToolResults
  -> 重新装载 SQL 历史并开始新的 Agent.run
```

这里恢复的是 provider-valid 消息和 Deferred 结果，不是恢复原 Python 协程。Harness StepPersistence 也是从安全快照开启新的 `Agent.run`，不会改变这个事实。

### 6.3 UsageLimits

当前只配置：

```text
request_limit       Agent 级配置受全局上限约束
total_tokens_limit  可由环境配置，当前可能为空
```

未配置：

```text
tool_calls_limit
input_tokens_limit
output_tokens_limit
count_tokens_before_request
```

`request_limit` 统计模型请求，不等于工具调用次数。Harness 的 LLM 摘要也是一次真实模型请求，会进入本轮 Usage，并可能更早触发 `request_limit`。

## 7. 上下文与 Token 风险

### 7.1 当前装载全部历史

消息 Store 会读取当前 Agent/Session 的全部 `chat_messages` 并按 sequence 排序；`AgentRunner` 将完整历史传入模型。

当前没有按消息数、最近轮次、输入 Token、工具结果体积或时间范围裁剪。`EARLY_MESSAGE_LIMIT` 只用于会话列表摘要，不限制模型历史。

### 7.2 Test 数据规模

审计时只统计长度和数量，没有输出业务内容：

| 指标 | 结果 |
| --- | ---: |
| 会话数 | 76 |
| 存储消息数 | 747 |
| 单会话消息数最大值 | 70 |
| 单会话消息数 P95 | 42 |
| 单会话序列化字符最大值 | 582,489 |
| 单会话序列化字符 P95 | 426,362 |
| 单会话工具结果数最大值 | 40 |
| 单会话工具结果字符 P95 | 57,369 |
| 单条工具结果最大字符数 | 25,421 |

字符数不是精确 Token，但已经足以证明上下文风险存在。

### 7.3 工具结果和附件

- MCP Tool Result 默认完整持久化、恢复并进入后续模型请求。
- 内置 `http_get` 单独限制为 4000 字符，但没有通用工具结果上限。
- 附件提取文本默认每个最多 32,000 字符，每轮最多 10 个附件。
- 工具 Schema 也会占用请求上下文，但当前没有统一观测。

### 7.4 当前 Token 是否可靠

DeepAgent 已能从 `result.usage` 返回：

```text
requests
input_tokens
output_tokens
total_tokens
```

普通 `/chat` 响应和最终 SSE 都会带 Usage，Console 也会显示。但当前有三个限制：

1. **Usage 没有写入会话数据库**，刷新后不能做历史成本、模型对比和压缩效果分析。
2. **Token 由 Provider 响应提供**。Pydantic AI 负责汇总；如果 OpenAI-compatible 或私有端点不返回 Usage、字段不兼容，结果可能是 0，而不是“真实消耗为 0”。
3. **没有可靠性标记**。前端和日志无法区分 Provider 实报、框架估算和缺失数据。

因此，当前 Usage 可以用于单次调试，但不足以单独驱动通用上下文预算。

### 7.5 模型表是否足够

当前 `llm_models` 只有：

```text
id / name / description / model / base_url / api_key
input_modalities / capabilities_source / created_at / updated_at
```

模型元数据刷新只处理输入模态。当前缺少：

```text
context_window_tokens
max_output_tokens
token_count_mode
tokenizer_name
context_metadata_source
```

所以当前模型表不足以支持按模型设置安全的上下文预算。

需要区分“模型事实”和“运行策略”：

| 类型 | 建议归属 |
| --- | --- |
| 上下文窗口、最大输出、Tokenizer、元数据来源 | `llm_models` |
| 是否压缩、触发比例、目标比例、保留最近轮次 | Agent 或全局运行策略 |

不建议把 `compaction_trigger_tokens`、`compaction_target_tokens` 全部固化为模型事实。相同模型在不同 Agent 上可能需要不同策略；第一版可以用全局默认比例，根据模型窗口动态计算，等出现真实差异再增加 Agent 级配置。

### 7.6 OpenAI-compatible、DeepSeek 和私有模型计数

推荐顺序：

1. Provider 提供并被 Pydantic AI 支持的请求前计数 API。
2. 已明确模型族和版本时，使用对应官方 Tokenizer callable。
3. 私有模型没有可靠 Tokenizer 时，使用经过实际 Usage 校准的保守估算。
4. 完全未知时使用保守字符估算并留安全余量。

注意：

- OpenAI-compatible 只表示 API 兼容，不代表使用 OpenAI Tokenizer。
- DeepSeek 不应默认套用 `tiktoken`。
- Harness 默认约 4 字符一个 Token。DeepAgent 有大量中文、JSON、路径和 Tool Schema，这个默认值可能低估 Token，必须通过真实 Provider Usage 做样本校准并增加安全系数。
- Core `count_tokens_before_request=True` 当前只对 Anthropic、Google、Bedrock Converse 和 OpenAI Responses 等明确支持的 Provider 可用，不能当作所有私有模型的统一方案。

## 8. Harness 0.8.0 能力对照

| 能力 | Harness 提供什么 | DeepAgent 当前情况 | 推荐 |
| --- | --- | --- | --- |
| `TieredCompaction` | 清理、去重、滑窗、总结的分层编排；保持 Tool Call/Return 配对 | 完整装载 SQL 历史，无压缩 | 优先试点 |
| `OverflowingToolOutput` | 工具返回生成时 Truncate/Spill/Summarize，支持受控回读 | 缺少通用上限 | 有共享存储方案后试点 |
| `Memory` | 命名空间 Notebook、读写搜索工具、CAS 和幂等 | 没有跨会话长期记忆 | 有明确产品需求后接入 |
| `InputGuard` | 请求前 allow/block/replace | 主要依赖 API 校验和 Prompt | 可用于凭据/范围策略 |
| `OutputGuard` | 最终输出 allow/block/replace/retry | SSE 已把片段发给前端 | 不适合要求严格撤回的流式输出 |
| `StepPersistence` | StepEvent、安全快照、ToolEffect、continue/fork | 已有 SQL 消息、SSE、Pending | 暂不接入 |
| Planning/SubAgents | 计划和委派 | 无明确产品需求 | 暂不接入 |
| FileSystem/Shell/CodeMode | Coding Agent 工具 | DeepAgent 无通用沙箱权限模型 | 不作为业务 Agent 默认能力 |
| Skills | 0.8.0 wheel 未发布 | DeepAgent 已有两类 Skill | 保留自研，后续再评估 |

## 9. Harness 接入的真实边界

### 9.1 `TieredCompaction`

Harness 的合理默认是：

```text
ClampOversizedMessages（可选）
  -> ClearToolResults
  -> SummarizingCompaction
  -> 直到低于 target_tokens
```

它比 DeepAgent 自己重写一套摘要器更值得采用，因为已经处理 Tool Call/Return 配对、增量摘要、Usage 计入和观测 Hook。

但存在一个关键适配点：Harness 修改的历史只会在当前 `Agent.run` 内持续生效；DeepAgent 成功后只追加 `result.new_messages()`，下一轮又从 SQL 读取完整原始历史。直接挂 Capability 会导致：

- 当前工具循环变短。
- SQL 原始历史不变。
- 下一轮可能重新对同一段历史压缩和付费总结。

因此正式方案必须保留 SQL 原始历史，同时增加“派生 LLM 上下文快照”，至少记录：

```text
agent_id / session_id
through_sequence
strategy_version
summary_or_compacted_messages
estimated_tokens
source_history_hash
updated_at
```

下一轮从“有效快照 + 截止 sequence 后的新消息”构建 `message_history`。原始历史仍用于页面、审计、分支和恢复。

### 9.2 Tool Call/Return 与 Pending HITL

Harness Compaction 声明会保持 Tool Call/Return 配对，但 DeepAgent 还要增加自己的保护规则：

- 未完成的 Deferred Tool Call 和当前 Pending 尾部永不压缩。
- 同一 `tool_call_id` 的 call/result/retry 必须作为一个原子组处理。
- 已被 DeepAgent 拒绝且不应进入历史的非法 UI 中间调用不能被摘要重新引入。
- 分支重发时，快照必须按 `through_sequence` 和历史哈希失效。
- 摘要不能改变前端历史展示，只改变送模上下文。

### 9.3 `OverflowingToolOutput`

该能力在工具返回生成时处理大结果，避免完整结果在后续每次请求中重复付费。它提供：

- `Truncate`：简单但有损。
- `Summarize`：有损且增加模型请求。
- `Spill`：完整结果外置，模型通过 `read_tool_result` 分段回读。

DeepAgent 不能直接使用默认本地文件 Store 上生产：

- Kubernetes 多实例无法保证下一个请求落到同一 Pod。
- 本地文件默认持续保留，需要 TTL 和清理。
- 句柄必须绑定 environment/user/agent/app/space，避免越权读取。

建议先让业务查询工具自身返回分页/摘要，再把 `OverflowingToolOutput` 作为最后防线。需要无损 Spill 时实现共享对象存储或数据库 Store；在此之前只对明确工具小范围使用受控截断。

### 9.4 Memory

Harness Memory 提供的价值不是“保存聊天记录”，而是给 Agent 一个跨 Run 的、可更新和检索的 Notebook。适合：

- 用户明确保存的偏好。
- 常用 Cube、命名习惯和工作约定。
- Agent 可复用但不应每轮塞满提示词的笔记。

不适合：

- 把整个会话历史复制成 Memory。
- 保存 Token、Cookie、API Key。
- 未经用户感知地自动吸收所有业务数据。

命名空间至少应为：

```text
environment / authenticated_user / agent / app / space
```

命名空间必须由 `AgentDeps` 解析，不能成为模型工具参数。Harness 0.8.0 内置 InMemory、File、SQLite、PostgreSQL Store，没有 MySQL Store；DeepAgent 若继续使用 MySQL，需要实现自定义 Store 或使用独立 PostgreSQL，不能在多 Pod 环境依赖本地 SQLite。

### 9.5 Guardrails

可优先考虑：

- 输入中凭据或高风险字段的阻断/替换。
- 按 `AgentDeps` 做租户、权限和工具访问策略。
- 非流式结构化输出的最终校验。

限制：`OutputGuard` 在流式完成后才看到完整输出。已经通过 SSE 发给前端的 Chunk 无法撤回，流式下也不支持 output retry。因此它不能代替 DeepAgent 的流式内容安全策略。

### 9.6 StepPersistence

StepPersistence 的增量价值是：

- append-only StepEvent。
- provider-valid ContinuableSnapshot。
- ToolEffect started/completed/failed 账本。
- 崩溃后识别“副作用可能已经发生但结果未知”。

它不会恢复：

- 原 Python 协程或 SSE 连接。
- Capability 内部状态和重试计数。
- Pending 表单 UI。
- 用户、Agent、App、Space 权限。
- 工具副作用的自动幂等。

DeepAgent 当前已经用 SQL 消息、EventPersister 和 Deferred 恢复覆盖了大部分正常路径。除非后续出现以下场景，否则不值得再维护第二份运行事实：

- 大量写入型工具，需要崩溃后判断是否重复执行。
- 子 Agent/长工作流，需要安全快照和 lineage。
- 需要逐步骤审计，而不仅是聊天消息历史。

即使接入，也应作为诊断/副作用账本，不能替代 `chat_sessions/chat_messages`。Harness 内置 Step Store 只有 Memory/File/SQLite；MySQL 需要自定义 Store。

## 10. 推荐保留、替换和新增

### 10.1 继续保留

- SQL 会话、消息、用户隔离和会话 API。
- SSE 协议、EventPersister、取消和历史重放。
- Deferred HITL、UI 工具、Pending 刷新恢复。
- Agent、模型、MCP、UI 工具和辅助模型配置中心。
- 请求 Header 透传、安全白名单和 MCP 工具前缀。
- 上传、附件、标题和 Console。
- 当前 Skill 加载器。

### 10.2 优先使用官方能力，避免自研重复机制

- 历史压缩：Harness `TieredCompaction`。
- 通用大工具输出治理：Harness `OverflowingToolOutput`。
- 大工具集发现：Core `ToolSearch`。
- 请求级工具过滤：Core `PrepareTools`。
- 生命周期观测：Core Hooks/Instrumentation。
- 跨会话 Notebook：Harness Memory。

### 10.3 需要 DeepAgent 做薄适配

- 从模型元数据计算上下文预算。
- 将 Compaction 结果持久化为派生上下文快照。
- 为摘要选择主模型或辅助模型。
- 将 Memory namespace 绑定认证用户和 App/Space。
- 为 Overflow Spill 提供共享、鉴权、TTL Store。
- 把压缩、Memory 和 Guardrails 的状态映射到现有日志/Console。

## 11. 分阶段实施方案

### 第一阶段：模型上下文元数据和 Token 观测

目标：先知道每个模型能装多少、实际用了多少、Usage 是否可信。

建议：

1. 锁定经过测试的 Pydantic AI 版本，避免 `>=1.0` 漂移。
2. 为 `llm_models` 增加可空模型事实字段：
   - `context_window_tokens`
   - `max_output_tokens`
   - `token_count_mode`
   - `tokenizer_name`
   - `context_metadata_source`
3. 扩展模型元数据刷新，但手工配置优先于自动结果。
4. 每轮记录：原始历史字符/消息数、工具结果字符、估算输入 Token、Provider 实报 Usage、模型请求数和工具调用数。
5. 为 Usage 增加 `provider_reported / estimated / missing` 状态，0 Token 不再默认解释为真实 0。
6. 先通过日志和观测验证，不强制改变历史。

DDL 风险：需要新增 Alembic 迁移，当前最新为 `018`，预计使用 `019`。字段均可空，结构风险低；但 DeepAgent 启动会自动 `upgrade head`，连接共享 Test 库时必须协调服务版本，不能让旧实例长期访问新旧不一致 Schema。

Usage 历史持久化不是压缩的强制前置。第一版可先记录结构化日志；如果需要按会话做成本报表，再设计单独的 Run Usage 表，不建议把运行级 Usage 硬塞进某条聊天消息。

影响文件预计：

```text
requirements.txt / 依赖锁文件
deep_agent/db/models.py
deep_agent/schemas/llm_model.py
deep_agent/db/stores/llm_model/
deep_agent/agent/spec.py
deep_agent/agent/models/
deep_agent/api/v1/llm_models.py
deep_agent/api/v1/deps.py
deep_agent/agent/runner.py 或独立 observability capability
frontend/console/configuration/
alembic/versions/019_*.py
tests/unit/ + tests/integration/
```

### 第二阶段：跨模型 `TieredCompaction`

目标：DeepSeek、OpenAI-compatible 和私有模型也能获得应用侧压缩。

建议：

1. 固定 `pydantic-ai-harness==0.8.0` 与已验证的 Core 版本；通过 Feature Flag 只给一个测试 Agent 开启。
2. 先启用 `ClearToolResults`，保留最近若干 Tool Pair。
3. 仍超预算时启用 `SummarizingCompaction`。
4. 摘要模型优先复用现有辅助模型体系，新增 `context_summary` use case；该表本身无需新字段，只需扩展已知 use case 和默认配置。
5. 为摘要请求预留 `request_limit`，避免压缩本身把 Agent 提前顶到上限。
6. 增加派生上下文快照，避免每轮重复压缩完整 SQL 历史。
7. Provider 原生 `OpenAICompaction` / `AnthropicCompaction` 只对明确支持的模型启用；同一模型不要再叠 Harness 总结压缩。

派生快照可以单独建表，也可以先用短期内存验证。正式生产更建议独立表，避免污染原始聊天消息和前端历史。该阶段 DDL 风险高于第一阶段，因为涉及快照失效、分支和历史一致性。

### 第三阶段：Memory

只在产品明确以下问题后实施：

- 什么内容允许记忆。
- 谁可以查看、编辑和删除。
- 记忆是用户主动保存，还是模型建议后确认。
- App/Space 切换时是否共享。
- 保留期限和敏感数据策略。

技术上优先复用 Harness Memory；DeepAgent 负责 namespace、权限、管理 API 和 Console。若使用当前 MySQL，需要自定义 Memory Store；若使用 PostgreSQL，可直接评估 Harness `PostgresMemoryStore`。

### 最后评估：StepPersistence

只有当崩溃后副作用未知、子 Agent 或长工作流成为真实问题时再做。不要因为“官方有”就复制一套步骤库。

## 12. 测试范围

### 12.1 模型元数据和 Usage

- 模型 CRUD、自动刷新和手工覆盖优先级。
- 缺失 context window 时使用保守默认并给出明确状态。
- Provider 实报、缺失、0 值和估算状态区分。
- OpenAI-compatible/DeepSeek/private 模型样本校准。
- `request_limit`、输入/输出/总 Token 限制的错误展示。

### 12.2 Compaction

- 低于阈值历史完全不变。
- 旧 Tool Result 被清理，最近 Pair 保留。
- call/result/retry 配对不破坏。
- Pending Deferred 尾部不压缩。
- 非法 UI 中间调用不会重新出现。
- 分支重发使过期快照失效。
- 刷新页面的产品历史仍完整。
- 下一轮使用快照，不重复总结同一历史。
- 摘要请求计入 Usage 和 request limit。
- 原生 Provider 压缩与 Harness 压缩不会同时启用。

### 12.3 Overflowing Tool Output

- 结构化 JSON 不被截成非法 JSON。
- Spill 句柄只能由同一权限命名空间读取。
- 多 Pod 可以读取同一结果。
- TTL、清理和 Store 故障回退。
- `ModelRetry` 和工具错误保持完整。

### 12.4 Memory

- user/agent/app/space 严格隔离。
- 模型不能通过参数选择其他 namespace。
- 并发写冲突和幂等重放。
- 敏感内容阻断、删除和审计。
- 自动注入预算上限。

### 12.5 现有能力回归

- 普通/流式聊天。
- SSE、Thinking、工具调用和取消。
- 会话标题、历史、删除和用户隔离。
- 合法/非法 HITL、Pending 刷新恢复。
- MCP Header、工具前缀和 UI 工具。
- 附件、模型切换和分支重发。

## 13. 剩余待确认问题

1. 第一批启用压缩的 Agent 和模型是哪一个，真实 context window 是多少。
2. Test/生产 Provider 是否稳定返回 input/output Usage；缺失比例是多少。
3. 压缩摘要用主模型，还是新增 `context_summary` 辅助模型。
4. 允许清空哪些工具结果，哪些结果必须永久保留原文供模型使用。
5. 派生上下文快照是否需要跨环境迁移，还是可随时重建。
6. 是否已有共享对象存储可承载 Overflow Spill。
7. Memory 是否已有明确用户场景；如果只是“以后可能有”，先不建表。
8. 是否需要按会话展示历史 Usage；如果不需要，先用结构化日志即可。

## 14. 最终判断

DeepAgent 不是“Pydantic AI 用得太少”，而是已经基于 Core 构建了在线产品层，但缺少成熟的上下文治理和运行观测。

最省力且风险最低的方向不是用 Harness 推倒现有功能，而是：

```text
保留 DeepAgent 产品层
  + 用 Harness 接管通用上下文压缩
  + 用 Harness 接管通用大工具返回治理
  + 有明确需求时用 Harness Memory
  + 暂不引入 StepPersistence 双状态源
```

这样可以减少最难维护的通用 Agent 机制自研，同时不破坏已经跑通的会话、SSE、MCP 和 HITL。

## 15. 官方资料

- [Pydantic AI Capabilities](https://ai.pydantic.dev/capabilities/)
- [Pydantic AI Toolsets](https://ai.pydantic.dev/toolsets/)
- [Pydantic AI Message History](https://ai.pydantic.dev/message-history/)
- [Pydantic AI Usage Limits](https://ai.pydantic.dev/agent/#usage-limits)
- [Pydantic AI MCP Client](https://ai.pydantic.dev/mcp/client/)
- [Pydantic AI Deferred Tools](https://ai.pydantic.dev/deferred-tools/)
- [Pydantic AI OpenAI Model](https://ai.pydantic.dev/models/openai/)
- [Pydantic AI Anthropic Model](https://ai.pydantic.dev/models/anthropic/)
- [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)
- [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview)
