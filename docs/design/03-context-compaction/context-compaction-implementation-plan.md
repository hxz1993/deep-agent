# DeepAgent 上下文压缩实施方案

> 状态：待评审，尚未接入生产代码  
> 编写日期：2026-07-26  
> 当前基线：DeepAgent `fix-ruge-0717`，`pydantic-ai-slim 2.12.0`  
> 隔离实测：`pydantic-ai-slim 2.14.1` + `pydantic-ai-harness 0.10.0`

## 1. 最终建议

DeepAgent 应接入 Pydantic AI Harness Compaction，但不能只在 `Agent(...)` 里加一个 Capability
就上线。

推荐路线：

```text
模型上下文元数据与 Token 观测
  ↓
ClampOversizedMessages + ClearToolResults 保护层
  ↓
LLM 上下文投影/压缩检查点持久化
  ↓
TieredCompaction 启用 SummarizingCompaction
  ↓
OverflowingToolOutput 外置大型工具结果
```

默认策略：

```text
TieredCompaction
├─ ClampOversizedMessages
├─ DeduplicateFileReads（只对明确映射的读工具）
├─ ClearToolResults
└─ SummarizingCompaction（前三层仍超预算时）
```

不建议：

- 只用 SlidingWindow 作为默认策略：简单但会永久丢失旧业务约束。
- 一达到阈值就总结：成本高，且 Harness 默认只给摘要模型每条 Tool Return 前 500 字符。
- 直接替换 SQL 原始会话：Console 历史、分支、HITL 和审计都依赖完整消息。
- 先接 StepPersistence：它不解决当前最直接的上下文预算问题，并与现有 SQL 会话大量重叠。

## 2. 当前运行链路

当前每轮请求：

```text
MessageStore.get_history()
  → 查询当前 session 的全部 chat_messages
  → deserialize_message()
  → AgentRunner._prepare_history()
  → resolve_history_for_llm()
  → Agent.run / run_stream_events(message_history=完整历史)
  → Pydantic AI Agent Loop
  → result.new_messages()
  → 只追加本轮新消息到 chat_messages
```

关键事实：

1. `get_history()` 无消息数、字符数或 Token 限制。
2. 所有旧 Tool Return 都原样进入下一次模型请求。
3. 旧附件引用会转成提取文本或占位；当前轮附件可能加载完整内容。
4. Usage 只在模型返回后取得，不能阻止当前请求在发送前超限。
5. `UsageLimits` 当前只设置请求数和可选总 Token，不设置输入/输出上限。
6. `result.new_messages()` 不包含 Harness 改写后的历史投影。

## 3. 为什么需要模型上下文属性

需要。上下文压缩不能只靠“消息超过 50 条”触发，因为：

- 不同模型窗口可能是 32K、64K、128K 或更大。
- 同样 20 条消息，普通聊天和财务模型大结果的体积差异可达数十倍。
- 需要动态估计本轮输出、Instructions、工具 Schema、附件和 Provider 包装开销。
- 私有 OpenAI-compatible 模型的窗口不能从 Pydantic AI 自动可靠推导。

### 3.1 模型级属性

0.2.2 已在 `llm_models` 增加三个运行时事实字段：

```text
context_window_tokens       BIGINT NULL
max_output_tokens           BIGINT NULL
tags                        JSON NULL
```

语义：

| 字段 | 用途 |
| --- | --- |
| `context_window_tokens` | Provider 声明的输入与输出共享总窗口 |
| `max_output_tokens` | Provider 声明的单次最大输出能力上限 |
| `tags` | 推理、工具调用、结构化输出等模型能力标签，与输入模态分开 |

这里不持久化 `max_input_tokens`、输出预留值、Tokenizer、估算系数或
`context_budget_config`。这些都不是模型的稳定属性：

- `max_input_tokens` 不能简单等于“总窗口减最大输出”。模型最大可输出 384K，不代表每轮都应为
  384K 留空，否则 1M 模型会无意义地只剩 616K 输入预算。
- 实际输出需求取决于当前任务、历史输出分布、请求参数和模型行为，应在每轮请求前动态预测。
- Tokenizer 与估算校准属于运行算法和观测数据；算法升级不应要求管理员逐模型修改配置。
- 不保存模型属性来源；目录只是预填入口，`llm_models` 中保存的事实始终可以手工修改。

0.2.2 只保存事实并透传到 `AgentSpec/AgentRunner`，尚不启用自动压缩。上下文窗口缺失的历史
模型保持可运行，但后续压缩功能必须自动关闭并给出观测提示。

不应把 `context_window` 塞进 Pydantic AI `ModelProfile`。当前 `ModelProfile` 描述工具、结构化输出、
Thinking 等协议能力，没有通用上下文窗口字段。

### 3.2 Agent 级策略

模型定义“容量”，Agent 定义“怎么压”。建议为 `agents` 增加一个 nullable JSON：

```text
compaction_config
```

第一版结构：

```json
{
  "enabled": true,
  "mode": "tiered",
  "trigger_ratio": 0.80,
  "target_ratio": 0.55,
  "keep_tool_pairs": 3,
  "min_clear_tokens": 2000,
  "preserve_first_user_message": true
}
```

约定：

- `NULL`：使用系统默认。
- `enabled=false`：明确关闭。
- `mode=tiered`：推荐默认。
- `mode=sliding_window`：仅用于可丢弃旧上下文的短任务 Agent。
- `mode=provider_native`：仅限明确支持且完成验证的供应商模型。
- `mode=off`：不压缩，只观测和报警。

摘要模型不再新增 Agent 外键。复用现有 `auxiliary_model_configs`，新增 use case：

```text
context_summary
```

这样可以为所有 Agent 统一选择便宜、稳定的摘要模型，也可回退到主模型。

### 3.3 预算计算

推荐把模型上限与每轮预测分开：

```text
predicted_output_tokens
= clamp(
    基于当前任务与近期输出的预测值,
    dynamic_output_floor,
    max_output_tokens
  )

runtime_overhead_tokens
= estimate(instructions + tool schemas + current input + attachments + provider envelope)

usable_history_budget
= context_window_tokens
- predicted_output_tokens
- runtime_overhead_tokens
- safety_margin_tokens

trigger_tokens = usable_history_budget × trigger_ratio
target_tokens  = usable_history_budget × target_ratio
```

首版预测不需要单独训练模型。可以用确定性算法：

1. 以系统设定的合理下限作为冷启动值。
2. 读取当前会话最近若干轮实际 `output_tokens`，采用分位数或 EWMA，避免被一次异常长输出带偏。
3. 结合当前用户输入长度、是否要求代码/报告、工具链阶段做有限放大。
4. 最终不超过模型的 `max_output_tokens` 能力上限。
5. 用 Provider 返回的真实 usage 持续校准估算误差和安全余量。

Harness 的 `estimate_token_count(messages)` 只统计消息内容，不包含完整 Tool Schema 和所有 Provider
包装，不能把它直接等同于真实请求 Token。后续只有实际观测证明规则不足时，再评估专门预测模型；
默认不应为“估计该留多少输出”额外发起一次 LLM 调用。

## 4. Harness 引包方式

### 4.1 依赖

不能继续使用：

```text
pydantic-ai-slim[...]>=1.0
```

建议第一轮固定：

```text
pydantic-ai-slim[openai,anthropic,google,mcp]==2.14.1
pydantic-ai-harness==0.10.0
```

PyPI 已有 Harness 0.11.0。正式开工时应重新审查 Changelog；若升级到 0.11.0，Core/Harness
要重新跑全部回归，不能只因为“最新版”直接替换。

### 4.2 代码装配

入口应在 `deep_agent/agent/factory.py` 的原生 Capability 列表，而不是塞进 `AgentRunner`：

```python
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    DeduplicateFileReads,
    SummarizingCompaction,
    TieredCompaction,
)

capabilities = []
if isinstance(model, DeepSeekChatModel):
    capabilities.append(DeepSeekThinkingRetry())
if compaction is not None:
    capabilities.append(compaction)

Agent(
    model,
    ...,
    capabilities=capabilities,
)
```

新增建议模块：

```text
deep_agent/agent/context/
├─ __init__.py
├─ budget.py             模型预算和阈值
├─ tokenizer.py          Token 估算与校准
├─ compaction.py         Harness Capability 装配
├─ checkpoint.py         压缩投影持久化
├─ file_keys.py          可去重读工具映射
└─ telemetry.py          压缩指标和脱敏日志
```

## 5. 各策略选择

### 5.1 ClampOversizedMessages

用途：

- 单条模型输出异常膨胀。
- 单个 Tool Call 参数异常巨大。

优点：

- 零模型调用。
- 防止“最新一条本身就塞爆下一轮”，其他历史裁剪无法解决这种情况。

边界：

- 不处理 Tool Return。
- 不应改写用户输入。
- 只作为异常保险，不是常规上下文管理。

建议：

```text
总是启用
阈值 = usable_input_budget 的 35%～50%
保留 head/tail 各 1K～2K 字符
```

### 5.2 ClearToolResults

用途：

- 清空旧 Tool Return 内容，但保留 Tool Call/Return 配对和最近几组完整结果。

优点：

- 对 DeepAgent 当前真实历史收益最大。
- 不调用模型。
- 工具可重新查询时，信息损失可控。

风险：

- 重写旧 Prompt 会破坏 Provider Prompt Cache。
- 某些工具结果不可重放或包含唯一证据，不能一刀切。

建议：

- 默认保留最近 3 组工具结果。
- `exclude_tools` 至少包括不可重放、审批、写操作和一次性签名工具。
- 使用 `min_clear_tokens`，回收太少时不破坏缓存。
- 对 UI/HITL Deferred 结果谨慎处理；用户确认结果通常很小，应保留。

### 5.3 DeduplicateFileReads

用途：

- 同一文件/Resource 被重复读取，只保留最新结果。

不能按工具名泛化。必须维护稳定映射：

```text
read_file                 → path
read_mcp_resource         → server_name + uri
未来 workspace read       → workspace_id + path + version
```

以下不能去重：

- 财务模型查询：同一表达式在数据变化后结果不同。
- 外部搜索：同一关键词结果可能变化。
- 写操作、审批、HITL。

### 5.4 SlidingWindow

用途：

- 只关心最近几轮的短任务。
- 摘要模型不可用时的最后兜底。

不建议作为通用默认。它会直接丢弃旧决策、约束和工具证据。

可用于：

- 无业务状态的临时问答 Agent。
- Summary 失败且下一次请求一定会超窗时的应急 fallback。

### 5.5 SummarizingCompaction

用途：

- 旧目标、决策和当前状态仍重要，但原始历史过大。

优点：

- 压缩比最高。
- 默认结构包含 Intent、Key decisions、Artifacts、Current state、Next steps、Open questions。
- 增量模式可基于旧摘要继续扩展。

风险：

1. 摘要调用计入当前 Run 的请求数和 Token。
2. 默认每条 Tool Return 只进入前 500 字符。
3. 摘要可能产生事实漂移。
4. DeepAgent 当前不会跨请求持久化摘要。
5. 摘要模型失败会让当前请求失败，Harness Tiered 不自动切 SlidingWindow。

启用前置：

- 模型上下文元数据已配置。
- 有可靠 `context_summary` 辅助模型。
- 压缩投影可以持久化。
- 有摘要质量回归数据集。
- 有 fallback 策略。

### 5.6 TieredCompaction

它不是新压缩算法，而是编排器：

```text
测量
→ 运行第一层
→ 再测量
→ 达标则停止
→ 未达标才运行下一层
```

DeepAgent 的推荐默认，因为真实实验中便宜层已经解决 60%～74% 的历史体积。

### 5.7 LimitWarner

它不是压缩策略，只是在接近请求数、上下文或总 Token 限制时给模型警告。

可用于长任务收尾，但不能防止已有历史超窗。优先级低于真正压缩。

### 5.8 Provider Native Compaction

Pydantic AI Core 支持 OpenAI/Anthropic 等供应商原生压缩能力。适用条件：

- 使用官方 Provider。
- 指定模型明确支持。
- 能接受 Provider 特有历史格式。

DeepSeek 和私有 OpenAI-compatible 端点优先使用 Harness 应用侧压缩。不要同时无脑启用
Provider Native 和 Harness Summary，避免双重摘要。

## 6. 压缩投影持久化设计

### 6.1 为什么不能覆盖 chat_messages

`chat_messages` 是产品事实历史，承载：

- Console 完整回放。
- 用户消息编辑与 `branch_from`。
- Tool Call/Result 展示。
- Pending HITL 恢复。
- 会话审计。

压缩历史只是“下一次给模型看的投影”，不能替代产品历史。

### 6.2 推荐新表

新增：

```text
chat_context_checkpoints
```

建议字段：

```text
agent_id                 VARCHAR
session_id               VARCHAR
source_message_count     INTEGER
source_last_sequence     INTEGER
payload                  JSON
estimated_tokens         INTEGER
policy_version           VARCHAR(32)
summary_model_id         VARCHAR NULL
created_at               TIMESTAMP
updated_at               TIMESTAMP
PRIMARY KEY(agent_id, session_id)
```

`payload` 保存：

- Harness 处理后的模型历史投影。
- 已有摘要。
- 保留的最近消息和完整工具配对。

不保存：

- Token、Cookie、Authorization。
- MCP 静态 Header。
- 原始附件二进制。

### 6.3 读取流程

```text
读取完整产品历史元数据
→ 查 checkpoint
→ checkpoint 不存在/失效：使用完整历史
→ checkpoint 有效：
   反序列化 payload
   + 加载 source_last_sequence 之后的原始消息
→ resolve_history_for_llm
→ Agent Run
```

### 6.4 写入流程

成功 Run 后：

```text
result.all_messages()
→ 提取模型本轮实际使用的压缩投影
→ 计算 source_last_sequence
→ 事务内 upsert checkpoint
→ chat_messages 仍只追加 result.new_messages()
```

注意：

- `result.all_messages()` 包含压缩投影，`result.new_messages()` 不包含。
- Streaming 需要在拿到最终 `AgentRunResultEvent` 后写 checkpoint。
- 取消且没有完整 Result 时不更新 checkpoint。
- Summary 失败不覆盖上一次有效 checkpoint。

### 6.5 失效规则

以下情况删除或回退 checkpoint：

- 用户从 `branch_from` 编辑的位置早于 `source_last_sequence`。
- Agent Prompt、能力、模型或压缩策略发生不兼容变化。
- `policy_version` 不匹配。
- checkpoint 反序列化失败。
- Pending HITL 的 Tool Call/Return 与原始历史不一致。

仅新增后续消息时，checkpoint 不失效，直接拼接 tail。

### 6.6 Pending HITL

规则：

- 未返回的 Deferred Tool Call 必须保留在投影尾部。
- 不把合法 Pending Call 当成待修复的悬空错误。
- 用户提交 Deferred Result 后，Result 必须与原 call ID 配对，再参与下一次压缩。
- Pending 状态下可以保存 checkpoint，但第一版更保守：只有 completed run 写 checkpoint，
  pending 继续使用上一次 checkpoint + 原始 tail。

## 7. Token 观测与校准

### 7.1 发送前

记录：

```text
raw_message_count
raw_serialized_chars
estimated_history_tokens
estimated_static_reserve
estimated_total_input
trigger_tokens
target_tokens
strategy_selected
```

### 7.2 返回后

已有 Pydantic Usage 可提供：

```text
requests
input_tokens
output_tokens
total_tokens
cache_read_tokens
cache_write_tokens
```

DeepAgent 当前最终响应只暴露 requests/input/output/total。内部观测应保留缓存 Token 和压缩额外请求。

### 7.3 校准

对每个 `llm_model_id` 记录：

```text
actual_input_tokens / estimated_input_tokens
```

使用滚动 P50/P95 生成 `token_estimate_multiplier` 建议，不自动覆盖用户配置。中文、JSON、长重复文本
差异很大，不能只采一条请求。

敏感要求：

- 指标不记录 Prompt、工具参数或结果原文。
- 只记录数量、策略、耗时、压缩前后体积和模型 ID。

## 8. API 与 Console

### 8.1 模型配置

模型编辑增加“上下文”分组：

- 上下文窗口。
- 最大输出 Token。
- Token 估算方式。
- Tokenizer。
- 校准系数。
- 元数据来源。

未知值显示“未配置”，不能假装使用某个默认窗口。

### 8.2 Agent 配置

增加“上下文策略”分组：

- 使用系统默认/关闭/自定义。
- 模式。
- 触发比例。
- 目标比例。
- 保留工具结果组数。

第一版可以只读展示系统策略，等观测稳定后再开放细调。

### 8.3 运行检查器

建议显示：

```text
历史消息数
估算 Token
模型上下文窗口
是否触发压缩
使用策略
压缩前/后 Token
摘要请求次数
checkpoint 时间
```

不展示摘要原文，不展示被清理的 Tool Result 内容。

## 9. 错误与回退

| 错误 | 行为 |
| --- | --- |
| context_window 未配置 | 不自动摘要；仅观测和单条 Clamp |
| Tokenizer 不可用 | 使用保守字符估算并记录 degraded |
| Summary 模型不可用 | 使用上次 checkpoint；必要时 SlidingWindow 兜底 |
| Summary 超时 | 不覆盖 checkpoint；返回可重试错误或兜底继续 |
| checkpoint 损坏 | 删除投影，回退完整历史并重新生成 |
| Tool 配对校验失败 | 禁止写 checkpoint，保留原始产品历史 |
| Pending HITL | 保留 pending tail，不修复为 synthetic error |
| 超长当前用户输入 | 不静默截断，返回明确输入过大错误 |
| 超长 Tool Return | 当前轮可用 OverflowingToolOutput；旧结果由 Clear 处理 |

## 10. 预计修改文件

### 10.1 依赖和数据库

```text
requirements.txt
alembic/versions/021_context_compaction.py
deep_agent/db/models.py
deep_agent/db/stores/llm_model/base.py
deep_agent/db/stores/agent/base.py
deep_agent/db/stores/message/base.py
deep_agent/db/stores/context_checkpoint/
```

迁移号以实施时最新 revision 为准，不在本设计中写死。

### 10.2 Schema/API

```text
deep_agent/schemas/llm_model.py
deep_agent/schemas/agents.py
deep_agent/api/v1/llm_models.py
deep_agent/api/v1/agents.py
```

可选新增只读诊断：

```text
GET /api/v1/sessions/{id}/context-status
```

不返回摘要正文和敏感历史。

### 10.3 运行时

```text
deep_agent/agent/spec.py
deep_agent/agent/factory.py
deep_agent/api/v1/deps.py
deep_agent/agent/runner.py
deep_agent/agent/context/*
deep_agent/agent/event_persister.py
```

### 10.4 Console

```text
frontend/console/configuration/llm-models.js
frontend/console/configuration/agents.js
frontend/console/configuration-template.html
frontend/console/workspace.js
frontend/console/console.css
```

## 11. 分阶段实施

### 阶段 0：固定版本和基线

1. 固定 Core/Harness 版本。
2. 完整运行 Python、Node、MCP、HITL 回归。
3. 用当前 Test 数据导出会话体积分布，脱敏后形成测试样本。
4. 不改变运行行为。

验收：升级 Core 后现有功能无回归。

### 阶段 1：模型预算与 Token 观测

1. 增加模型上下文元数据。
2. 增加请求前估算。
3. 增加返回后实际 Usage 记录。
4. Console 展示只读预算。
5. 压缩保持关闭。

验收：能解释每个模型为什么在某个阈值触发。

### 阶段 2：零 LLM 保护层

1. 装配 Clamp。
2. 装配 ClearToolResults。
3. 建立 exclude tools。
4. 不启用 Summary。
5. 观察 Prompt Cache、成本和回答质量。

这一阶段可以不建 checkpoint，因为策略是确定性的；但仍应记录压缩前后指标。

### 阶段 3：持久化投影

1. 新建 checkpoint 表和 Store。
2. 实现读取、写入和失效。
3. 保持产品历史完整。
4. 覆盖分支、取消和 Pending HITL。

验收：跨请求不会重新从完整历史生成相同摘要。

### 阶段 4：Tiered Summary

1. 配置 `context_summary` 辅助模型。
2. 启用 Tiered 最后一层。
3. 增加 summary failure fallback。
4. 建立摘要质量评测。
5. 小流量 Agent 试点后扩大。

### 阶段 5：大型工具结果外置

评估 Harness `OverflowingToolOutput`：

- 大结果写入受控 Store。
- 历史只保留句柄、预览和回读工具。
- Store key 必须带 user/agent/app/space/session。
- 设置 TTL、容量、权限和清理。

这比反复压缩已经进入历史的大结果更根本。

## 12. 测试矩阵

### 12.1 单元测试

- 每种策略触发/不触发。
- Tool Call/Return 配对。
- Pending Deferred Call。
- 首个用户目标保留。
- exclude tools。
- 估算器和校准系数。
- Summary 请求计入 UsageLimits。
- Summary 失败 fallback。
- checkpoint 序列化、拼接和失效。

### 12.2 DeepAgent 回归

- 普通聊天。
- SSE 文本/Thinking。
- MCP 多轮工具调用。
- MCP Resource。
- UI/HITL pending、刷新恢复、提交。
- 取消和部分历史。
- 历史编辑与 branch_from。
- 附件、多模态和旧附件引用。
- 自动标题不读取错误投影。
- 用户、Agent、App、Space 隔离。

### 12.3 质量评测

固定长会话数据集，比较：

- 最终答案关键约束召回率。
- 工具参数正确率。
- 关键数字准确率。
- Tool Call 次数。
- 输入/输出 Token。
- 延迟。
- Summary 次数。
- 重复查询次数。

至少包含：

- 多轮财务模型查询。
- 用户多次补充约束。
- Pending 澄清表单。
- 长文件/Resource。
- 编辑旧消息后重发。
- 工具结果关键事实位于头部和尾部。

## 13. 上线与回滚

功能开关建议：

```text
DEEP_AGENT_COMPACTION_ENABLED=false
DEEP_AGENT_COMPACTION_DRY_RUN=true
```

上线顺序：

1. Dry-run 只记录“如果压缩会怎样”。
2. 开启 Clamp。
3. 开启 Clear。
4. 单个测试 Agent 开启 Summary。
5. 扩大范围。

回滚：

- 关闭 Capability 即可恢复完整 SQL 历史运行。
- checkpoint 是派生数据，可以安全删除。
- 不回滚/删除原始 chat_messages。

## 14. 待确认问题

实施前需要产品/研发确认：

1. 哪些 Agent 可以丢弃旧原始 Tool Result，哪些工具必须排除。
2. DeepSeek 私有模型的窗口、最大输出和推荐 tokenizer 数据来源。
3. `context_summary` 使用主模型还是单独低成本模型。
4. 压缩摘要是否需要在 Console 作为诊断信息可见；建议默认不展示正文。
5. checkpoint 保留多久，是否需要按会话删除自动级联。
6. Summary 失败时是“报错重试”还是“SlidingWindow 继续”；建议先报错，稳定后再自动 fallback。

## 15. 当前可直接执行的下一步

建议下一轮只实施“阶段 0 + 阶段 1”：

```text
固定 Core/Harness
→ 完整回归
→ 增加模型上下文元数据
→ 增加 Token dry-run 观测
→ 不真正改写历史
```

它能先把模型窗口、估算误差和真实会话分布测准，再决定触发比例。这样不会一开始就把摘要、DDL、
HITL、分支和工具结果外置全部绑成一次高风险改造。
