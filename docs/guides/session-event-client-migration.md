# Conversation/History 客户端迁移清单

DeepAgent `0.6.0` 的数据库事实源仍是 `chat_session_events`，但普通聊天客户端不再解析
Event。服务端提供两个只读视图：

- `GET /api/v1/sessions/{session_id}/history`：普通聊天、工具、Plan、Deferred、附件和上下文。
- `GET /api/v1/sessions/{session_id}/events`：仅供 Console 轨迹、审计和精确回读。

两个接口都从同一 Event Store 实时投影，不双写、不新增 History 表。

## 1. History 契约

客户端类型和 Reducer 联调示例：

- `fixtures/conversation-history-v1.json`
- `fixtures/conversation-sse-v1.ndjson`

```json
{
  "schema_version": 1,
  "session_id": "...",
  "event_head_sequence": 42,
  "items": []
}
```

`ConversationItem` 公共字段：

```text
item_id / kind / status / turn_id / run_id
order.sequence / order.part_offset
source_event_ids / created_at / updated_at
```

类型：

- `user_message`：`text/attachments/context_refs`。
- `assistant_message`：`thinking/text/phase/model/provider/usage`；`phase` 为 `process/final`。
- `tool`：Call 与 Result 已按 `call_id` 合并，含 `mode/input/output/outcome/artifacts`。
- `plan`：`write_plan` 的完整快照和完成数。

稳定 ID：Message 为 `message:<event_id>`，工具为 `tool:<call_id>`，计划为
`plan:<call_id>`。Compaction、Title 和 Run Terminal 不进入普通 History。

## 2. SSE 契约

- 流式增量继续使用 `text/thinking/tool_call/tool_result/tool_call_removed`，都带 `item_id`。
- 持久化完成项统一发送 `conversation_item_upsert`，其中 `item` 与随后 `/history`
  返回的同 ID Item 完全一致。
- `session_title` 直接更新标题；客户端不轮询标题，也不需要解析 Title Event。
- `session_event_appended/updated` 只供 Console 轨迹和审计。
- `context_compaction_progress` 仍是非持久化进度；Compaction 不进入普通聊天。

客户端只维护一份 Conversation Item Store：History 全量装载、Delta 按 `item_id`
Patch、Upsert 按 `item_id` 完整替换。

## 3. DeepCube 助手（只读审计结论）

仓库：`deepfos-repos/python/element-python3`

主要影响文件：

- `packages/page/src/components/layout/content/DeepCubeChatbot/service.ts`
- `packages/page/src/components/layout/content/DeepCubeChatbot/types.ts`
- `packages/page/src/components/layout/content/DeepCubeChatbot/DeepCubeChatbot.tsx`
- `packages/page/src/components/layout/content/DeepCubeChatbot/taskPlan.ts`
- 对应 `test/*.test.ts(x)`

改造：

- 保留现有 `/history` 路径，响应类型换成 `ConversationHistoryResponse`。
- 删除本地 History Part 解析、Tool Call/Result 配对、Plan 恢复和 Pending 推导。
- Stream Reducer 增加 `conversation_item_upsert`，增量事件按 `item_id` Patch。
- 删除标题轮询，监听 `session_title`；手动重命名接口不变。
- 保留 DeepCube 专属代码应用、`config_fill` 和业务 Context 展示。
- Deferred 请求仍提交原 `tool_call_id`，服务端负责跨 Run 继承 Turn。

## 4. DeepTable 助手（只读审计结论）

仓库：`deepfos-repos/deeptable/element-deep-table`

主要影响文件：

- `src/components/aiAssistant/client/sessions.ts`
- `src/components/aiAssistant/types.ts`
- `src/components/aiAssistant/message/history.ts`
- `src/components/aiAssistant/hooks/useChatHistorySync.ts`
- `src/components/aiAssistant/hooks/useStreamRecovery.ts`
- `src/components/aiAssistant/hooks/useStreamRunControl.ts`
- `src/components/aiAssistant/taskPlan.ts`
- 对应 `**/*.test.ts(x)`

改造：

- 保留 `/history` 路径，直接把 `ConversationItem[]` 放入聊天状态。
- 删除 `HistoryMessage.parts[]` 解包、Checkpoint State、Tool 配对和本地 Plan 恢复。
- Stream Reducer 统一处理 Item Upsert/Patch；断流恢复重新拉 `/history`。
- 删除标题轮询，监听 `session_title`。
- 附件和 Context 直接读取 `user_message.attachments/context_refs`。
- Deferred、停止、会话列表、删除和手动改名请求保持不变。

本次 DeepAgent 改动没有写入上述两个外部仓库。

## 5. 最小验收

- History 加载与流式 Upsert 后的 Item 集合完全一致。
- 工具、Plan 和 Deferred 刷新前后 ID、顺序和状态不变。
- 附件、Context、Artifact 无需读取 Event Payload 即可展示。
- 标题用 `session_title` 实时更新，不轮询。
- 普通客户端不请求 Event API；Console 只有轨迹页请求 Events。
