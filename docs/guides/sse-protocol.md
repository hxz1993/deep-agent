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
在上传阶段抽取或截断文档文本。TXT、DOCX 和 XLSX 由 Agent 显式启用的 `builtin:attachment`
能力按需分页读取。

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
  "model_message_count": 4,
  "messages": [
    { "role": "user", "content": "你好", "branch_point": 0 },
    { "role": "assistant", "parts": [...], "branch_point": 1 }
  ]
}
```

### 响应

- `Content-Type: text/event-stream`
- 连接保持打开，服务端按 Agent 执行进度持续推送事件
- 客户端断开时，服务端停止推送

> **注意**：浏览器原生 `EventSource` 仅支持 GET，因此前端需使用 `fetch` + `ReadableStream` 手动解析 SSE（参见 `frontend/console/chat.js` 与 `protocol.js`）。

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
| 用户停止 | `cancelled` | 客户端断开连接；已产出内容部分持久化 |
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
| `phase` | `"started" \| "completed" \| "fallback"` | 压缩阶段 |
| `message` | `string` | 面向用户的短状态文案 |
| `request_index` | `number` | 当前 Agent Run 内模型请求序号 |
| 其他字段 | `number \| string \| boolean` | 估算、策略、原因与是否达标等观测值，按阶段可选 |

客户端建议延迟约 150ms 显示，避免快速压缩闪烁；同一请求只维护一个瞬时状态。收到首个
`thinking`、`text`、`tool_call` 或任何结束事件后移除。该事件不属于模型消息，不能加入
assistant 正文，也不会出现在历史接口中。

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

模型请求调用某个工具。由于底层使用 Pydantic AI 流式 API，**同一 `tool_call_id` 可能收到 2 次** `tool_call` 事件：

```json
// 第 1 次：工具调用开始，参数尚未解析完成
{
  "type": "tool_call",
  "tool": "doc_search_doc_search",
  "args": {},
  "tool_call_id": "call_00_evdBG0xFNRn5Q8IlwgZW4217"
}

// 第 2 次：参数就绪，即将执行（UI 工具可能附带 widget / metadata）
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
| `args` | `object` | 工具参数；首帧可能为 `{}`，应以同 `tool_call_id` 的后续帧覆盖 |
| `tool_call_id` | `string \| null` | 调用唯一标识，用于关联 `tool_result` |
| `widget` | `string \| null` | 可选；UI 工具渲染提示（来自 DB，**不发给模型**） |
| `metadata` | `object \| null` | 可选；客户端扩展字段（来自 builtin/manual 定义，**不发给模型**） |
| `source` | `string \| null` | 可选；`builtin` \| `manual` |

**事件来源（后端实现）**：

| 次序 | 来源 | `args` 状态 |
|------|------|-------------|
| 较早 | `PartStartEvent` + `ToolCallPart`（模型流） | 通常为空 `{}` |
| 较晚 | `FunctionToolCallEvent`（工具执行前） | 完整参数 |

**客户端处理建议（重要）**：

- 以 `tool_call_id` 为键做 **upsert**，不要为每次事件新建 UI 块
- 当 `args` 为空对象时，可显示「参数接收中…」占位，收到完整参数后更新
- 不要假设每个 `tool_call_id` 只出现一次
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

`usage` 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `requests` | `number \| null` | LLM 请求次数 |
| `input_tokens` | `number \| null` | 输入 token |
| `output_tokens` | `number \| null` | 输出 token |
| `total_tokens` | `number \| null` | 总 token |

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

客户端断开 SSE 连接（如 `AbortController.abort()`）且服务端检测到断开后发出。此后流结束，**不会**再发 `final`。

```json
{
  "type": "cancelled",
  "status": "cancelled",
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

## 5. 完整示例

以下摘录一次真实对话流（省略大量 `text` delta）：

```
event: session
data: {"session_id": "3eff69967bee4a809c46a47c19e113ff"}

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
6. **工具结果关联**：用 `tool_call_id` 匹配并关闭对应工具块
7. **压缩状态**：`context_compaction` 只做瞬时展示，首个正式输出或结束事件到达后移除
8. **结束判断**：收到 `final`、`cancelled` 或 `error` 后关闭 loading 状态
9. **中断**：用户取消时 `AbortController.abort()`；若已收到 `cancelled` 则不必再提示错误

## 7. 与 REST 接口的关系

| 接口 | 说明 |
|------|------|
| `POST /api/v1/chat` | 非流式，直接返回 `{ session_id, output, usage }` |
| `POST /api/v1/chat/stream` | 流式（`StreamChatRequest`），支持默认 Agent 与 DB Agent 两种请求体，事件语义与上表一致 |
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
- `tool_call` 的「同 ID 多次推送」为当前版本的**既定行为**，不是 bug；客户端必须兼容
