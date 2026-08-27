# SSE 流式消息协议（v0.6.0）

`POST /api/v1/chat/stream` 使用 `text/event-stream`。普通客户端以
`ConversationItem` 为唯一渲染模型：历史全量装载、直播增量 Patch、持久化 Item Upsert。

## 普通聊天事件

| `type` | 持久化 | 客户端处理 |
| --- | --- | --- |
| `text` / `thinking` | 否 | 按 `item_id` 追加 Delta |
| `tool_call` / `tool_result` / `tool_call_removed` | 否 | 按 `item_id` 更新临时工具项 |
| `conversation_item_upsert` | 是 | 按 `item.item_id` 完整替换 |
| `session_title` | 是 | 更新会话列表和页头标题 |
| `final` / `cancelled` / `error` | 否 | 结束本次 Run；不作为聊天历史事实源 |

持久化 Upsert 示例：

```json
{
  "type": "conversation_item_upsert",
  "persisted": true,
  "item": {
    "item_id": "tool:call-1",
    "kind": "tool",
    "status": "completed",
    "order": {"sequence": 12, "part_offset": 2},
    "source_event_ids": ["...", "..."]
  }
}
```

该 Item 必须与随后请求 `/api/v1/sessions/{session_id}/history` 得到的同 ID Item
完全一致。持久化的 `assistant_message` Upsert 表示该 Assistant Item 已经完整落库：客户端应在
Upsert 到达时立即用它替换同 Run 的直播草稿，不能继续等待 `final`；服务端不会在该 Upsert 后再发送
属于同一 Assistant Item 的 `text/thinking` Delta。`final` 只保留 Run 状态、Usage 和上下文预算，
客户端可忽略权威 Upsert 后异常迟到的同 Run Delta。

## 轨迹与压缩事件

`session_event_appended/updated` 是 Event Store 的实时镜像，只供 Console 轨迹、审计、
Title Event 和 Compaction 原位展示。普通客户端无需消费。

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

## Deferred 与重连

- Deferred 继续提交原 `tool_call_id`；服务端定位原 Tool Event 并继承 `turn_id`。
- `tool_call_removed` 只撤销尚未持久化的直播项。
- 断流后重新请求 `/history`，不从 `final` 或本地 Event Payload 猜终态。
- 标题通过 `session_title` 更新，不轮询 `/sessions/{id}`。

Console 实现见 `frontend/console/chat.js`、`conversation-items.js`、`protocol.js` 和
`trajectory.js`。
