# DeepCube 助手 Conversation/History 改造清单

本文只描述 `element-python3` 中 DeepCube 助手如何适配 DeepAgent `0.6.0`
的 Conversation/History 与 SSE 契约。它是 DeepAgent 项目内的交接文档，不修改、
不兼容包装、也不向 DeepCube 仓库写入任何文件。

## 1. 审计基线

- 仓库：`deepfos-repos/python/element-python3`
- 分支：`test`
- 提交：`f10caf371479`
- 审计时工作区：干净
- 主要目录：`packages/page/src/components/layout/content/DeepCubeChatbot/`

DeepCube 当前已经请求
`GET /api/v1/sessions/{session_id}/history`，但按旧结构读取
`messages[] + context_checkpoint`。随后由前端拆 `parts[]`、合并相邻 Assistant、
配对 Tool Call/Result、恢复 Plan、推导 Pending，并轮询会话标题。

这些职责现在都已有服务端统一投影，客户端不应再重复实现。

## 2. 改造后的数据流

```text
/history.items
       +
SSE conversation_item_upsert
       ↓
ConversationItem Store（唯一聊天状态）
       ↓
DeepCube 视图选择器
       ↓
普通消息 / 工具卡片 / Plan / 附件 / Context / Markdown 代码应用
```

轨迹 Event API 不属于 DeepCube 助手的普通聊天依赖。DeepCube 不需要消费
`session_event_appended`、`session_event_updated`，也不需要理解 Compaction Event。

历史加载规则：

1. 校验 `schema_version === 1`。
2. 以 `items` 全量替换本地持久化 Item Store。
3. 按 `order.sequence + order.part_offset + item_id` 排序。
4. 不读取或自行构造 Checkpoint、Message Part、Call/Result 配对结果。

直播规则：

- `text/thinking`：按事件的 `item_id` 追加到直播 Assistant 草稿。
- `tool_call/tool_result/tool_call_removed`：按 `item_id` 更新临时工具项。
- `conversation_item_upsert`：按 `item.item_id` 完整替换持久化项。
- `session_title`：直接更新当前标题和会话列表，不再轮询。
- `final/cancelled/error`：只结束当前 Run，不把 `final.output` 再追加为第二份消息。
- 断流恢复：重新拉 `/history`，以服务端投影覆盖本地持久化状态。

工具从开始起就是稳定的 `tool:<call_id>` 或 `plan:<call_id>`。Assistant Delta 使用
`live:<run_id>:assistant`，持久化后会变为 `message:<event_id>`；收到同 `run_id`
的持久化 Assistant Upsert 时，应移除对应直播草稿，避免重复正文。首条用户消息也可
先乐观展示，收到同一 `run_id/turn_id` 的 `user_message` 后以持久化项替换。

## 3. 契约映射

| Conversation Item | DeepCube 展示 | 关键字段 |
| --- | --- | --- |
| `user_message` | 用户正文、附件、页面上下文 | `text`、`attachments`、`context_refs` |
| `assistant_message` | 一条助手回复，Thinking 折叠、Text 正常展示 | `thinking`、`text`、`phase`、`status` |
| `tool` | 一条工具或 Deferred 卡片 | `call_id`、`mode`、`status`、`input/output`、`widget/metadata` |
| `plan` | Composer 上方任务计划 | `plan`、`total`、`completed`、`status` |

状态不要再压缩成旧的 `running/pending/success/error/interrupted` 五种。建议直接保留
服务端状态：

- `pending`：普通工具已发起，尚无结果。
- `awaiting_input`：Deferred/UI Tool 等待用户确认，可继续提交原 `call_id`。
- `completed`：工具、消息或 Plan 已完成。
- `failed/rejected/interrupted/removed`：分别展示失败、拒绝、中断或已撤销。

`tool.mode === "deferred" && status === "awaiting_input"` 才是可交互 UI Tool。
刷新历史不会让它自动失效；仍应使用原 `call_id` 提交 `deferred_tool_results`。

Plan 不再靠 `source=planning + tool name` 从工具片段中识别。服务端已经投影成
`kind=plan`，当前只会提供 `write_plan`。客户端保留所有 Plan Item 作为历史事实，
Composer 只选择当前 Turn 最新一份；`completed === total` 时按当前产品规则隐藏面板。

## 4. 文件级修改清单

### `types.ts`

新增或替换：

- `ConversationHistoryResponse`、四类 `ConversationItem`、`ConversationStatus`。
- SSE 的 `conversation_item_upsert` 和 `session_title` 类型。
- `text/thinking/tool_*` Delta 的 `item_id`。
- Tool 展示状态支持 `awaiting_input/rejected/removed`。

删除：

- `HistoryMessage`、`HistoryPart` 及全部旧 History Part 子类型。
- `HistoryContextCheckpoint`。
- `SessionHistoryResponse.messages/context_checkpoint/model_message_count`。
- 只为旧 Checkpoint 服务的 `branchPoint`。

附件类型可继续保留 DeepCube 的 `previewUrl/extracted_text` 等视图字段，但服务端
`ConversationAttachment` 只保证 `id/name/mime/byte_size/content_hash`；预览 URL 仍由
客户端依据 File ID 派生，不能要求 History 返回文件正文。

### `service.ts`

保留：

- `fetchAgentSessionHistory()` 的 URL、鉴权 Header 和错误处理。
- Chat、附件、Deferred、取消、手动改名请求。
- DeepCube Context Reference 的构造与展示摘要。

重写：

- `fetchAgentSessionHistory()` 返回 `ConversationHistoryResponse`。
- 增加纯数据的 `normalizeConversationHistory()`、排序和按 ID Upsert 辅助函数；可以
  直接移植 DeepAgent Fixture 对应的最小逻辑，不解析 Event Payload。
- 将现有直播 Assistant/Tool 更新逻辑改为 Item Store Patch，而不是修改某一条
  `ChatMessage.segments` 后再等待历史重建。

删除：

- `historyMessagesToChatMessages()`。
- `buildChatTimelineItems()` 中的 Checkpoint 插入逻辑。
- `isValidHistoryContextCheckpoint()` 及边界判断。
- `pollAgentSessionTitle()`、`isGeneratedSessionTitle()` 和轮询常量/测试注入参数。
- 旧 History 中相邻 Assistant 合并、Tool Call/Result 配对、Pending 推导。

### `DeepCubeChatbot.tsx`

状态层：

- 用 `ConversationItem[]` 或 `Map<item_id, ConversationItem>` 保存持久化会话。
- 直播草稿单独保存，只承接尚未落定的 Delta。
- 普通消息列表由轻量视图选择器生成；选择器只决定视觉分组，不重新推导工具结果。

事件层：

- `handleStreamEvent()` 增加 `conversation_item_upsert` 和 `session_title`。
- Tool/Plan/Deferred 都按服务端 `kind/status/mode` 更新。
- `final` 不再清空或重新猜 Plan，不再从 `final.deferred.calls` 建立持久化工具事实；
  该字段只可作为 Upsert 到达前的瞬时兜底。
- `loadSession()`、首次加载和断流恢复直接装载 `history.items`。

删除：

- `contextCheckpoint` State、Checkpoint History Refresh 和聊天区压缩分隔标记。
- `restoreTaskPlan(history.messages)`。
- `titlePollAbortRef`、`cancelTitlePolling()`、`startTitlePolling()` 及首次发送后的轮询。
- 历史恢复时按用户正文匹配、再判断后面有没有 Assistant 的旧逻辑；改为按本次
  `run_id/turn_id` 和可渲染 Item 判断。

保留：

- DeepCube Markdown、`deepcube` 代码块插入/覆盖预览。
- `config_fill` 代码块表单和页面配置写入。
- `deepcube_clarify` Deferred 卡片。
- 图片/文本附件预览、模型图片能力校验。
- `deepcube.editor_context` 的构造、去重和展示。

### `taskPlan.ts`

- 删除 `HistoryMessage[]` 扫描、Run 内重组和 Pending UI Tool 推导。
- 删除 `read_plan/update_task_statuses` 兼容，只认服务端 `kind=plan`。
- 保留 Plan Snapshot 的前端显示校验，但输入改为 `PlanConversationItem.plan`。
- 提供“当前 Turn 最新计划”的选择器；完成计划是否隐藏只属于显示规则。

### 测试

重点替换：

- `test/service.test.ts`
- `test/taskPlan.test.tsx`
- 直接或间接依赖标题轮询、Checkpoint、History Part 的组件测试

使用 DeepAgent 提供的：

- `docs/guides/fixtures/conversation-history-v1.json`
- `docs/guides/fixtures/conversation-sse-v1.ndjson`

至少覆盖：History 与 Upsert 等价、Assistant 草稿替换、单/多工具、Plan、
Deferred 刷新后仍可提交、失败/中断/撤销、附件、Context、标题 SSE 和断流恢复。

## 5. 明确不改的业务协议

- `/history`、Chat、Stream、Cancel、Attachment、Session Rename 的 URL 不变。
- `attachments: [{id,name,mime}]` 不变。
- `context_refs` 不变。
- Deferred 继续发送 `deferred_tool_results: [{tool_call_id,result}]`。
- DeepCube 的 App/Space/Language Header 和固定 Agent 选择逻辑不变。
- DeepCube 页面代码、配置填充和 Context 业务能力不下沉到 DeepAgent。

## 6. 验收标准

- 客户端不再出现 `HistoryMessage.parts[]`、`context_checkpoint` 或 Event Payload 解析。
- 刷新前后消息、工具、Plan、Deferred、附件和 Context 的 ID、顺序、状态一致。
- 一个 Tool Call/Result 只渲染一个 `tool:<call_id>`。
- 一个 `write_plan` 只渲染一个 `plan:<call_id>`。
- 历史中的 `awaiting_input` UI Tool 可以继续原 Turn；终态工具不可再次提交。
- 标题由 `session_title` 即时更新，不产生轮询请求。
- Compaction 只可显示当次非持久化进度，不进入历史聊天列表。
