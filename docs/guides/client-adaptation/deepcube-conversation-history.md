# DeepCube 助手 Conversation/History 改造清单

本文只描述 `element-python3` 中 DeepCube 助手如何适配 DeepAgent `0.6.0`。它保存在
DeepAgent 项目，不授权修改、提交或推送 DeepCube 仓库。

## 数据边界

- 当前 Run：消费 `thinking/text/tool_call/tool_result/tool_call_removed/final` 等实时 SSE。
- 重新打开、刷新或断流恢复：读取 `GET /api/v1/sessions/{session_id}/history`。
- 标题：消费 `session_title`，不轮询。
- 自动压缩：只消费当前 Run 的 `context_compaction_progress`，History 不恢复标记。
- 普通聊天不请求 Event API，也不解析 Event Payload。

实时状态与 History 允许不同。实时页面可以展示思考、工具执行进度和压缩提示；History 只展示服务端
从 Event Store 投影的最终 `ConversationItem[]`。客户端不需要把直播草稿替换成第二份持久化 Item。

## History 类型

```text
ConversationHistoryResponse
  schema_version
  session_id
  event_head_sequence
  items[]

ConversationItem
  item_id / kind / status / turn_id / run_id
  order.sequence / order.part_offset
  source_event_ids / created_at / updated_at
```

四类 Item：

- `user_message`：正文、附件、Context Reference。
- `assistant_message`：合并后的 Thinking、Text、模型和 Usage。
- `tool`：服务端已按 Call ID 合并的 Call/Result，包含 Deferred 状态。
- `plan`：`write_plan` 完整快照。

## 文件影响

### `types.ts`

- 增加 `ConversationHistoryResponse` 和四类 Conversation Item。
- 删除旧 `HistoryMessage/HistoryPart/context_checkpoint/model_message_count`。
- 保留实时 Delta、`session_title`、`final/cancelled/error` 类型。

### `service.ts`

- 保留 `/history` URL、鉴权 Header 和错误处理。
- 返回并校验 `ConversationHistoryResponse`，按 `order + item_id` 排序。
- 删除旧 Message Part 解包、Tool 配对、Checkpoint、Plan 恢复和标题轮询。

### `DeepCubeChatbot.tsx`

- 当前 Run 继续使用既有实时草稿、工具卡和 `final` 收口。
- `loadSession()`、刷新和断流恢复直接装载 `history.items`。
- 不增加持久 Item SSE 替换逻辑。
- 保留 DeepCube Markdown、代码应用、`config_fill`、`deepcube_clarify`、附件和业务 Context。

### `taskPlan.ts`

- 历史计划直接读取 `kind=plan`，不扫描 History Part。
- 当前实时计划继续使用 `tool_result.plan`。
- 当前 Run 结束时隐藏 Composer 计划；History Item 仍保留。

## 验收

- 实时 Thinking/Text、工具、Plan、Deferred 不重复。
- `final` 后正文只出现一次。
- 刷新后从 `/history`恢复消息、工具、Plan、附件和 Context。
- Deferred 刷新后仍使用原 Call ID 恢复交互。
- 自动压缩当前 Run 可见，刷新 History 后不显示。
- 标题实时更新且没有轮询。
