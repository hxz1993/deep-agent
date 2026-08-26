# SSE 流式消息协议（v0.6.0 / V8）

`POST /api/v1/chat/stream` 使用 `text/event-stream`。客户端使用
`fetch + ReadableStream`，按空行分帧并拼接多行 `data:`。

## 持久化与直播事件

持久化事件是 Event Store 的实时镜像：

| `type` | 语义 | 客户端处理 |
| --- | --- | --- |
| `session_event_appended` | Message、Compaction 或异常终态已经提交 | 按 `event_id` 插入并按 `sequence` 排序 |
| `session_event_updated` | 同一 Compaction 生命周期更新 | 按同一 `event_id` 覆盖，不能追加第二行 |

公共 Event 字段只有：`event_id/session_id/sequence/schema_version/event_type/status/turn_id/run_id/payload/created_at/updated_at`。
普通消息的 `payload.message` 是一条完整 Pydantic `ModelRequest` 或 `ModelResponse`；
`message_id/part_index/call_id/tool_name` 不再是 Event 顶层字段。

以下事件只服务当前直播，不入库：

- `text`、`thinking`
- `tool_call`、`tool_result`、`tool_call_removed`
- `context_compaction_progress`
- `final`、`cancelled`、`error`

刷新后的权威状态由 `GET /api/v1/sessions/{session_id}/events` 返回。工具直播状态
与持久化 Message 中的 Tool Part 按 `tool_call_id` 投影为同一逻辑项。

## 上下文压缩

压缩开始追加一条 `compaction/status=running` Event；完成或失败只更新同一 Event。
等待摘要模型期间服务端每约 10 秒发送：

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

`phase` 只取 `preparing/summarizing/validating/committing/retry_wait`，不提供虚假百分比。
默认总预算 300 秒，网关 Read Timeout 至少 330 秒、建议 360 秒，并关闭 SSE 缓冲。
心跳由应用层产生，不依赖 Provider 返回 Token。

## 取消与 Deferred

停止生成调用 `POST /api/v1/sessions/{session_id}/cancel`。客户端继续读取原 SSE，
直到收到真实 `cancelled` 或终态；连接断开也会取消正在等待的模型和摘要调用。

正常完成和正常 Pending 不写 Run Event。`final` 继续携带本次 `run_id`、Usage 和状态。
新用户回合的 `turn_id` 等于首个 `run_id`；Deferred 恢复创建新 Run，但从持久化
Tool Call 的 `tool_call_id` 精确继承原 `turn_id`。只有没有 Assistant Message 的失败、
取消或中断才追加 `run/terminal` Event。

## 重连

- 从已知最大 `sequence` 分页补齐，不按时间戳猜顺序。
- `tool_call_removed` 只撤销尚未持久化的直播工具项。
- `final` 不是历史事实；最终消息、工具和压缩状态以 Event Store 为准。

前端实现见 `frontend/console/protocol.js`、`chat.js`、`session-events.js` 和
`trajectory.js`。
