# DeepAgent 上下文压缩设计

> 状态：已实现并合入主开发分支
>
> 更新日期：2026-07-30
>
> 依赖组合：`pydantic-ai-slim 2.14.1` + `pydantic-ai-harness 0.10.0`
>
> 数据库迁移：v0.3.0 完整基线 Alembic `001`

## 1. 最终口径

DeepAgent 使用 Pydantic AI Harness 的确定性压缩能力，并由 DeepAgent 补齐安全摘要、
Checkpoint、统一 Token 估算和 SSE 观测。产品会话与模型输入明确分层：

```text
完整事实源
chat_messages
    │
    ├─ 最新 Checkpoint.payload
    └─ Checkpoint 覆盖范围之后的原始消息
            │
            ▼
      本轮模型输入投影
```

边界如下：

1. `chat_messages` 始终保存完整原始历史。压缩不得删除、替换或重写原始消息。
2. `chat_context_checkpoints` 是模型输入的派生投影，同一 `(agent_id, session_id)` 只有一行。
3. Checkpoint 更新时整体覆盖，不累积 `S0、S1、S2` 摘要列表。
4. 压缩默认自动启用，不提供环境变量、Agent 或数据库开关。
5. 模型没有配置 `context_window_tokens` 时不启用压缩，也不按模型名称猜窗口。
6. 摘要始终使用当前 Agent 主模型，不再提供 `context_summary` 辅助模型用途。
7. Harness 暂不升级；当前版本已支持自定义 tokenizer。
8. `context_window_tokens` 和 `max_output_tokens` 是模型事实；本轮输出预留和安全余量是内部算法，
   不新增配置字段。
9. 摘要调用与正式回答共用本轮 `RunUsage` 和全局 `UsageLimits`，计入请求次数与 Token。
10. Console 只显示当前请求的瞬时“正在压缩”状态，不把状态写入历史。

## 2. 为什么需要派生投影

原链路会读取会话的全部历史并传给模型：

```text
MessageStore.get_history()
→ AgentRunner._prepare_history()
→ resolve_history_for_llm()
→ Agent.run / run_stream_events
→ append(result.new_messages())
```

长会话的主要风险是 Token 而不是消息条数：

- 工具结果和附件工具分页读取的文件内容可能远大于普通对话。
- 不同模型的总窗口可能相差数十倍。
- Provider usage 只能在响应后获得，不能挽救已经超窗的请求。
- 直接修改历史会破坏回放、编辑分支、HITL、审计和 Tool Call/Result 配对。

因此压缩只能发生在“原始历史”到“本轮模型请求”的派生阶段。

## 3. 模型字段和动态输出预算

### 3.1 持久化模型事实

`llm_models` 只保存稳定事实：

| 字段 | 语义 |
| --- | --- |
| `context_window_tokens` | Provider 声明的输入与输出共享总窗口 |
| `max_output_tokens` | Provider 声明的单次最大输出能力上限 |
| `input_modalities` | 文本、图片等输入模态 |
| `tags` | 推理、工具调用、结构化输出等能力标签 |

不新增或持久化：

- `max_input_tokens`
- 本轮预计输出 Token
- 本轮安全余量
- Tokenizer 名称
- 估算校准系数
- `context_budget_config`

例如总窗口为 1M、最大输出为 384K，并不表示普通问答必须固定预留 384K。

### 3.2 输出 Token 预测

预测样本优先从完整原始 `chat_messages` 中最近最多 8 个 `ModelResponse` 读取：

```text
usage.output_tokens > 0
├─ 是：采用 Provider 真实值，usage_source=provider
└─ 否：用统一 TokenEstimator 估算响应内容，usage_source=estimated
```

预测规则：

```text
无样本：内部下限 2048
1 个样本：max(2048, 样本)
2~8 个样本：max(2048, 上四分位数 P75)
最终预测：min(预测值, max_output_tokens)
```

`max_output_tokens` 只是模型硬上限，不是固定预留。

### 3.3 统一 TokenEstimator

预算判断、Harness tier、摘要请求预算、Checkpoint 统计和摘要后复检使用同一估算器。

- 仅对可确认的 OpenAI 官方模型使用 `tiktoken`。
- 私有模型和 OpenAI Compatible 服务不能因为协议兼容就默认套用 OpenAI tokenizer。
- 未知模型使用保守回退：
  - CJK 字符约 1 字符 1 Token。
  - 普通英文数字约 4 字符 1 Token。
  - 代码、JSON、标点、Emoji 和复杂 Unicode 使用更保守比例。
  - 额外计入消息、Part、请求包、工具定义、输出 Schema 和媒体占位开销。

运行观测记录：

```text
usage_source = provider | estimated
estimator_source = model_tokenizer | conservative_fallback
```

这些来源不新增数据库字段，也不修改 Provider 原始 usage。

### 3.4 本轮预算

```text
safety_margin_tokens
  = max(1024, 完整请求估算 × 估算器误差比例)

usable_request_tokens
  = context_window_tokens
  - predicted_output_tokens
  - safety_margin_tokens

trigger_tokens = usable_request_tokens × 0.80
target_tokens  = usable_request_tokens × 0.55
```

使用模型 tokenizer 时误差比例为 5%，保守回退时为 10%。完整请求估算包含消息、系统指令、
工具 Schema、原生工具、输出 Schema 和请求包装，不只统计聊天正文。

## 4. 正确的压缩执行顺序

不是先裁剪再决定是否压缩，而是：

```text
组装完整模型输入
→ 估算完整请求和动态输出预算
→ 未超过 80%：直接请求模型
→ 超过 80%：进入压缩链
   ├─ ClampOversizedMessages
   ├─ DeduplicateFileReads
   ├─ ClearToolResults
   ├─ 主模型安全滚动摘要
   └─ Sliding Window 失败兜底
→ 每层后重新估算
→ 低于 55% 目标线立即停止
→ 正式请求主模型
```

确定性层只影响当前模型输入。只有可靠摘要成功并达到目标线，才允许更新 Checkpoint。

### 4.1 ClampOversizedMessages

- 处理异常大的单个消息 Part。
- 单 Part 上限取目标预算的 40%。
- 保留头尾各 2000 字符。
- 使用统一 TokenEstimator。

### 4.2 DeduplicateFileReads

只对能计算稳定资源 Key 的读取结果去重：

- `read_file`
- `read_mcp_resource`

不对搜索、财务查询、写操作、审批和普通 MCP 工具做名称猜测式去重。

### 4.3 ClearToolResults

- 默认完整保留最近 3 组 Tool Call/Result。
- 预计收益不足 2000 Token 时不清理。
- 不拆散 Tool Call/Result 配对。
- 排除 `builtin__confirm`、`builtin__choice`、`deep_cube__clarify`。

### 4.4 主模型安全滚动摘要

确定性压缩仍高于目标线时才摘要。摘要模型就是本轮主模型。

设组合历史为：

```text
[旧摘要 S0]
[首条用户任务 U0]
[Checkpoint 保留历史 H]
[Checkpoint 后新增历史 N]
```

找到安全分割点后：

```text
待摘要前缀 = messages[:cutoff]
最近保留尾部 = messages[cutoff:]
```

生成：

```text
S1 = summarize(S0 + 新纳入摘要的旧历史)
```

新投影：

```text
[S1]
[U0]
[最近保留尾部]
```

旧摘要只在 `<previous_summary>` 中出现一次；构造 `to_summarize` 时必须移除旧摘要、系统指令
和单独保留的首条用户任务。

### 4.5 防止摘要请求自身超窗

每次摘要单独计算预算：

```text
摘要请求 = 摘要系统指令 + 旧摘要 + 当前历史块 + 格式化标签
可用范围 = 模型总窗口 - 动态摘要输出上限 - 安全余量
```

待摘要内容过大时，按安全消息边界滚动：

```text
S0 + 第一块 → 临时摘要 S1
S1 + 第二块 → 临时摘要 S2
S2 + 第三块 → 最终摘要 S3
```

- 单次摘要不拆散 Tool Call/Result。
- 单轮最多 4 个摘要块、最多 5 次摘要请求。
- 摘要输出上限由目标预算动态计算，最多 4096 Token，并受模型 `max_output_tokens` 限制。
- 摘要调用前为正式回答保留至少一次全局请求额度。
- 摘要超时、Provider 异常、块过大或次数不足时进入 Sliding Window。

### 4.6 摘要后复检

```text
target_met = estimated_tokens_after <= target_tokens
reduction_ratio = 1 - after / before
```

第一次摘要未达标时会用更短保留尾部再尝试一次。仍不达标则：

1. 本轮使用 Sliding Window。
2. 标记 `target_met=false` 或记录兜底结果。
3. 不保存新 Checkpoint。

如果首条任务、当前用户输入和必要系统/工具定义本身已经超过可用窗口，返回明确错误，要求用户
缩短内容、拆分附件或更换模型，不静默截断当前输入。

## 5. Checkpoint

### 5.1 读取

```text
读取完整 raw history
→ 读取 checkpoint
→ 校验 payload schema version
→ 校验 projection fingerprint
→ 校验 source_last_sequence
→ 校验原始历史前缀 hash
→ 反序列化 checkpoint payload
→ checkpoint payload + raw tail
```

任一校验失败都回退完整原始历史，派生状态损坏不得阻断会话。

### 5.2 写入条件

只有以下条件全部成立才保存：

- 本轮正常完成。
- 不是 Pending、取消或异常。
- 原始新消息已经持久化。
- 确实生成或更新了摘要。
- 摘要调用成功。
- 最终投影达到目标线。
- 未使用 Sliding Window。
- 脱敏 payload 能重新反序列化。

以下情况不更新：

- 未到触发线。
- 只执行确定性压缩。
- 摘要失败、超时或未达目标。
- Sliding Window 兜底。
- Pending、取消或运行异常。

保存继续使用 `row_version` CAS。CAS 冲突或派生状态保存失败只记录告警，不让已完成对话失败。

### 5.3 安全与失效

Checkpoint：

- 移除 Provider 元数据。
- 省略原始媒体内容和临时签名 URL。
- 保留安全的附件引用 ID、名称和 MIME，便于后续重新解析。
- 对 Token、Cookie、Authorization、API Key 等字段与 Bearer 文本脱敏。
- 写入前执行序列化后再反序列化验证。

以下操作删除 Checkpoint：

- `branch_from` 截断历史。
- `replace_history`。
- 删除会话。

模型、窗口、最大输出、估算器或压缩策略变化时，`projection_fingerprint` 改变，旧投影自动失效。

## 6. Usage 与观测

Pydantic AI 的每次模型调用都会产生 `ModelResponse`。Provider 返回的 usage 随完整响应序列化进：

```text
chat_messages.payload.usage
```

包括工具调用中间响应。恢复历史后可以继续读取这些样本。第一版不新增 usage 表：

- 输出预测优先读取完整原始历史，避免 Checkpoint 裁剪影响样本。
- Provider usage 缺失或为 0 时由 TokenEstimator 回退估算。
- 一次 Run 的汇总 `RunUsage` 继续通过 REST/SSE 返回，不额外落库。
- 摘要响应不写入原始 `chat_messages`，但摘要 usage 合并进本轮 `RunUsage`。
- 未来只有成本、计费或跨会话统计需要时，才考虑专门调用账本。

最终 `context` 观测包括：

- 完整请求估算、消息估算与工具 Schema 估算。
- 输出预测值、样本数、Provider/估算样本数和来源。
- estimator 来源。
- trigger、target、压缩前后 Token、缩减比例和策略。
- 摘要次数、分块数、失败原因和是否达标。
- Provider input usage 与估算误差（Provider 提供时）。

## 7. SSE 与 Console

流式接口新增应用事件：

```json
{
  "type": "context_compaction",
  "phase": "started | completed | fallback",
  "message": "正在压缩上下文…"
}
```

事件顺序：

```text
session
→ context_compaction.started
→ context_compaction.completed 或 fallback
→ thinking / text / tool_call
→ final
```

Console：

- 延迟 150ms 显示，避免极快压缩造成闪烁。
- 同一请求最多显示一个胶囊状态，并随 phase 更新。
- 收到首个 `thinking`、`text` 或 `tool_call` 后移除。
- `final`、`error`、`cancelled`、主动停止和异常清理时兜底移除。
- 不把状态写入会话历史，刷新后不会回放。

`GET /api/v1/sessions/{session_id}/context-status` 继续提供只读 Checkpoint 与预算状态，不返回
摘要正文或原始历史。该接口以 `estimate_scope=history_only` 标识会话历史快照，并直接返回
后端预算器计算的 `safety_margin_tokens` 与 `usable_request_tokens`；模型窗口未知时二者为
`null`。Console 只能展示这些值，不得重新实现动态输出预测或安全余量算法。实际完成 Run 的
预算和压缩结果仍以 SSE `final.context` 为准。

## 8. 配置

保留的全局环境变量：

```dotenv
DEEP_AGENT_CONTEXT_COMPACTION_TRIGGER_RATIO=0.80
DEEP_AGENT_CONTEXT_COMPACTION_TARGET_RATIO=0.55
DEEP_AGENT_CONTEXT_KEEP_TOOL_PAIRS=3
DEEP_AGENT_CONTEXT_MIN_CLEAR_TOKENS=2000
DEEP_AGENT_CONTEXT_SUMMARY_TIMEOUT=30
```

已删除且不得恢复：

```dotenv
DEEP_AGENT_CONTEXT_COMPACTION_ENABLED
DEEP_AGENT_CONTEXT_OUTPUT_TOKEN_FLOOR
```

2048 输出预测下限属于内部算法。压缩是否启用只取决于模型是否配置有效的
`context_window_tokens`。

## 9. 数据库与发布

- 完整基线 `001` 已包含 `chat_context_checkpoints`；后续优化不新增迁移。
- `summary_model_id` 继续记录本轮主模型配置 ID，不再表示辅助摘要模型。
- 辅助模型目录只保留 `title`；读取辅助模型配置时会删除旧版遗留的 `context_summary` 行。
- 配置备份只创建 Checkpoint 表结构，不复制会话或 Checkpoint 数据。
- 本地验证只连接保留的 SQLite，不连接共享 Test。
- 发布时 ORM、Runner、API、Console 和文档必须同版本交付。

## 10. Console 测试会话

为避免人工逐轮输入数十次才能复现压缩，Workspace 在会话搜索框和普通“新建会话”
按钮之间提供独立的“新建测试会话”入口。普通新建逻辑不变。

入口是管理员控制台的常驻功能，不再增加环境变量开关。当前已选择 Agent，且 Agent
使用的模型配置了有效 `context_window_tokens` 时可以创建测试会话；模型未配置上下文
窗口时，后端返回明确的 422。

交互链路：

```text
点击测试会话图标
→ GET /api/v1/context-test-scenarios?agent_id=<当前 Agent>
→ 弹窗显示当前模型预算和纯对话场景
→ 选择场景并确认
→ POST /api/v1/context-test-scenarios/{scenario_key}
→ 后端按当前模型实时 trigger 动态生成合法 ModelRequest / ModelResponse
→ 原子写入 chat_sessions / chat_messages
→ Console 打开新会话并预填推荐验证问题
```

首版提供长中文记录、多轮决策追踪、中英代码混合三类场景，不生成 Tool Call/Result，
不伪造 Checkpoint，也不直接执行模型。生成历史会超过当前实时触发线约 8%，用户发送弹窗
预填的问题后，压缩仍通过正式 Runner 链路发生。

测试会话不是新增的数据类型：它使用普通会话表、当前用户、当前 Agent 和现有消息
序列化器，标题以 `【压缩测试】` 标识，可按普通会话删除。因此不增加数据库字段或
Alembic。场景构造器必须通过“序列化后可反序列化、没有工具消息、超过实时触发线”的
自动测试；未来 Pydantic 消息结构变化时，应先更新构造器和对应测试。

## 11. 验收场景

后端至少覆盖：

1. Checkpoint payload 与 raw tail 正确组装。
2. 旧摘要在摘要 Prompt 中只出现一次。
3. 新摘要替换旧摘要，不形成摘要列表累积。
4. Checkpoint 整行覆盖并增加 `row_version`。
5. 原始 `chat_messages` 不被压缩改写。
6. 中文、英文、代码、JSON 和混合内容估算。
7. 私有 OpenAI Compatible 模型使用保守回退。
8. Provider usage 落库、恢复与最近 8 个输出样本。
9. 摘要请求始终低于模型窗口。
10. 超大待摘要历史安全分块。
11. 分块不拆 Tool Call/Result。
12. 摘要输出具有内部动态上限。
13. 摘要后重新验证 `target_met`。
14. 摘要失败、超时或未达目标时兜底且不写 Checkpoint。
15. 单条受保护输入超窗时返回明确错误。
16. 主模型摘要计入本轮 usage 和全局请求次数。
17. 普通请求与 SSE 行为一致。
18. SSE 压缩状态先于正式模型输出，且不进入历史。
19. 三类测试场景都能按当前模型预算生成超过触发线的合法纯对话历史。
20. 测试会话归属当前用户和 Agent，并可按普通会话删除。

前端至少覆盖：

1. `started`、`completed`、`fallback` 文案。
2. 150ms 延迟与单实例更新。
3. 首个正式输出和各种结束路径清理。
4. 既有 SSE、HITL、取消和历史回放不回归。
5. 测试入口位于搜索框与普通新建之间，普通新建按钮不变。
6. 场景确认后创建并打开会话，同时预填推荐验证问题。

实现变更继续要求运行目标 Python 测试、完整 Python 测试、Console Node 测试、Alembic
upgrade/downgrade、配置备份测试和 `git diff --check`。后续待办见
[`../TODO.md`](../TODO.md)。
