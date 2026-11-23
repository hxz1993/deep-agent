# SSE 流式消息协议（v0.14.3）

`POST /api/v1/chat/stream` 使用 `text/event-stream`。实时会话和重新打开的 History 使用不同生命周期：

- 当前 Run 使用流式事件增量展示。
- 正常收流后保留当前直播结果；切换、刷新、重连或重新打开会话时，读取 `/api/v1/sessions/{session_id}/history` 的最终投影。
- 客户端不解析 Event Store Payload，也不接收重复的持久化 Conversation Item 通知。

## 普通聊天事件

| `type` | 持久化 | 客户端处理 |
| --- | --- | --- |
| `text` / `thinking` | 否 | 按 `item_id` 追加当前 Assistant Delta |
| `tool_call` / `tool_result` / `tool_call_removed` | 否 | 按 `tool_call_id` 更新当前 Run 的临时工具项 |
| `output`（显式开启结构化输出） | 是 | 尝试失败仅更新轨迹，accepted 成功按 output_id 展示原生 JSON |
| `session_title` | 是 | 更新会话列表和页头标题 |
| `final` | 否 | 收口 Run 状态、Usage 和上下文预算；保留本轮直播结果，不额外刷新 `/history` |
| `cancelled` / `error` | 否 | 保留当前直播结果并结束 Run；重连或重新打开时由 `/history`校准 |

直播中的 Thinking、工具进度、自动压缩提示等可以只存在于当前页面，不要求在 History 中重放。
History 的用户消息、助手回复、工具、Plan、附件和 Context 均由后端从 Event Store 确定性投影。

用户输入先保存。纯只读调用的正文和结果可以先从 SSE 展示，History 在收尾提交后补齐。
需要查看进行中的会话时，使用下文的观察流。审批卡在待操作事实提交后才可交互。
写入和能力未知的工具在执行前检查持久化，实际压缩也先保存其所用历史。
压缩提交后，后续只读轮次沿用内存中的摘要和增量。输出预留从入场历史采样一次，此后每个模型响应只计入一次。
正常完成、主动停止或执行异常收尾时，会保存可恢复的已接收内容，终态按实际保存结果返回。普通聊天的发送连接断开后，已开始的 Run 继续执行。

普通聊天从首个模型请求起提供 `present`。模型可用它选择会话附件和可信工具返回的文件，直接交付原附件时无需重新生成。成功的 `final` 和同步 `ChatResponse` 可带 `output_files`，每项含 File ID、文件名、MIME、已知大小及可用时的 `preview_url`。History 在对应的最终 `assistant_message` 或 `structured_output` 上返回同样的列表。空列表、取消、失败和等待 UI 回传时省略该字段，正文及业务 JSON 仍在原有 `output` 中。

## 结构化输出（0.14.0，默认关闭）

Agent 显式开启后，输出尝试使用独立 `type:"output"`；它不是 UI Deferred 工具，也不是顶层运行 error。
通用字段为 `session_id,turn_id,run_id,output_id,sequence,name,tool_call_id,attempt,status,accepted,schema_digest`。
失败为 `status:"error",accepted:false,errors:[...]`，不复制候选到普通回复；成功为 `status:"completed",accepted:true,output:<原生JSON>`，包括显式 null。
普通工具与输出混调时整个响应在宿主分发前拒绝，原始调用保留在轨迹，不发布假工具执行或审批。

只有来源 SDK call/ack、预算及必要状态保存后才提交不可变 `output/attempt` 接受事实，再发送成功。连接发送失败不能撤销已保存结果。
`final.output` 与 HTTP `ChatResponse.output` 保持原生 JSON；成功附加 `output_info:{name,output_id,tool_call_id,attempt,turn_id}`。
关闭不新增字段或 output 事件，Pending 仍只有旧 deferred 状态且没有接受结果。

### 允许文本输出（0.14.3）

Agent 开启结构化输出后，可再勾选 allow_text_output（默认 false）。纯文本结束沿用 text/thinking/工具/最终 final 事件和 assistant_message 历史；final.output 为正文，没有 output_info，不产生成功 output 事件或 output/attempt 接受事实。JSON 分支完全沿用上述身份、校验和提交点，包含字符串或 null 根值。

混合模式的文字需等完整模型响应通过分发检查并保存后再按既有text事件发布，防止稍后非法调用使已直播正文与History不一致；首个正文可晚于供应商首Token，关闭路径的增量不变。客户端必须完整处理流，不在首个 text 事件时提前完成：之后可能出现普通工具、Deferred 或输出调用。文字伴随合法输出调用时文字作为过程说明，JSON 为最终结果；History 也保留该说明并仅有一个 structured_output。按 output_info／kind 区分分支，按 output_id 对 output/final/Live/History 去重，不从正文内容猜测 JSON。失败候选只在轨迹，不能作为业务结果采用。允许的文本结束不消耗或重置已有格式纠错及请求额度。

每个 Run（含 Deferred 续跑）从当前 Agent 配置装配该策略，Run 内不反复读取；同一对话约定配置一致。事件不再写完整 Schema 或配置引用，历史快照不驱动续跑。schema_digest 仍保留为当次 Schema 指纹，不用于配置恢复。关闭结构化输出时，即使草稿 allow_text_output:true，仍沿用旧协议。BOM 默认保持 false；平台支持普通文本并不授权业务保存。

History 外壳继续 `schema_version:1`，已接受结果新增：

```json
{
  "item_id": "same-output-id",
  "kind": "structured_output",
  "status": "completed",
  "turn_id": "turn-id",
  "run_id": "run-id",
  "order": {"sequence": 12, "part_offset": 0},
  "source_event_ids": ["same-output-id", "source-call-event-id"],
  "created_at": "2026-10-22T00:00:00Z",
  "updated_at": "2026-10-22T00:00:00Z",
  "name": "lease_result",
  "tool_call_id": "provider-call-id",
  "attempt": 2,
  "output": {"text": "处理完成"}
}
```

同一结果的 History.item_id、output.output_id、final.output_info.output_id 和事件 ID 完全一致；Live 原样包装，不产生另一个模型执行或事实。客户端按此 ID 去重。
失败与成功轨迹均标“输出”，Error/Completed 区分，不增加“校验”类型。格式修正失败不额外计一次运行异常；只有最终失败记一次 execution/error。业务工具/请求/Token 不因语义事件重复计数。
完整配置、恢复、限制、包兼容及客户端迁移规则见[结构化输出指南](structured-output.md)。

## 上下文预算与Usage（v0.10.18）

`final.context.budget`保留原有字段，新增`context_occupancy_tokens`、`occupancy_source`、
`usage_anchor_id`和`estimation_fallback_reason`。`estimated_request_tokens`继续表示当前范围的
请求估算；该字段也补充到只读`context-status`响应。字段定义见[上下文预算指南](context-compaction-budget.md)。

`occupancy_source=provider_usage_plus_estimate`表示通过模型、历史、参数和指令指纹验证的Provider
Usage基准加新增内容估算；`estimated`表示保守估算。基准失效或缺失时，`usage_anchor_id=null`，
并由`estimation_fallback_reason`说明原因。最近响应完成后的预算包含本次响应，不沿用请求前的旧占用。

`final`中的累计Usage与上下文占用分开：累计Usage按唯一模型响应计数，不能用来推算窗口剩余。
客户端继续使用后端预算，不自行累加指令或复制Token算法，也不将未知数据当零。
实时完整请求预算为`estimate_scope=full_model_request`；重新打开会话的只读状态缺少最终请求参数，
返回`history_only`估算，可出现`request_parameters_unavailable`。两者数值及来源可能不同，不能
把只读回退当成Usage丢失。原始Usage与基准元数据仍在响应事件中，真正的新请求会重新验证基准。

只读`context-status`（普通会话与管理员详情共用）补充最近一次成功压缩的展示字段：

| 字段 | 含义 |
| --- | --- |
| `compaction_estimated_tokens_before` | 同次压缩前的预计占用；无记录时为`null` |
| `compaction_estimated_tokens_after` | 同次压缩落库前最终复检的预计占用；无记录时为`null` |
| `compaction_estimate_scope` | 该组前后数值的范围：`full_model_request`或`history_only`；无压缩记录时为`null` |

前后数值成对读取压缩事件`budget.estimated_tokens_before/estimated_tokens_after`，范围取该事件的
`budget.estimate_scope`。自动压力压缩包含主请求参数；主动压缩等缺少完整主请求参数的路径标记
`history_only`，客户端应保留范围提示，不把两者混作相同口径。

旧事件缺少完整的新字段对时，前后值一起回退到`budget.estimated_before/estimated_after`，范围标记
`history_only`，不将新旧口径各取一项拼成比较。旧响应字段`compaction_estimated_tokens`继续表示
历史内容压缩后的估算，含义不变；客户端优先使用新的前后字段，旧服务响应缺少新字段时仍可
使用该旧字段展示压缩后值，未知的压缩前值显示“—”。

## 观察正在执行的一轮（v0.8.5）

`GET /api/v1/sessions/{session_id}/live?agent_id=…&run_id=…` 是只读 SSE，
沿用 History 的用户与 Agent 归属校验。客户端打开会话时从 `run-status` 获取当前 Run，
该接口只观察指定的 Run。原页面 POST SSE 不重复订阅该 Run。
Console 查询会话列表时传 `include_active_runs=true`，当前 API 实例可见的运行项会附带 `active_run_id`。默认列表返回原有字段，点开时以运行状态接口为准。

| 事件 | 内容与用途 |
| --- | --- |
| `observation_sync` | `session_id, agent_id, run_id, replay_available, baseline_sequence`；历史与当前回放衔接边界 |
| `observation` | `run_id, event, data`；内部 `event/data` 原样复用现有聊天 SSE |
| `observation_end` | `run_id`；该轮执行已收尾，回读历史后解除运行状态，连接结束 |
| `observation_reset` | 有界原因；观察连接不可继续，重新校准历史，不取消或重跑 Agent |

上述包装是瞬时传输控制，不写入事件表、模型历史或轨迹行。`baseline_sequence` 只读取该轮开始前
已有的数据库序号；先订阅并缓冲新帧，再读取 History，保留基线前的内容与该轮用户输入，
当前轮助手/工具由回放呈现，不能与 History 重复。终态只校准这一轮，不混入后来轮次的直播。

指定 Run 已结束/不匹配或回放已超限返回 409；订阅容量不可用返回 503，未授权会话返回 404。
断线只尝试原 Run；无法完整回放时退回该轮 History 轮询。UI 工具只有正式待操作终态才可操作。
终态 `final/error/cancelled` 不代表底层已完成清理；观察页等待 `observation_end` 或历史校准收口。

转发与原执行位于同一个 API worker，不引入跨进程协调。回放最多每 Run 4 MiB/4,096 帧、
进程共 32 MiB；观察队列最多 1 MiB/512 帧，总订阅上限 128。慢客户端被断开而非阻塞执行。
Run 结束释放回放，连接断开释放队列。普通聊天的 Run 由服务进程管理，关闭发起请求或只读观察都只断开相应连接。
重新打开会话时可查询并跟随仍活跃的同一个 Run，或回读已保存的结果。明确停止仍调用现有取消接口。
渠道发送流沿用原请求生命周期，进程重启不恢复尚未完成的 Run。

诊断会话详情（含发布 Agent 和远程 Agent 跳入）打开时，如果已经有活动 Run，使用管理员只读
`GET /api/v1/diagnostics/sessions/{session_id}/live?run_id=…` 接入同一事件流。读取历史和文件仍走
诊断权限，页面关闭只断开观察。A2A 和 MCP 发布任务执行时也产生运行事件；结构化回复在校验后展示。
详情页观察打开时发现的那一轮，结束后以历史校准。

普通聊天的 Console 打开会话后，以 `GET /api/v1/sessions/{session_id}/activity?agent_id=…`
监听同一会话的新一轮。初次连接和 Run 开始、结束时发出 `session_activity`，只包含
`status`（`running` 或 `idle`）和 `run_id`。连接保持到切换会话或关闭页面，
每个进程最多 128 条会话活动连接，独立于 Run 流的订阅上限。
多个变化可以合并为最新通知。客户端用状态、历史和指定 Run 的 SSE 校准，不重发消息。
同一会话同时仅允许一轮。控制台停止时带 `run_id`，避免迟到的停止命令取消下一轮。

工具生命周期帧继续使用稳定调用字段 `tool`，并额外返回展示字段 `display_name`：

```json
{
  "type": "tool_call",
  "tool": "web_fetch",
  "display_name": "获取网页内容",
  "tool_call_id": "...",
  "args": {}
}
```

`tool_result`、`tool_call_removed` 以及 `final.deferred.calls/approvals` 使用相同规则。客户端只在普通
会话工具卡标题中使用 `display_name || tool`；调用、续跑和撤回仍以 `tool`、`tool_call_id` 为准。
`display_name` 是当前工具配置动态生成的展示信息，不写入 Event Payload，也不进入 `/events` 或轨迹。

## 轨迹与压缩事件

`session_event_appended/updated` 是 Event Store 的实时镜像，只供 Console 轨迹、审计、Title Event
和 Compaction 原位展示。普通客户端无需消费。

摘要模型等待期间每约 10 秒发送非持久化 `context_compaction_progress`：

```json
{
  "type": "context_compaction_progress",
  "persisted": false,
  "event_id": "...",
  "phase": "summarizing",
  "elapsed_ms": 20000,
  "attempt": 1
}
```

阶段只取 `preparing/summarizing/validating/committing/retry_wait`。历史预算估算、安全边界选择和证据整理
均不得阻塞 SSE 事件循环，准备阶段耗时较长时同样发送 `preparing` 心跳。默认总预算 300 秒；
取消 Run 会取消摘要调用。普通聊天的 Run 在发送连接断开后继续，包括正在进行的摘要调用。

实时会话可在触发位置展示压缩进度；重新打开 History 不恢复压缩提示。Console 轨迹仍通过 Event API
展示真实 Compaction Event。

## Deferred 与重连

- Deferred 继续提交原 `tool_call_id`；服务端定位原 Tool Event 并继承 `turn_id`。
- `tool_call_removed` 只撤销尚未持久化的直播项。
- 断流后重新请求 `/history`，不从 `final` 或本地 Event Payload 猜终态。
- 标题通过 `session_title` 更新，不轮询 `/sessions/{id}`。

Console 实现见 `console/chat.js`、`conversation-items.js`、`protocol.js` 和
`trajectory.js`。
