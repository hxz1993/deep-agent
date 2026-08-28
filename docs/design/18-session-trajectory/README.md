# 18 · 会话事件与轨迹

> v0.6.0 会话结构；当前产品版本0.6.2，Alembic head：010。Code Mode不修改会话事件结构。

DeepAgent 会话域只有 `chat_sessions` 和 `chat_session_events`。Event Store 是模型恢复、
会话历史、Console 轨迹和Plan的唯一事实源；没有 Checkpoint、Plan、Evidence、
搜索或 Provider HTTP 报文留底表。

## Event 粒度

一条完整 Pydantic AI `ModelRequest` 或 `ModelResponse` 保存为一条 Event：

- `user/message`：含用户正文、附件、Context Reference、System/Instructions。
- `assistant/message`：按原顺序包含 Thinking、Text、Tool Call、File、Speech 等 Part；
  Usage、模型、Provider 和 Finish Reason保留在原 Message 元数据中。
- `tool/results`：一次完整工具反馈 Request，可含一个或多个 Result、Retry、Deferred Result。
- `model/request`：不能归入普通用户或工具结果的框架 Request。
- `compaction`：一次压缩一行，从 `running` 原地更新为终态。
- `run/terminal`：仅在失败、取消或中断且没有 Assistant Message 时追加。

`chat_session_events` 的独立列为：`event_id/session_id/sequence/schema_version/event_type/status/turn_id/run_id/search_text/payload/created_at/updated_at`。
Tool Name、Call ID 和 Part Offset 从 `payload.message.parts[]` 派生；不再保存
`message_id/part_index/call_id/tool_name` SQL 列。普通问答通常只有 User 和 Assistant 两行，
一次工具往返通常是 User、Assistant Call、Tool Results、Assistant Text 四行。

## Turn、Deferred 与 Plan

- 新用户输入先生成 Run，直接使用 `turn_id = run_id`。
- Deferred 结果通过 Call ID 精确定位原 Tool Call Event；新 Run 继承原 Turn。
- 正常 Run 不落单独事件。最后一条 Assistant Message 保存 `completed/pending/interrupted` 状态。
- Planning 只暴露 `write_plan`。Call 和 Result 保留在真实 Message 中，轨迹按 Call ID 合成
  一条 Planning 项；同 Turn 的 Deferred 恢复最近成功快照，新 Turn 使用空 PlanStore。

## 恢复、搜索和压缩

写入直接持久化 `new_messages()` 中的完整 Message；读取按 `sequence` 取得
`payload.message` 并通过 Pydantic TypeAdapter 校验，不再重组 Part。未知 Message/Part 可审计，
但模型恢复和压缩失败关闭。

`search_text` 是可重建、最大 64K、NFKC 归一化的脱敏搜索投影，排除 Reasoning、Token、
Header、Cookie 和 Provider Details。中文搜索先按权限和事件范围取有界候选，再做连续子串、
单字与二元组评分。Call ID 查询在授权后的 Message Payload 中精确匹配。

压缩只替换模型可见 Surface，不删除原始 Message Event：

```text
最新 completed Compaction 合成锚点
+ source.through_sequence 之后的完整 Message Events
```

摘要采用 DSH 八段结构；超大工具结果只在摘要副本中按 `8192/4096/1024` 裁剪。
Tool/Context Evidence 由程序确定性生成，并以 `source_event_id + part_offset + call_id` 回查原始事实。
失败、超时、取消和 Stale Compaction 不改变 Surface。

## API、SSE 与 Console

普通聊天与轨迹是同一 Event Store 的两种服务端投影：

```http
GET /api/v1/sessions/{session_id}/history
```

返回稳定的 `ConversationItem[]`，供普通聊天客户端直接渲染。用户、助手、工具和 Plan
分别投影成稳定 Item；Tool/Plan 的 Call 与 Result 在服务端按 Call ID 合成。

```http
GET /api/v1/sessions/{session_id}/events
  ?after_sequence=0&limit=100
  &event_type=&turn_id=&run_id=&call_id=
```

Event API 按 Sequence 分页，内部 `search_text` 不返回，只供轨迹、审计和精确回读。
实时聊天通过 Text/Thinking/Tool 等流式事件展示当前 Run；正常收流保留本轮直播结果，切换、刷新、
重连或重新打开会话时从`/history`读取最终 Conversation 投影。
`session_event_appended/session_event_updated`继续供轨迹和标题等系统事件消费。

Event → Conversation 的确定性投影位于后端。Console 普通聊天只消费 `/history`和流式事件；
只有轨迹读取 Event API。Compaction 按真实 Sequence 穿插在轨迹中，普通 Run 不显示。实时会话允许
保留进度和压缩提示等临时状态，重新打开的 History 只展示持久化 Conversation。

Console内部不再为History与SSE维护两套展示分支：`conversation-items.js`负责History节点投影，
`assistant-presentation.js`把History和SSE转换为同一组Presenter动作，`chat.js`只负责请求编排、
DOM Presenter和页面副作用；`trajectory.js`继续独立消费Event API。

Branch From 本版不实现，也不预埋 Parent 列。稳定 Event ID、完整 Message 和工具 Call ID 已提供
未来实现分支读取所需的事实边界。
