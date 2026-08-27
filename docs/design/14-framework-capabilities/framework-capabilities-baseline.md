# Pydantic AI与Harness采用基线（v0.6.0）

当前固定版本：`pydantic-ai-slim[openai,anthropic,google,mcp]==2.31.0`、
`pydantic-ai-harness[skills]==0.21.0`。Harness仍是0.x能力库，升级必须固定准确版本并跑完整回归。

## 职责边界

| 层 | 负责 |
| --- | --- |
| Provider | HTTP协议、模型参数、原生模态、Usage与Provider Response ID |
| Pydantic AI Core | Agent Loop、公共Model Part、Toolset、Deferred、Cancellation、流式事件、Provider适配 |
| Harness | Planning、Skills、ToolOutputLimits/Spill等可复用能力 |
| DeepAgent | Agent/Provider/Model配置、用户授权、SQL Event Store、Context Reference、Compaction、SSE、Console、MCP与HITL |

框架原语不能自动成为产品协议。会话身份、权限、事件顺序、审计、删除和跨环境维护仍由DeepAgent定义。

## 已采用

### Pydantic AI Core

- `ModelMessagesTypeAdapter`、公共Request/Response Part、`message_history`和`new_messages()`：Event Codec
  以完整公共Part为持久化单位，读取时严格往返。
- `conversation_id/run_id`：Conversation在Session内稳定，Run每次执行唯一；DeepAgent额外用`turn_id`
  归组Deferred续跑。
- `CancellationToken/RunCancelled`：正式停止和客户端断开取消；已形成的有效Part持久化为Interrupted。
- Deferred Tool Result：Call/Result按Call ID跨Run配对，UI Tool/HITL Pending由Run生命周期表达。
- Tool Search、Prepared/Filtered Toolset和MCP Toolset：只在已有Agent授权范围内渐进披露。
- 官方OpenAI、Anthropic、Google、DeepSeek和OpenAI-Compatible Provider：模型切换不改变Event结构。
- Provider`CompactionPart`：作为普通Provider专属Part无损保存，只回传兼容Provider。

### Harness

- `Planning(tools=['write_plan'])`：每次写完整快照；同Turn Deferred从Event恢复InMemoryPlanStore。
- `Skills`：只负责`SKILL.md`渐进披露；安装、授权、文件和脚本隔离归DeepAgent。
- `ToolOutputLimits + Spill`：完整大结果保存平台File，History保存预览和Artifact句柄；读取使用
  `read_tool_result`。
- Clamp/Deduplicate等确定性思路按需吸收；不再使用批量清空旧Tool Result。

### DeepAgent自有产品能力

- `chat_sessions + chat_session_events`是唯一会话事实源；Event API、Conversation History、Console轨迹
  和Plan投影共用同一数据，实时SSE只承载当前Run增量与终态。
- 单Event上下文压缩：动态预算、八段摘要、Tool/Context Evidence、来源Hash、目标复检、300秒预算与
  SSE进度。
- `search_conversation_history/read_conversation_event`：Agent+User授权内的中英文历史检索和精读。
- MCP配置/Header、企业管理员Console、平台File、Context Reference与UI Deferred产品协议。

## 明确不采用为默认事实源

- Harness`StepPersistence`不替代SQL Event Store；未来只可能用于已定义幂等的执行恢复或副作用账本。
- Provider原生压缩不替代DeepAgent跨Provider结构化摘要；两者不能把同一历史重复改写。
- 不保存Provider原始HTTP Request/Response、Header、Cookie、API Key或动态工具Schema留底表。
- 不建立透明Tool Result缓存；重复读是否必要由模型结合Evidence和历史工具决定。
- 不用Harness Memory替代聊天历史；长期记忆必须另行定义可查看、编辑、删除和授权。
- 不默认启用CodeMode、Shell、主服务文件系统或模型自建Capability。
- 不做静默全局模型回退；候选模型、地域、成本和数据边界必须由Agent显式配置。

## 触发后再评估

- Branch From：先确定复制前缀或父链读取，再决定Parent结构。
- 长任务崩溃恢复：业务工具先定义幂等、补偿和副作用审计。
- 跨会话长期记忆：先定义User/Agent/App/Space、编辑和删除。
- 全文索引：真实数据量与查询计划证明应用层有界搜索不足后，分别评审FTS5、GIN和MySQL FULLTEXT。
- OTLP/Evaluation：公司观测后端、保留周期和权限明确后接入，不把高频Span写入业务表。

## 升级审计清单

1. 核对公共Part新增、删除和序列化字段，Round Trip未知类型必须失败关闭。
2. 核对Deferred、Cancellation、Tool Search和Provider Stream关闭语义。
3. 核对Planning工具名、快照Schema、ToolOutputLimits与Spill接口。
4. 运行Unit、Integration、SQLite迁移往返、三方言DDL与Console Node测试。
5. 不因框架新增一个类就增加表、协议或配置；必须先给出产品收益和失败边界。
