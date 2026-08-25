# Session Event 客户端迁移清单

DeepAgent `0.6.0` 不再提供旧的会话 History 聚合结果。客户端必须从 Session
Event Store 恢复聊天、工具、Plan 和压缩标记。本说明记录电子表格助手、
DeepCube 助手以及通用 `ai-components` 的实际影响，不要求 DeepAgent 发布任务
跨仓修改这些客户端。

## 1. 新契约

### 1.1 读取历史

```http
GET /api/v1/sessions/{session_id}/events
    ?agent_id={agent_id}
    &after_sequence=0
    &limit=500
```

响应：

```json
{
  "items": [
    {
      "event_id": "...",
      "session_id": "...",
      "sequence": 12,
      "schema_version": 1,
      "event_type": "assistant/text",
      "status": null,
      "turn_id": "...",
      "run_id": "...",
      "message_id": "...",
      "part_index": 0,
      "call_id": null,
      "tool_name": null,
      "payload": {},
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "next_after_sequence": null
}
```

当 `next_after_sequence` 非空时继续翻页，直到返回 `null`。客户端按
`sequence` 排序，不能按时间戳排序，也不能只读取第一页。

旧响应中的下列字段全部删除：

- `messages`
- `model_message_count`
- `context_checkpoint`

内置 Console 的确定性投影可作为实现参考：
[`frontend/console/session-events.js`](../../frontend/console/session-events.js)。

投影规则：

- 同一 `message_id` 的事件按 `part_index` 重组一个模型消息。
- `tool/call` 与 `tool/result` 是两行，通过 `call_id` 配对。
- `run` 事件提供 Run 终态；`cancelled/interrupted` 投影为中断状态。
- `write_plan` 的成功 `tool/result` 从
  `payload.part.metadata.deepagent_plan` 读取完整快照。
- `compaction` 是独立可视事件，不属于普通聊天消息。
- 未识别的 `schema_version` 或 `model/unknown_part` 必须显式降级，不能静默丢弃。

### 1.2 SSE

新增持久化事件：

- `session_event_appended`：`data.persisted=true`，`data.event` 是完整 Event。
- `session_event_updated`：同一 `event_id` 的生命周期更新，客户端必须 Upsert，
  不能追加第二行。

压缩等待事件改为：

- `context_compaction_progress`：`persisted=false`，阶段为 `preparing`、
  `summarizing`、`validating`、`committing` 或 `retry_wait`。

聊天 Token Delta、Thinking 和临时工具状态仍是非持久化直播事件。刷新后的最终
状态以 Event API 为准。

### 1.3 不变与简化

- `deferred_tool_results` 请求体和 `tool_call_id` 不变。
- Deferred 恢复会创建新 `run_id`，但服务端自动继承原 `turn_id`；客户端不要
  以单个 Run 作为完整用户交互边界。
- Plan 只识别 `write_plan`，删除 `read_plan` 和 `update_task_statuses`。
- Session ID 仍由服务端创建；客户端只需把服务端返回值作为全局唯一、不透明 ID。
- `app_id`、`space_id` 是可空来源信息，当前授权仍是 `agent_id + user_id`；现有
  客户端不需要伪造默认 App 或 Space。
- 会话列表、删除、改名、取消和 Chat 请求路径不因 Event Store 改变。

## 2. DeepCube 助手

仓库：
`deepfos-repos/python/element-python3`

必须修改：

| 文件 | 当前依赖 | 修改要求 |
| --- | --- | --- |
| `packages/page/src/components/layout/content/DeepCubeChatbot/service.ts` | `fetchAgentSessionHistory()` 请求 `/history`；`buildChatTimelineItems()` 插入 Checkpoint | 改为 Events 分页读取；增加 Event 投影；按真实 `compaction.sequence` 构建时间线 |
| `packages/page/src/components/layout/content/DeepCubeChatbot/types.ts` | `SessionHistoryResponse`、`HistoryContextCheckpoint`、旧 `context_compaction` SSE | 替换为 Event DTO、分页响应、`session_event_appended/updated` 和 `context_compaction_progress` 类型 |
| `packages/page/src/components/layout/content/DeepCubeChatbot/DeepCubeChatbot.tsx` | 加载、断流恢复和压缩后刷新都读取 `history.messages/context_checkpoint` | 统一读取 Event 投影；直播 Event 按 `event_id` Upsert；不再维护独立 Checkpoint State |
| `packages/page/src/components/layout/content/DeepCubeChatbot/taskPlan.ts` | 同时识别三个 Planning 工具，并按最新单 Run 恢复 | 只识别 `write_plan`；历史恢复按最后成功快照，Run 分组使用 `turn_id` 处理 Deferred |
| `packages/page/src/components/layout/content/DeepCubeChatbot/ContextCompaction.tsx` | 展示旧阶段和 Checkpoint 投影 | 接收新五阶段进度；持久化标记来自 `compaction` Event，历史普通聊天可隐藏，轨迹按 Sequence 展示 |
| `packages/page/src/components/layout/content/DeepCubeChatbot/test/service.test.ts` | 断言旧 History/Checkpoint | 改为 Events 分页、消息重组、Call/Result 配对和压缩原位顺序测试 |
| `packages/page/src/components/layout/content/DeepCubeChatbot/test/taskPlan.test.tsx` | 覆盖三个旧 Plan 工具 | 删除两个旧工具用例，增加跨 Run 同 Turn 的 `write_plan` 恢复用例 |
| `packages/page/src/components/layout/content/DeepCubeChatbot/test/contextCompaction.test.tsx` | 使用旧压缩阶段 | 改为新阶段和同 Event ID Upsert 测试 |
| `docs/ui-tools-external.md` | 示例仍使用旧 History/Checkpoint | 更新为 Event API 与 `call_id` 精确回读说明 |

可直接保留：

- `service.ts` 中 Chat 请求的 `session_id`。
- `service.ts` 中 `deferred_tool_results`。
- `session` SSE 返回后确认真实 Session ID 的流程。
- 会话列表、标题轮询、删除和取消流程。

## 3. 电子表格助手

仓库：
`deepfos-repos/deeptable/element-deep-table`

必须修改：

| 文件 | 当前依赖 | 修改要求 |
| --- | --- | --- |
| `src/components/aiAssistant/client/sessions.ts` | `fetchAgentSessionHistory()` 请求 `/history` | 改为带 Cursor 的 Events 分页函数 |
| `src/components/aiAssistant/types.ts` | 旧 History、Checkpoint 和压缩 SSE 类型 | 增加 Event DTO；删除 Checkpoint；补充持久化 Event SSE 与新压缩阶段 |
| `src/components/aiAssistant/message/history.ts` | 从 `HistoryMessage[]` 投影聊天，并按 `source_last_sequence` 插标记 | 改为从 Event[] 按 Message/Part/Run 投影；压缩标记直接使用 Event Sequence |
| `src/components/aiAssistant/hooks/useChatHistorySync.ts` | 刷新 `messages/context_checkpoint` | 改为一次 Events 投影，同时更新消息、Plan 和压缩可视项 |
| `src/components/aiAssistant/hooks/useStreamRecovery.ts` | 断流后请求旧 History | 请求 Events，并以 `turn_id/sequence` 判断本轮是否已有持久化助手输出 |
| `src/components/aiAssistant/hooks/useStreamRunControl.ts` | 只处理旧 `context_compaction` | 处理新进度事件以及 `session_event_appended/updated` Upsert |
| `src/components/aiAssistant/hooks/useAgentChat.ts` | 压缩后刷新旧 History | 改为刷新 Events；Deferred 请求参数保持不变 |
| `src/components/aiAssistant/state/chatAtoms.ts` | 独立 `contextCheckpointAtom` | 删除 Checkpoint Atom；保留或改造为 Event Map 与当前投影状态 |
| `src/components/aiAssistant/components/MessageList.tsx` | 渲染运行时 Checkpoint Marker | 直接消费按 Sequence 排好的 Event 可视投影 |
| `src/components/aiAssistant/components/ContextCompaction.tsx` | 维护旧阶段和 Marker/Checkpoint 合并 | 使用新五阶段；持久化标记由同一个 Compaction Event Upsert |
| `src/components/aiAssistant/taskPlan.ts` | 三个 Planning 工具、最新单 Run 恢复 | 只保留 `write_plan`；恢复时以 Turn 覆盖 Deferred 跨 Run |
| `src/components/aiAssistant/**/*.test.ts(x)` | 多处 fixture 使用 `messages/context_checkpoint` | 统一改 Event fixture，覆盖分页、Upsert、Turn/Run、Plan 和断流恢复 |
| `docs/design/03-AI智能助手/04-统一AI对话框架构.md` | 记录旧历史和压缩契约 | 更新 Event Store、SSE 与 Plan 口径 |

可直接保留：

- `src/components/aiAssistant/client/chat.ts` 的 `session_id` 和
  `deferred_tool_results` 请求。
- 现有停止生成、会话列表、标题、删除和 UI Tool 提交主流程。

## 4. 额外发现：通用 ai-components

仓库 `deepfos-AI/ai-components` 也有一个真实旧契约消费者：

- `packages/ai-web/src/pages/main/hooks/deepAgent.ts`

该文件的 `getDeepAgentSessionHistory()` 仍请求 `/history`，并将
`HistoryMessage.parts[]` 转成页面 Item。它至少需要改为 Event 分页和消息/工具投影，
否则 `0.6.0` 发布后打开历史会返回 404。它不是本次指定的两个助手，但必须加入
后续前端发布清单。

## 5. 客户端验收最小集

- 超过 500 个 Event 的会话能完整翻页，无重复、无漏行。
- 刷新前直播内容与刷新后 Event 投影一致。
- 同一 `session_event_updated.event_id` 不产生重复 Run/Compaction 行。
- Tool Call/Result 跨 Deferred Run 仍按 `call_id` 配对，并保留共同 `turn_id`。
- 压缩标记在真实 Sequence 位置；历史普通聊天可隐藏，轨迹必须显示。
- 300 秒摘要期间每约 10 秒能看到进度，不因无 Provider Token 断开。
- Plan 只消费 `write_plan`，Deferred 恢复后仍显示同 Turn 最新快照。
- `app_id/space_id` 缺失时，会话读取和授权不受影响。
