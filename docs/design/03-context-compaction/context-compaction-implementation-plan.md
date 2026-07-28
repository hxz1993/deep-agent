# DeepAgent 上下文压缩实施方案

> 状态：已在 `fix-ruge-0829-context-compaction` 实施，默认关闭，待部署验证
> 更新日期：2026-07-29
> 依赖组合：`pydantic-ai-slim 2.14.1` + `pydantic-ai-harness 0.10.0`
> 数据库迁移：Alembic `022 → 023`

## 1. 结论

DeepAgent 使用 Pydantic AI Harness 做模型输入历史压缩，但继续由 DeepAgent 管理产品会话、
身份、SSE、HITL 和数据库。

核心边界：

1. `chat_messages` 永远保存完整原始历史，是回放、分支、审计和 Pending 恢复的事实源。
2. 压缩结果只是一份给模型使用的派生投影，保存在 `chat_context_checkpoints`。
3. 确定性清理不会单独更新 Checkpoint；只有真正生成或更新 LLM Summary 后才保存。
4. 模型的 `context_window_tokens` 和 `max_output_tokens` 是容量事实，不是压缩策略。
5. 输出预留量每轮动态估算。模型最大输出只作为上限，不能直接从总窗口中全额扣除。
6. 模型没有配置上下文窗口时自动停用压缩，不猜测模型名称，不阻断对话。
7. 第一版只有全局环境变量，不在 Agent 上增加 `compaction_config`。

默认策略：

```text
Harness TieredCompaction
├─ ClampOversizedMessages
├─ DeduplicateFileReads
├─ ClearToolResults
└─ SummarizingCompaction

Summary 或分层压缩异常
└─ SlidingWindow 兜底
```

功能默认通过 `DEEP_AGENT_CONTEXT_COMPACTION_ENABLED=false` 关闭。完成部署环境验证后再显式开启。

## 2. 前因

原运行链路会在每轮读取当前会话的全部 `chat_messages`，再原样传给 Pydantic AI：

```text
MessageStore.get_history()
  → AgentRunner._prepare_history()
  → resolve_history_for_llm()
  → Agent.run / run_stream_events(message_history=完整历史)
  → result.new_messages()
  → 追加本轮原始消息
```

长会话的主要风险不是消息条数，而是历史 Token：

- 工具返回可能远大于普通对话。
- 不同模型的总窗口可能相差数十倍。
- 模型返回后的 Usage 无法挽救已经超窗的本次请求。
- 直接覆盖历史会破坏 Console 回放、编辑分支、工具配对、HITL 和审计。

因此需要在“产品历史”和“模型输入历史”之间增加派生投影。

## 3. 模型字段与输出预算

### 3.1 持久化字段

0.2.2 已在 `llm_models` 保存：

| 字段 | 语义 |
| --- | --- |
| `context_window_tokens` | Provider 声明的输入与输出共享总窗口 |
| `max_output_tokens` | Provider 声明的单次最大输出能力上限 |
| `input_modalities` | 文本、图片等输入模态 |
| `tags` | 推理、工具调用、结构化输出等能力标签 |

不新增和不持久化：

- `max_input_tokens`
- “本轮输出预留”
- Tokenizer 名称
- 估算校准系数
- `context_budget_config`
- 模型属性来源

这些值不是稳定模型事实。特别是：

```text
总窗口 1M、最大输出 384K
```

不代表每轮必须预留 384K。如果这样做，普通问答也会无意义地失去 384K 输入空间。

### 3.2 首版动态预测

本轮输出预估：

```text
没有历史 Usage：
  predicted_output_tokens = 全局输出预估下限

有历史 Usage：
  predicted_output_tokens = 最近 8 个有效输出样本的上四分位数

最终：
  predicted_output_tokens = min(预测值, max_output_tokens)
```

默认输出预估下限为 2048 Token。`max_output_tokens` 只做硬上限。

预算：

```text
safety_margin_tokens
  = max(1024, context_window_tokens × 5%)

usable_request_tokens
  = context_window_tokens
  - predicted_output_tokens
  - safety_margin_tokens

trigger_tokens
  = usable_request_tokens × 0.80

target_tokens
  = usable_request_tokens × 0.55
```

Harness 的 `estimate_token_count()` 采用轻量估算，不能等同于 Provider 的真实计费 Token。
5% 安全余量用于覆盖 Instructions、工具 Schema、Provider 包装和估算误差。后续应根据真实 Usage
观测调整，不为输出预测额外调用一个模型。

## 4. Harness 压缩链

入口位于：

```text
deep_agent/agent/context/
├─ budget.py
├─ compaction.py
├─ checkpoint.py
└─ file_keys.py
```

`DeepAgentContextCompaction` 是 Pydantic AI `AbstractCapability`，由
`deep_agent/agent/factory.py` 注入 Agent 的原生 Capability 列表。

### 4.1 ClampOversizedMessages

- 限制单个异常大的模型消息部分。
- 单部分上限取目标预算的 40%。
- 保留头尾各 2000 字符。
- 不把它当作普通历史裁剪。

### 4.2 DeduplicateFileReads

只对语义稳定、可明确计算资源 Key 的读取工具启用：

- `read_file`
- `read_mcp_resource`

不对搜索、财务查询、写操作、审批和普通 MCP 工具做名称猜测式去重。

### 4.3 ClearToolResults

- 默认完整保留最近 3 组 Tool Call/Return。
- 预计回收不足 2000 Token 时不清理，避免无意义破坏 Prompt Cache。
- 保留 Tool Call/Return 配对。
- 排除 `builtin__confirm`、`builtin__choice`、`deep_cube__clarify`。

### 4.4 SummarizingCompaction

前三层仍不能把历史压到目标预算时才调用摘要模型。

- 使用 Harness 的结构化摘要提示。
- 开启增量摘要。
- 保留首条用户消息。
- 默认摘要模型来自辅助模型场景 `context_summary`。
- 未配置辅助模型时复用当前主模型。
- 摘要调用计入同一轮 Pydantic AI Usage 和请求次数。

### 4.5 SlidingWindow

分层压缩或摘要调用异常时使用。它只保证当前请求能继续，不写新的 Checkpoint。

## 5. Checkpoint 语义

### 5.1 定位

Checkpoint 不是浏览器状态、执行状态或第二份会话：

```text
原始产品历史：chat_messages
派生模型投影：chat_context_checkpoints
```

只有 Summary 真正生成或相对旧摘要发生变化时才更新。确定性 Clamp、去重和工具结果清理
会在需要时重算，不单独持久化。

### 5.2 表结构

Alembic `023` 新增 `chat_context_checkpoints`：

| 字段 | 用途 |
| --- | --- |
| `agent_id` + `session_id` | 联合主键，与会话隔离口径一致 |
| `source_last_sequence` | 投影覆盖到的最后一条原始消息序号 |
| `source_history_hash` | 已覆盖原始历史前缀的 SHA-256 |
| `payload` | 脱敏后的 Pydantic AI 消息投影 |
| `payload_schema_version` | 投影载荷版本 |
| `projection_fingerprint` | 模型、预算策略和摘要模型的策略指纹 |
| `estimated_tokens` | 投影 Token 估算 |
| `summary_model_id` | 实际选用的摘要模型配置 ID，可空 |
| `row_version` | CAS 乐观并发版本 |
| `created_at` / `updated_at` | 创建和更新时间 |

不保存 `source_message_count`，因为 `source_last_sequence + 1` 已表达覆盖数量。

### 5.3 读取

```text
读取完整 raw history
→ 读取 checkpoint
→ 校验 schema version
→ 校验 projection fingerprint
→ 校验 source_last_sequence
→ 校验 raw 前缀 hash
→ 反序列化 checkpoint payload
→ checkpoint projection + raw tail
→ 发送给模型
```

任一校验失败都回退到完整原始历史，派生数据损坏不能阻断对话。

### 5.4 写入

```text
模型成功完成且不是 Pending
→ 本轮 new_messages 先写入 chat_messages
→ 确认本轮实际生成/更新了 Summary
→ 对 result.all_messages() 做脱敏、序列化和反序列化校验
→ 以 row_version 做 CAS 写入 checkpoint
```

禁止未经处理直接持久化 `result.all_messages()`。Checkpoint 会移除 Provider 元数据，隐藏附件
内容和临时签名 URL，并对 Token、Cookie、Authorization、API Key 等字段及 Bearer 文本脱敏。

### 5.5 失效和删除

以下操作在同一个数据库事务中删除 Checkpoint：

- `branch_from` 截断历史。
- `replace_history` 修复或替换历史。
- 删除会话。

模型、窗口、比例、保留工具组数或摘要模型变化时，`projection_fingerprint` 会改变，旧 Checkpoint
自动失效。只追加新消息时不失效，读取时拼接 raw tail。

### 5.6 失败边界

| 场景 | 原始历史 | Checkpoint |
| --- | --- | --- |
| 完成且生成 Summary | 追加完整新消息 | CAS 新建或更新 |
| 只发生确定性压缩 | 追加完整新消息 | 不更新 |
| Summary 失败/超时 | 正常完成则追加 | 不覆盖旧值 |
| Pending HITL | 保存 Pending 原始消息 | 不更新 |
| 流取消 | 保存已产生的原始部分 | 不更新 |
| 模型请求失败 | 按既有规则保存用户输入 | 不更新 |
| Checkpoint 保存失败 | 对话仍成功 | 记录告警 |

## 6. 后端与 Console

### 6.1 全局只读配置

`GET /api/v1/global-settings` 增加“上下文压缩”分组：

- 启用开关
- 触发比例
- 目标比例
- 输出预估下限
- 保留工具结果组数
- 最小清理收益
- 摘要超时

配置仍来自环境变量，修改后重启生效，不写数据库。

### 6.2 辅助模型

`auxiliary_model_configs` 新增内建用途：

```text
context_summary：上下文摘要
```

这里只绑定已有 `llm_models`，不新增模型连接字段。

### 6.3 会话观测接口

新增只读接口：

```text
GET /api/v1/sessions/{session_id}/context-status
```

接口沿用会话所有权校验，只返回：

- 原始消息数和历史 Token 估算
- 上下文窗口、最大输出和动态输出预测
- trigger / target
- Checkpoint 是否存在、覆盖序号、估算 Token、更新时间和摘要模型 ID

不返回摘要正文或原始历史内容。

SSE/普通聊天最终结果增加 `context` 观测对象。Console 请求上下文面板展示预算、是否触发、
压缩前后 Token 和 Checkpoint 状态。

## 7. 配置

```dotenv
DEEP_AGENT_CONTEXT_COMPACTION_ENABLED=false
DEEP_AGENT_CONTEXT_COMPACTION_TRIGGER_RATIO=0.80
DEEP_AGENT_CONTEXT_COMPACTION_TARGET_RATIO=0.55
DEEP_AGENT_CONTEXT_OUTPUT_TOKEN_FLOOR=2048
DEEP_AGENT_CONTEXT_KEEP_TOOL_PAIRS=3
DEEP_AGENT_CONTEXT_MIN_CLEAR_TOKENS=2000
DEEP_AGENT_CONTEXT_SUMMARY_TIMEOUT=30
```

启动校验：

- trigger 和 target 必须在 `(0, 1)`。
- target 必须小于 trigger。
- 输出下限必须为正整数。
- 保留组数和最小清理收益不能为负。
- 摘要超时必须大于 0。

## 8. 迁移与备份

- `023` 只新增 Checkpoint 表，`downgrade` 删除该派生表。
- 配置备份创建表结构，但不复制 `chat_context_checkpoints` 数据。
- `chat_sessions`、`chat_messages` 和 Checkpoint 都属于会话数据，不进入配置备份内容。
- 新版备份 ORM 验证会确认 `ChatContextCheckpoint` 可读取。
- 本地含 `023` 的代码不得连接仍处于 `022` 的共享 Test 并启动，因为应用会自动执行
  `alembic upgrade head`。

发布顺序：

1. 用 `022` 工具备份共享 Test 配置。
2. 在临时 SQLite 完成 `022 → 023 → 022 → 023`。
3. 同一版本交付 ORM、Store、Runner、API、Console、备份工具和 Migration。
4. 部署后确认数据库为 `023`。
5. 先保持全局开关关闭，检查控制台和既有对话回归。
6. 配置摘要模型后在 Test 显式开启，验证长工具会话。
7. 用新版工具再做一次配置备份。

## 9. 已验证效果

仓库单元集成用例构造 8 组长 Tool Call/Return，并通过真实
`pydantic-ai-harness 0.10.0` 执行：

```text
模型输入压缩前：2481 estimated tokens
模型输入压缩后： 326 estimated tokens
压缩比例：       86.9%
```

验证同时确认：

- 8 组原始 Tool Return 全部保留在 `chat_messages`。
- Checkpoint 只保存压缩投影。
- Bearer Token 不进入 Checkpoint。
- 投影内 Tool Call/Return 保持配对。
- 下一轮直接复用 Checkpoint；未生成新 Summary 时 `row_version` 不变化。
- 分支截断会删除 Checkpoint。
- 1M 窗口、384K 最大输出的冷启动预算只预留默认 2048 输出 Token，而不是 384K。

## 10. 后续迭代

第一版暂不做：

- Agent 级压缩策略。
- 目录或模型级 Tokenizer 配置。
- 专门的输出预测模型。
- Provider Native Compaction。
- Harness Memory / StepPersistence。
- 通用大工具结果外置。

后续优先级：

1. 在 Test 收集模型真实 Usage、压缩频率、Summary 耗时和失败率。
2. 建立摘要质量回归样本，重点验证业务约束、文件路径、待办和 Tool 配对。
3. 根据真实误差调整输出下限和安全余量。
4. 若单次 Tool Return 经常超过窗口，再评估 Harness `OverflowingToolOutput` 和共享对象存储。
5. 只有不同 Agent 确实需要不同策略时，再设计 Agent 级配置。
