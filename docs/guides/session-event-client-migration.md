# Conversation/History 客户端迁移清单

DeepAgent `0.6.6` 的唯一数据库事实源是 `chat_session_events`。普通聊天客户端不解析 Event：

- `GET /api/v1/sessions/{session_id}/history`：普通聊天、工具、Plan、Deferred、附件和上下文。
- `GET /api/v1/sessions/{session_id}/events`：仅供 Console 轨迹、审计和精确回读。

两个接口都从同一 Event Store 实时投影，不双写、不新增 History 表。

## 1. History 契约

客户端类型和联调示例：

- `fixtures/conversation-history-v1.json`
- `fixtures/conversation-sse-v1.ndjson`

两个客户端的逐文件改造说明：

- `client-adaptation/deepcube-conversation-history.md`
- `client-adaptation/deeptable-conversation-history.md`

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
- `tool`：Call 与 Result 已按 `call_id` 合并，含稳定字段 `tool_name`，以及
  `display_name/mode/input/output/outcome/artifacts`。
- `plan`：`write_plan` 的完整快照、完成数和可选 `display_name`。

`display_name` 只用于普通会话展示，客户端使用 `display_name || tool_name`。它在每次读取 History 时，
根据会话所属 Agent 的当前内置工具元数据、UI 工具 `metadata.display_name` 和 MCP `Tool.title` 动态补充；
旧 Event 不迁移，Event Payload、数据库和 `/events` 响应中都不保存该字段。调用协议、轨迹和审计继续使用
原始 `tool_name`。

稳定 ID：Message 为 `message:<event_id>`，工具为 `tool:<call_id>`，计划为
`plan:<call_id>`。Compaction、Title 和 Run Terminal 不进入普通 History。

## 2. SSE 契约

- 当前 Run 使用 `text/thinking/tool_call/tool_result/tool_call_removed` 增量展示；工具生命周期帧保留
  原 `tool` 并额外携带 `display_name`，Deferred Call/Approval 同样如此。
- `session_title` 直接更新标题；客户端不轮询标题，也不需要解析 Title Event。
- `session_event_appended/updated` 只供 Console 轨迹和审计。
- `context_compaction_progress` 只在当前实时会话展示；重新打开 History 不恢复压缩标记。
- `final/cancelled/error` 收口当前 Run。正常结束保留直播结果；切换、刷新、断流恢复或重新打开会话时读取 `/history`。

实时 UI 与 History 不要求逐项相同：直播可以保留思考、进度、工具执行和压缩提示，History 只展示
Event Store 的最终 Conversation 投影。客户端不需要维护“临时项覆盖为持久化项”的第二套状态机。

## 3. DeepCube 助手

仓库：`deepfos-repos/python/element-python3`

主要影响文件：

- `packages/page/src/components/layout/content/DeepCubeChatbot/service.ts`
- `packages/page/src/components/layout/content/DeepCubeChatbot/types.ts`
- `packages/page/src/components/layout/content/DeepCubeChatbot/DeepCubeChatbot.tsx`
- `packages/page/src/components/layout/content/DeepCubeChatbot/taskPlan.ts`
- 对应 `test/*.test.ts(x)`

改造：

- 保留 `/history` 路径，响应类型换成 `ConversationHistoryResponse`。
- 删除旧 History Part 解析、Tool Call/Result 配对、Checkpoint 和 Plan 恢复。
- 实时链路继续使用现有 Delta 与 `final`；不消费 Event API 或持久化 Item 同步通知。
- 删除标题轮询，监听 `session_title`；手动重命名接口不变。
- 保留 DeepCube 专属代码应用、`config_fill`、业务 Context 和 Deferred。
- 断流或重新打开会话时重新读取 `/history`。

## 4. DeepTable 助手

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

- 保留 `/history` 路径，直接消费 `ConversationItem[]`。
- 删除旧 `messages[].parts` 解包、Checkpoint、Tool 配对和 Plan 恢复。
- 实时链路继续使用现有 Delta 与 `final`，断流后重新请求 `/history`。
- 删除标题轮询，监听 `session_title`。
- 附件和 Context 直接读取 `user_message.attachments/context_refs`。
- Deferred、停止、会话列表、删除和手动改名请求保持不变。

本次 DeepAgent 改动不会写入上述两个外部仓库。

Alembic `010`只删除配置目录中Provider、模型、Skill、MCP、UI工具包和UI工具的`enabled`字段，并把
Agent能力收敛为显式列表；Chat、History、SSE、附件、Plan和Deferred契约没有变化。两个普通聊天
客户端不读取这些配置目录字段，因此不需要因010调整运行代码；只有外部管理员页面若直接维护上述
资源，才需删除对应启停字段和操作。

Alembic `011`仅恢复模型级`enabled`。普通`GET /llm-models`仍表示当前可用模型列表，停用模型会由
服务端过滤；DeepCube和DeepTable继续使用原接口，无需解析新字段或修改聊天协议。只有模型管理页面
需要显式请求`selectable_only=false`并提供启停操作。

## 5. 最小验收

- 实时 Text/Thinking/Tool/Plan/Deferred 正常，不重复正文或工具卡。
- 刷新后 `/history` 的 ID、顺序、状态、附件和 Context 正确。
- 断流后重新读取 `/history` 能恢复持久化终态。
- 标题用 `session_title` 实时更新，不轮询。
- 自动压缩只在当前实时会话显示，History 不恢复标记。
- 普通客户端不请求 Event API；Console 只有轨迹页请求 Events。
