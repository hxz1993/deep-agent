# SSE 流式消息协议（v0.6.0）

`POST /api/v1/chat/stream` 使用 `text/event-stream`。实时会话和重新打开的 History 使用不同生命周期：

- 当前 Run 使用流式事件增量展示。
- 正常收流后保留当前直播结果；切换、刷新、重连或重新打开会话时，读取 `/api/v1/sessions/{session_id}/history` 的最终投影。
- 客户端不解析 Event Store Payload，也不接收重复的持久化 Conversation Item 通知。

## 普通聊天事件

| `type` | 持久化 | 客户端处理 |
| --- | --- | --- |
| `text` / `thinking` | 否 | 按 `item_id` 追加当前 Assistant Delta |
| `tool_call` / `tool_result` / `tool_call_removed` | 否 | 按 `tool_call_id` 更新当前 Run 的临时工具项 |
| `session_title` | 是 | 更新会话列表和页头标题 |
| `final` | 否 | 收口 Run 状态、Usage 和上下文预算；保留本轮直播结果，不额外刷新 `/history` |
| `cancelled` / `error` | 否 | 保留当前直播结果并结束 Run；重连或重新打开时由 `/history`校准 |

直播中的 Thinking、工具进度、自动压缩提示等可以只存在于当前页面，不要求在 History 中重放。
History 的用户消息、助手回复、工具、Plan、附件和 Context 均由后端从 Event Store 确定性投影。

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
客户端断开或正式取消会取消摘要调用。

实时会话可在触发位置展示压缩进度；重新打开 History 不恢复压缩提示。Console 轨迹仍通过 Event API
展示真实 Compaction Event。

## Deferred 与重连

- Deferred 继续提交原 `tool_call_id`；服务端定位原 Tool Event 并继承 `turn_id`。
- `tool_call_removed` 只撤销尚未持久化的直播项。
- 断流后重新请求 `/history`，不从 `final` 或本地 Event Payload 猜终态。
- 标题通过 `session_title` 更新，不轮询 `/sessions/{id}`。

Console 实现见 `console/chat.js`、`conversation-items.js`、`protocol.js` 和
`trajectory.js`。
