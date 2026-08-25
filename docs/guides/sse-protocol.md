# SSE 流式消息协议（v0.6.0）

`POST /api/v1/chat/stream`使用`text/event-stream`。浏览器原生`EventSource`不支持POST，客户端使用
`fetch + ReadableStream`，按空行分帧、拼接多行`data:`后解析JSON。

## 两类事件

持久化事件是Event Store的实时镜像：

| `type` | 语义 | 客户端处理 |
| --- | --- | --- |
| `session_event_appended` | 新Event已经提交 | 按`event.event_id`插入，按`sequence`排序 |
| `session_event_updated` | Run或Compaction生命周期终态已提交 | 按同一`event.event_id`覆盖，不追加到底部 |

两者都携带`persisted=true`和完整公共Event：`event_id/session_id/sequence/schema_version/event_type/status/turn_id/run_id/message_id/part_index/call_id/tool_name/payload/created_at/updated_at`。
刷新后的权威结果由`GET /api/v1/sessions/{session_id}/events`返回，应与实时Upsert结果一致。

非持久化事件只用于当前直播过程，明确`persisted=false`或没有持久化标志：

- `text`、`thinking`
- `tool_call`、`tool_result`、`tool_call_removed`
- `context_compaction_progress`
- `final`、`cancelled`、`error`

这些事件不能在刷新后被假定仍然存在；最终消息与工具Part会另外通过持久化Event提交。

## 上下文压缩

压缩开始会先追加一条`compaction/status=running`Event，完成或失败时更新同一Event ID。等待摘要模型期间
服务端每约10秒发送：

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

`phase`只取`preparing/summarizing/validating/committing/retry_wait`，不提供虚假百分比。客户端按
`event_id`更新一条临时状态；收到持久化终态后移除临时状态并以Event为准。

默认摘要总预算300秒，因此网关Read Timeout至少330秒、建议360秒，并关闭SSE代理缓冲。心跳由应用
外层调度，不依赖Provider返回Token。

## 取消与Deferred

停止生成调用：

```http
POST /api/v1/sessions/{session_id}/cancel
```

客户端继续读取原SSE，直到收到真实`cancelled`或终态；不能仅关闭连接后伪造成功。服务端在发布首条
Session信息前已经登记Active Run，因此拿到Session ID后即可取消。连接异常断开会触发同一个
Cancellation Token，并取消正在等待的摘要模型。

UI Tool/HITL返回Pending时，Run Event更新为`pending`。提交`deferred_tool_results`会创建新Run，继承
同一`turn_id`，工具结果继续按`call_id`与旧Run中的调用配对；客户端不需要自行合并Run身份。

## 错误与重连

- `error`保留后端错误语义；Provider Context Overflow只有在尚未输出正文或工具事件时才可能压缩后安全
  重试一次，否则返回原错误。
- 客户端重连或页面刷新后，从已知最大Sequence调用Event API分页补齐，不按时间戳猜顺序。
- `tool_call_removed`撤销的是尚未持久化的实时工具卡；已持久化Event不由该消息删除。
- `final`表示本次Run结束，不等于它本身是历史事实；持久化消息和Run终态仍以Event为准。

前端实现见`frontend/console/protocol.js`、`chat.js`和`session-events.js`。
