# SSE 流式消息协议

本文档描述 `deep-agent` 后端 `POST /api/v1/chat/stream` 接口返回的 **Server-Sent Events (SSE)** 消息格式，供前端、SDK 或其他客户端对接使用。

## 1. 连接方式

### 请求

`POST /api/v1/chat/stream` 使用统一的 `StreamChatRequest` 请求体。通过可选字段 `agent_id` 区分两种模式：

| 模式 | 条件 | 行为 |
|------|------|------|
| 默认 Agent | 省略 `agent_id` | 使用 `OPTION` 默认配置，可通过 `model` / `capabilities` 覆盖 |
| DB Agent | 传入 `agent_id`（UUID） | 从数据库加载 Agent 配置，忽略 `model` / `capabilities` |

**默认 Agent 模式：**

```http
POST /api/v1/chat/stream HTTP/1.1
Content-Type: application/json
Accept: text/event-stream

{
  "message": "用户输入",
  "attachments": [
    {
      "id": "platform-file-id",
      "name": "diagram.png",
      "mime": "image/png"
    }
  ],
  "session_id": "可选，省略则服务端生成",
  "model": "可选，如 openai:gpt-4o",
  "capabilities": ["builtin:general", "mcp:doc_search"],
  "user": "可选用户标识"
}
```

新上传只发送 `id/name/mime`。旧会话中已经保存的 `extracted_text` 仍可读取，但新链路不再
在上传阶段抽取或截断文档文本。只要当前或历史会话存在可读附件，运行时系统工具 `read_file`
就会自动注入；TXT、DOCX 和 XLSX按需分页读取，无需 Agent配置附件工具包。

**DB Agent 模式：**

```http
POST /api/v1/chat/stream HTTP/1.1
Content-Type: application/json
Accept: text/event-stream

{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "用户输入",
  "session_id": "可选，省略则服务端生成",
  "user": "可选用户标识"
}
```

> 带 `agent_id` 时，会话存储键为 `{agent_id}:{session_id}`，与默认模式的裸 `session_id` 隔离。

#### 结构化上下文引用（v0.3.5）

普通与流式请求都可增加 `context_refs`：

```json
{
  "message": "分析当前选区",
  "context_refs": [
    {
      "key": "current_selection",
      "type": "deeptable.selection",
      "schema_version": "2.0",
      "label": "销售预算!B2:C4",
      "payload": {"selection": {}, "dataList": []},
      "metadata": {"source": "element-deep-table"}
    }
  ]
}
```

- `context_refs` 可以与文字、附件及 `branch_from` 同时使用，也可以单独形成用户轮次。
- `context_refs` 与 `deferred_tool_results` 互斥；恢复 Deferred 时由 Tool Result 携带新数据。
- 原始 `payload/metadata` 随历史保存；模型只收到排除 `metadata` 的安全派生投影。
- 单请求最多 16 个引用，单个完整引用最多 256 KiB，总计最多 512 KiB，JSON 嵌套最多 32 层。
- 主动压缩接口不接收 `context_refs`；只有已经发送并持久化的引用进入自动或主动压缩。

#### 编辑续聊（`branch_from`）

从已有会话的指定位置截断历史并继续对话：

- 请求体可选 `branch_from`（整数），取值来自 `GET /api/v1/sessions/{id}/history` 返回的 `messages[].branch_point`
- 服务端在运行前删除 `sequence >= branch_from` 的持久化消息，再发送新 `message`
- 与 `deferred_tool_results` 互斥（不可同时传）
- 用户消息在 Agent 运行前即写入存储，因此即使后续 `error` 事件，用户输入仍会保留

历史响应示例：

```json
{
  "session_id": "abc",
  "conversation_id": "deepagent:agent-id:abc",
  "model_message_count": 4,
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "context_refs": [],
      "branch_point": 0
    },
    {
      "role": "assistant",
      "status": "completed",
      "run_id": "run-id",
      "parts": [...],
      "branch_point": 1
    }
  ]
}
```

### 响应

- `Content-Type: text/event-stream`
- 连接保持打开，服务端按 Agent 执行进度持续推送事件
- 客户端停止生成必须调用 session 取消接口，并继续读取原 SSE 直到收到真实 `cancelled`
- 首条 `session` 事件包含稳定 `conversation_id` 和本次独立 `run_id`；同一 Session 的 Deferred
  恢复和后续发送复用 Conversation ID，但每次都生成新 Run ID

> **注意**：浏览器原生 `EventSource` 仅支持 GET，因此前端需使用 `fetch` + `ReadableStream` 手动解析 SSE（参见 `frontend/console/chat.js` 与 `protocol.js`）。

### 正式取消（v0.3.10）

```http
POST /api/v1/sessions/{session_id}/cancel?agent_id={optional}
```

接口使用 Pydantic AI 2.26 `CancellationToken` 终止当前 Pod 内正在运行的流，返回
`cancel_requested` 或幂等的 `not_running`。收到 `cancel_requested` 后客户端必须继续读取同一条
SSE，直到服务端捕获 `RunCancelled` 并返回唯一的 `cancelled`；取消后不再返回 `final`。
服务端会在发布首条 `session` SSE 前注册 Active Run，因此客户端一旦拿到 `session_id` 就可以
安全启用停止操作，不需要等待第一段 thinking、文本或工具事件。

`cancelled` 携带本轮实际 `usage`、历史计数和分支点。服务端只持久化
`RunCancelled.new_messages()`；若 Provider 返回的部分响应仍错误标记为 `complete`，DeepAgent 会把
本轮最后一个非空部分响应规范化为 `state=interrupted`。下一轮复用历史时，
Pydantic AI 会为未完成工具调用自动补充 `outcome=interrupted`，DeepAgent 不再手工拼接文本或
生成 `ToolDenied`。

取消请求失败时客户端应提示失败并允许重试，不能断开 SSE 或伪造停止成功。`not_running` 也不
等于已取消：它可能表示 Run 已经自然结束，客户端仍等待原流的真实终态。浏览器关闭或网络中断
时可以 Abort 本地请求，服务端会取消并清理无人消费的 Run，但该路径不向已断开的客户端伪造
事件，也不是停止按钮的产品语义。

UI Deferred 已经进入 Pending 后不存在活动 Run，因此返回 `not_running`；表单取消由
`DeferredToolResults` 表达，不调用会话取消接口。当前注册表是进程级，多 Pod 必须依赖会话
粘性或后续共享取消协调器，不能用客户端假事件掩盖未命中实例的问题。
v0.3.14 起部署入口固定每个 Pod 一个异步 Gunicorn Worker，避免同一 Pod 内请求命中不同
Registry；异步 Worker 仍可并发处理 I/O。该设置不解决多 Pod 随机路由。

`cancelled` 示例：

```json
{
  "type": "cancelled",
  "status": "cancelled",
  "conversation_id": "deepagent:agent-id:session-id",
  "run_id": "run-id",
  "history_persisted": true,
  "model_message_count": 8,
  "user_branch_point": 6,
  "usage": {
    "requests": 1,
    "input_tokens": 1200,
    "output_tokens": 96,
    "total_tokens": 1296
  },
  "run_metrics": {
    "status": "cancelled",
    "duration_ms": 1820,
    "cache_read_tokens": null,
    "cache_write_tokens": null,
    "tool_calls": 1,
    "tool_search_calls": 0,
    "compaction": "none"
  }
}
```

## 2. SSE 帧格式

每个事件由若干行组成，以**空行**（`\n\n` 或 `\r\n\r\n`）分隔：

```
event: <事件名>
data: <JSON 字符串>

```

### 字段说明

| 行 | 含义 |
|----|------|
| `event:` | 事件类型，与 `data.type` 一致（冗余但便于按 event 过滤） |
| `data:` | 单行 JSON；若未来有多行 data，按 SSE 规范用 `\n` 拼接后再 `JSON.parse` |
| `: ...` | 注释行，用于心跳（keep-alive），客户端应忽略 |

### 心跳示例

```
: ping - 2026-06-05T03:45:20.860855+00:00

```

## 3. 事件生命周期

一次完整对话流通常按以下顺序出现（中间可能穿插多次 `text` / `tool_call` / `tool_result`）：

```mermaid
sequenceDiagram
    participant Client
    participant Server
    participant Agent

    Client->>Server: POST /chat/stream
    Server-->>Client: session
    opt 上下文超过触发线
        Server-->>Client: context_compaction (started)
        Server-->>Client: context_compaction (completed/fallback)
    end
    loop Agent 推理
        Server-->>Client: thinking (delta, 可选)
        Server-->>Client: text (delta)
        Server-->>Client: tool_call (可能多次，同 id)
        Server-->>Client: tool_result
    end
    Server-->>Client: final
```

| 阶段 | 事件 | 说明 |
|------|------|------|
| 开始 | `session` | 返回本次会话 ID，客户端应保存用于后续请求 |
| 上下文治理 | `context_compaction` | 可选；压缩开始、完成或兜底的瞬时状态，不写入历史 |
| 思考过程 | `thinking` | 模型内部推理增量（reasoning 模型）；与 `text` 分离展示 |
| 流式输出 | `text` | 模型回复的文本增量 |
| 工具调用 | `tool_call` | 模型发起工具调用（同一 `tool_call_id` 可能推送多次） |
| 工具结果 | `tool_result` | 工具执行完毕的返回 |
| 结束 | `final` | 完整输出与 token 用量 |
| 用户停止 | `cancelled` | 正式取消或客户端断流；已产出内容部分持久化 |
| 异常 | `error` | 流中错误（出现后流通常结束） |

## 4. 事件类型详解

### 4.1 `session`

流中**第一个**事件，告知本次会话标识。

默认 Agent 模式：

```json
{
  "session_id": "3eff69967bee4a809c46a47c19e113ff"
}
```

DB Agent 模式（请求体含 `agent_id`）：

```json
{
  "session_id": "3eff69967bee4a809c46a47c19e113ff",
  "agent_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | `string` | 会话 ID；请求未传时由服务端 `uuid4().hex` 生成 |
| `agent_id` | `string` | 可选；DB Agent 模式时回显请求中的 Agent UUID |

客户端应将 `session_id`（及 `agent_id`，若使用 DB Agent）持久化，后续同一会话的请求需带上相同字段以延续上下文。

---

### 4.2 `context_compaction`

上下文超过动态触发线时，服务端会在正式模型输出前发送应用自有状态事件：

```json
{
  "type": "context_compaction",
  "phase": "started",
  "message": "正在压缩上下文…",
  "request_index": 1,
  "estimated_tokens_before": 3252,
  "target_tokens": 1060
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"context_compaction"` | 固定值 |
| `phase` | `"started" \| "deterministic" \| "summarizing" \| "saving" \| "completed" \| "fallback"` | 压缩阶段 |
| `message` | `string` | 面向用户的短状态文案 |
| `request_index` | `number` | 当前 Agent Run 内模型请求序号 |
| 其他字段 | `number \| string \| boolean` | 估算、策略、原因与是否达标等观测值，按阶段可选 |

客户端建议延迟约 150ms 显示，避免快速压缩闪烁；同一请求只维护一个独立过程条，并与主动
压缩复用同一视觉组件。该事件不属于模型消息，不能加入 assistant 正文。自动压缩成功写入
Checkpoint 后，应重新读取历史并展示 `context_checkpoint` 分隔标记；临时 Sliding Window、
失败或未持久化结果只保留瞬时过程条，不会出现在历史接口中。

`final.context.compaction.checkpoint_saved` 明确表示自动压缩的派生投影是否已经持久化：`true`
才可展示成功并刷新历史，`false` 表示生成了摘要但保存失败，`null` 表示本轮没有尝试保存。

v0.3.6 中 `summarizing` 表示一次语义摘要，不代表分块序号。遇到 429、502、503、504、连接
重置或超时时，服务端可用完全相同的摘要请求重试一次，并发送“模型暂时不可用，正在重试摘要”；
这仍是一份摘要，兼容字段 `summary_chunks` 固定为 `1`。

#### 主动压缩流

`POST /api/v1/sessions/{session_id}/compact?agent_id=...` 使用同一
`context_compaction` 过程事件，并以 `context_compaction_result` 结束。请求体只允许可选的
`model_id`；没有 `agent_id` 时必须提供它。`completed` 包含压缩前后估算、摘要调用 usage 和
当前 Checkpoint，并立即关闭 SSE；客户端随后独立刷新 history 与 `context-status`，避免
Checkpoint 已保存后因状态查询延迟而让流无法收口。`noop` 表示本次未产生可保存的压缩结果，可能
是空历史、无安全摘要前缀或没有明显 Token 收益，不能据此假定模型一定未被调用；`failed`
表示旧 Checkpoint 保持不变。该接口不产生普通 `text/final` 助手回复，也不因已有 Checkpoint
或缺少新增消息而拒绝入口操作；但当前有效 Checkpoint 已覆盖全部原始历史时，不会反复只压缩
旧摘要，而是返回 `noop/no_reduction`。主动压缩使用同一稳定 `conversation_id`，但生成独立
`run_id`；终态 `context_compaction_result` 携带二者和 `run_metrics`。自动压缩属于当前聊天 Run
的子阶段，不生成第二个 Run ID。

---

### 4.3 `text`

模型回复的**文本增量**，客户端按顺序拼接即可得到完整 Markdown 文本。

```json
{
  "type": "text",
  "delta": "让我先搜索一下文档。"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"text"` | 固定值 |
| `delta` | `string` | 本帧新增的文本片段，可能为一个字符、一个词或一句话 |

**客户端处理建议**：

- 将 `delta` 追加到当前文本缓冲区
- 可对缓冲区做增量 Markdown 渲染
- 出现 `tool_call` 后，后续 `text` 属于**新的文本段**（工具调用会切断文本流）

---

### 4.4 `thinking`

模型**内部推理**的文本增量（如 DeepSeek reasoning、Claude extended thinking）。与面向用户的 `text` 分离，客户端应单独展示（建议默认折叠）。

```json
{
  "type": "thinking",
  "delta": "用户用中文打招呼，我应该用中文回应。"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"thinking"` | 固定值 |
| `delta` | `string` | 本帧新增的思考片段 |

**客户端处理建议**：

- 将 `delta` 追加到独立的思考缓冲区，**不要**与 `text` 拼接
- 建议以可折叠区块展示，默认折叠；流式时标题可显示「思考中…」
- 出现 `text` 或 `tool_call` 时，应结束当前思考块的流式状态
- 历史回放：`GET /sessions/{id}/history` 中 assistant `parts` 含 `{ "type": "thinking", "content": "..." }`

---

### 4.5 `tool_call`

模型请求调用某个工具。DeepAgent 只在 Pydantic AI 完成参数校验后发布普通 Function、MCP
和 UI 工具调用；模型流中尚未校验的临时 `ToolCallPart` 不会形成业务 `tool_call` 事件：

```json
{
  "type": "tool_call",
  "tool": "myapp__pick_date",
  "args": { "prompt": "请选择交付日期" },
  "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217",
  "widget": "date_picker",
  "metadata": { "form_version": "1" },
  "source": "manual"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"tool_call"` | 固定值 |
| `tool` | `string` | 稳定工具协议名（MCP 前缀示例 `doc_search_doc_search`）；与工具包编码不同 |
| `args` | `object` | 已通过工具 Schema 校验的完整参数 |
| `tool_call_id` | `string \| null` | 调用唯一标识，用于关联 `tool_result` |
| `widget` | `string \| null` | 可选；UI 工具渲染提示（来自 DB，**不发给模型**） |
| `metadata` | `object \| null` | 可选；客户端扩展字段（来自 builtin/manual 定义，**不发给模型**） |
| `source` | `string \| null` | 可选；`builtin` \| `manual` \| `tool_search`；Tool Search 是运行诊断，不是业务工具 |

Provider Native Tool 使用对应的原生 Call/Return 事件，不经过普通 Function Tool 参数校验链路；
后端仍保持其原生流式事件。

**客户端处理建议（重要）**：

- 仍以 `tool_call_id` 为键做 **upsert**，兼容重连、历史恢复和未来 Provider 差异
- 只有已验证的普通工具调用才创建业务工具块；若工具随后返回 `ModelRetry`，或取消后该调用未进入
  官方中断历史，服务端会发送 `tool_call_removed`，客户端必须删除对应临时工具块
- UI 工具（builtin/manual）的 `widget` / `metadata` 由后端 enrichment 注入；客户端可优先使用 SSE 字段，或与 `GET /api/v1/ui-tools` 缓存合并

---

### 4.6 `tool_result`

工具执行完成后的返回。

```json
{
  "type": "tool_result",
  "tool": "doc_search_doc_search",
  "result": "### 维度管理\n...(markdown 文本)...",
  "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"tool_result"` | 固定值 |
| `tool` | `string` | 工具名称 |
| `result` | `string \| number \| boolean \| object \| array \| null` | 工具返回值，经 `_to_jsonable` 序列化 |
| `tool_call_id` | `string \| null` | 对应 `tool_call` 的 ID |

**客户端处理建议**：

- 通过 `tool_call_id` 找到对应的 `tool_call` UI 块并标记完成
- 若 `result` 为字符串且含 Markdown，建议渲染为富文本
- 若 `tool_call_id` 缺失，可回退到「最近一个未完成的工具块」

---

### 4.6.1 `tool_call_removed`

已经发布的工具调用被 Pydantic AI 正式撤回时发送。当前包括两类情况：业务工具返回
`ModelRetry`，模型需要修正参数后重试；Run 被取消且该调用没有进入官方中断历史。

```json
{
  "type": "tool_call_removed",
  "tool": "deepfos_deeptable_workbook_get",
  "tool_call_id": "call_workbook_1",
  "reason": "model_retry"
}
```

客户端必须按 `tool_call_id` 删除对应的实时工具块；它不是工具失败结果，也不进入最终 History。
后续修正后的调用会使用新的 `tool_call_id` 正常发送 `tool_call` / `tool_result`。`reason` 当前为
`model_retry` 或 `cancelled`。

---

### 4.7 `final`

流正常结束时的汇总事件，**一定在成功路径末尾出现**（在 `session` 锁释放后发出）。

```json
{
  "type": "final",
  "status": "completed",
  "output": "## 平台的 11 种维度类型\n\n...",
  "usage": {
    "requests": 4,
    "input_tokens": 18079,
    "output_tokens": 1012,
    "total_tokens": 19091
  }
}
```

当 Agent 调用了需在前端执行的 UI 工具（human-in-the-loop）且 run 暂停等待用户输入时，`status` 为 `"pending"`，并附带 `deferred`：

```json
{
  "type": "final",
  "status": "pending",
  "output": null,
  "deferred": {
    "calls": [
      {
        "tool": "myapp__pick_date",
        "args": { "prompt": "是否继续？" },
        "tool_call_id": "call_00_abc",
        "widget": "date_picker",
        "metadata": { "form_version": "1" }
      }
    ],
    "approvals": []
  },
  "usage": { "requests": 2, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120 }
}
```

续跑：客户端再次 `POST /chat/stream`，请求体携带相同 `session_id` 与 `deferred_tool_results`（`message` 可省略）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"final"` | 固定值 |
| `status` | `"completed" \| "pending"` | `pending` 表示等待 UI 工具结果 |
| `output` | `string \| object \| ... \| null` | Agent 最终输出；`pending` 时常为 `null` |
| `deferred` | `object \| null` | `pending` 时包含未完成的 `calls` / `approvals`；`calls[]` 项可含与 `tool_call` 相同的 `widget` / `metadata` 可选字段 |
| `usage` | `object` | Token 与请求次数统计 |
| `model_message_count` | `integer` | 可选；持久化后原始 `ModelMessage` 条数，供客户端同步编辑锚点 |
| `user_branch_point` | `integer` | 可选；本轮用户消息下标，与 `messages[].branch_point` 一致 |
| `conversation_id` | `string` | 当前 Agent + Session 的稳定 Pydantic AI Conversation ID |
| `run_id` | `string` | 本次发送、Deferred恢复或重试的独立 Pydantic AI Run ID |
| `run_metrics` | `object` | 当前页面可用的临时运行指标；完整指标不写入聊天历史 |

`usage` 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `requests` | `number \| null` | LLM 请求次数 |
| `input_tokens` | `number \| null` | 输入 token |
| `output_tokens` | `number \| null` | 输出 token |
| `total_tokens` | `number \| null` | 总 token |

`run_metrics.cache_read_tokens/cache_write_tokens` 只读取 Provider Usage。Provider 未报告时为
`null`，不能解释为零命中；详细运行指标当前只存在于终态 SSE 和脱敏结构化日志，页面刷新后
History 仅恢复 `conversation_id/run_id/status`。

**客户端处理建议**：

- 流式 `text` 拼接结果应与 `output` 基本一致；可用 `final` 做校验或兜底展示
- 展示 `usage` 供用户了解消耗

---

### 4.8 `error`

Agent 执行过程中抛出未捕获异常时发出，此后流结束，**不会**再发 `final`。

```json
{
  "type": "error",
  "message": "Connection timeout",
  "history_persisted": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"error"` | 固定值 |
| `message` | `string` | 错误描述 |
| `history_persisted` | `boolean` | 可选；为 `true` 时表示本轮用户 `message` 已写入会话历史，可用 `branch_from` 重试 |
| `model_message_count` | `integer` | 可选；错误后当前会话原始 `ModelMessage` 条数 |
| `user_branch_point` | `integer` | 可选；本轮用户消息在原始历史中的下标（与 `branch_point` 一致） |

---

### 4.9 `cancelled`

正式取消接口触发 Pydantic AI `CancellationToken`，或客户端异常断开 SSE 后发出。此后流结束，
**不会**再发 `final`。Pydantic AI 2.26 提供的中断消息状态优先用于持久化；若 Provider 尚未
形成完整消息时，DeepAgent 仅保存官方返回的部分消息，并把最后一个非空响应规范化为
`interrupted`；不会用前端累积内容拼造历史。已经发布但未进入该官方中断历史的工具调用，会先
收到 `tool_call_removed`，随后才收到 `cancelled`。

```json
{
  "type": "cancelled",
  "status": "cancelled",
  "conversation_id": "deepagent:agent-id:session-id",
  "run_id": "run-id",
  "history_persisted": true,
  "model_message_count": 3,
  "user_branch_point": 0
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"cancelled"` | 固定值 |
| `status` | `"cancelled"` | 固定值 |
| `history_persisted` | `boolean` | 是否已写入部分 assistant 内容 |
| `model_message_count` | `integer` | 可选；当前会话 `ModelMessage` 条数 |
| `user_branch_point` | `integer` | 可选；本轮 user 消息的 `branch_point` |
| `conversation_id` | `string` | 与首条 `session` 事件一致 |
| `run_id` | `string` | 与被取消的活动 Run 一致 |
| `run_metrics` | `object` | 本次取消前已发生的 Usage、耗时、工具和压缩指标 |

## 5. 完整示例

以下摘录一次真实对话流（省略大量 `text` delta）：

```
event: session
data: {"session_id":"3eff69967bee4a809c46a47c19e113ff","conversation_id":"deepagent:anonymous:3eff69967bee4a809c46a47c19e113ff","run_id":"run-id"}

event: text
data: {"type": "text", "delta": "让我先搜索一下文档中关于\"维度类型\"的相关内容。"}

event: tool_call
data: {"type": "tool_call", "tool": "doc_search_doc_search", "args": {}, "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217"}

event: tool_call
data: {"type": "tool_call", "tool": "doc_search_doc_search", "args": {"query": "维度类型"}, "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217"}

event: tool_result
data: {"type": "tool_result", "tool": "doc_search_doc_search", "result": "### 维度管理\n...", "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217"}

event: text
data: {"type": "text", "delta": "让我查看维度概览文档..."}

event: final
data: {"type": "final", "output": "## 平台的 11 种维度类型\n...", "usage": {"requests": 4, "input_tokens": 18079, "output_tokens": 1012, "total_tokens": 19091}}
```

## 6. 客户端实现清单

对接时请确保实现以下行为：

1. **解析 SSE**：支持 `\n\n` / `\r\n\r\n` 分帧，忽略 `:` 心跳注释
2. **保存 session**：收到 `session` 后写入状态，后续请求携带 `session_id`
3. **文本流**：`text.delta` 顺序拼接，支持 Markdown 渲染
4. **思考过程**：`thinking.delta` 独立缓冲，可折叠展示，不与 `text` 拼接
5. **工具调用 upsert**：同 `tool_call_id` 的 `tool_call` 更新而非重复创建
6. **工具结果与撤回**：`tool_result` 关闭对应工具块；`tool_call_removed` 删除对应临时工具块
7. **压缩状态**：自动和主动 `context_compaction` 复用独立过程条；成功后以历史 Checkpoint 标记收口
8. **结束判断**：收到 `final`、`cancelled` 或 `error` 后关闭 loading 状态
9. **中断**：用户停止时只调用 `/sessions/{id}/cancel` 并等待真实 `cancelled`；失败或 `not_running` 不得断流或伪造成功，`AbortController` 只用于页面卸载和网络资源清理

## 7. 与 REST 接口的关系

| 接口 | 说明 |
|------|------|
| `POST /api/v1/chat` | 非流式，直接返回 `{ session_id, output, usage }` |
| `POST /api/v1/chat/stream` | 流式（`StreamChatRequest`），支持默认 Agent 与 DB Agent 两种请求体，事件语义与上表一致 |
| `POST /api/v1/sessions/{id}/cancel` | 请求取消当前实例内的活动 Run；客户端继续消费原 SSE，直到收到 `cancelled` |
| `GET /api/v1/sessions/{id}` | 查询会话元信息（消息条数等），不返回 SSE |
| `DELETE /api/v1/sessions/{id}` | 删除会话历史 |

## 8. 后端源码索引

| 文件 | 职责 |
|------|------|
| `deep_agent/api/v1/chat.py` | SSE 端点，`EventSourceResponse` 封装 |
| `deep_agent/agent/events.py` | 事件 dict 构造函数 |
| `deep_agent/agent/runner.py` | Pydantic AI 事件 → SSE 事件映射 |
| `frontend/console/chat.js` | 参考客户端实现 |

## 9. 版本与兼容性

- 当前协议版本：**v1**（随 `/api/v1` 路径发布）
- 新增事件类型时，`event` 与 `data.type` 将保持一致
- 客户端对未知 `event` 应忽略，避免硬崩溃
- 普通 Function、MCP 和 UI 工具只发布参数校验后的调用；客户端仍应按 `tool_call_id` 幂等处理
