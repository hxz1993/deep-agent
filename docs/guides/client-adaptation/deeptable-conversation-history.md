# DeepTable 助手 Conversation/History 改造清单

本文只描述 `element-deep-table` 中电子表格 AI 助手如何适配 DeepAgent `0.6.0`
的 Conversation/History 与 SSE 契约。文档保存在 DeepAgent 项目；不会写入或整理
DeepTable 仓库中的现有改动。

## 1. 审计基线

- 仓库：`deepfos-repos/deeptable/element-deep-table`
- 分支：`feat/ai-assistant-panel-ruge-0806`
- 提交：`75b60f808acd`
- 审计时已有用户改动：`vite.config.ts`，以及一个未跟踪的设计目录；本次未触碰
- 主要目录：`src/components/aiAssistant/`

DeepTable 当前同样已经请求 `/history`，但响应仍按
`messages[] + context_checkpoint` 处理。旧投影逻辑分别存在于 Types、History、
Chat History Sync、Stream Recovery、Stream Reducer、Plan 和 Jotai State 中。

这次适配应直接切换到 Conversation Item，不保留旧 History 兼容层。

## 2. 改造后的分层

建议把状态分为两类：

- `conversationItemsAtom`：服务端持久化 Conversation Item，History 全量装载，Upsert
  完整替换。
- `liveItemsAtom`：当前 Run 的未持久化草稿和 Delta；Run 终态后清理。

```text
/history.items ───────────────┐
conversation_item_upsert ─────┼─→ Conversation Item Store ─→ UI selectors
text/thinking/tool_* delta ───┘                ↓
                                  Message / Tool / Plan / Deferred
```

现有 `ChatMessage + segments` 可以作为短期纯视图模型继续存在，但不能再成为事实源。
如果保留，必须由 `ConversationItem[]` 确定性派生，不能再解析 Message Part、配对工具
或猜测终态。更干净的做法是让 `MessageList` 直接消费按 Turn 分组后的 Item View。

## 3. Conversation Item 在 DeepTable 中的表达

### 用户消息

`user_message` 直接提供：

- `text`：正文。
- `attachments`：附件引用和稳定元数据。
- `context_refs`：页面、Sheet、选区等安全引用。

DeepTable 继续用现有 `renderContextPreview`、附件 Chip 和图片预览表现这些字段。
`context_refs` 中的 `rangeId/baselineRevision/writeMask` 等业务 Payload 仍由页面适配器
解释；DeepAgent 不替代电子表格的冲突校验或写入权限判断。

### 助手消息

一个 `assistant_message` 已经合并 Thinking 与 Text。客户端只做折叠展示，不再合并
相邻 Assistant 记录。`phase=process/final` 用于区分过程说明和当前 Turn 的最终正文。

### 工具与 Deferred

一个 `tool` 已经由服务端按 `call_id` 合并 Call/Result：

- `mode=deferred + status=awaiting_input`：渲染可提交 UI Tool 卡片。
- `pending`：普通工具执行中。
- `completed/failed/rejected/interrupted/removed`：只读终态。
- `input/output/artifacts`：直接用于详情和大结果回读入口。

历史恢复出来的 Deferred 不应因为“来自 History”自动变成过期只读。是否可操作只看
`mode/status` 和 UI Tool Registry 是否匹配。用户确认或取消仍提交原 `call_id`。

### Plan

`plan` 是独立 Item，不再从 Tool Part 恢复。只支持 `write_plan`：

- `plan` 是完整快照。
- `total/completed` 是服务端投影计数。
- Composer 读取当前 Turn 最新一项。
- `completed === total` 时按当前产品要求隐藏已完成计划面板，但 Item 仍保留在 Store。
- Pending、Interrupted、Failed 的展示直接使用 Item Status，不扫描 Assistant Run。

## 4. 文件级修改清单

### `src/components/aiAssistant/types.ts`

新增：

- `ConversationHistoryResponse`。
- `ConversationItemBase`、`UserConversationItem`、`AssistantConversationItem`、
  `ToolConversationItem`、`PlanConversationItem`。
- `conversation_item_upsert`、`session_title` SSE 类型。
- Delta 事件的 `item_id`。

删除：

- `HistoryMessage`、`HistoryPart` 和旧 History Part 子类型。
- `HistoryContextCheckpoint`、`RuntimeContextCompactionMarker`。
- `context_checkpoint/model_message_count/messages` 旧 History 字段。
- `branchPoint` 及 `restoredFromHistory`。
- Tool 的 `restored` 只读兼容标记。

将 Tool Status 对齐服务端七种状态，不再把 `completed` 改名为 `success` 后又在各处
反向判断。

### `src/components/aiAssistant/client/sessions.ts`

- 保留 `/history` URL、鉴权 Header 和 `agent_id` 参数。
- `fetchAgentSessionHistory()` 返回新类型。
- 删除 `isGeneratedSessionTitle()`、`pollAgentSessionTitle()`、轮询常量与测试注入接口。
- Session 列表、删除、取消、手动改名 API 不变。

### `src/components/aiAssistant/message/history.ts`

建议重命名为 `conversation.ts`，职责改为：

- History Schema 校验。
- Item 排序和按 ID Upsert。
- 将 Item 按用户 Turn 分成视觉 Group。
- 找到最新 Plan、当前可交互 Deferred、可渲染 Assistant 输出。

完整删除：

- `historyMessagesToChatMessages()`。
- `mergeAssistantMessage()`。
- `restoreUserMessage()/restoreAssistantMessage()` 的 Part 解析。
- Checkpoint 边界和 `buildChatTimelineItems()` 的 Marker 插入。
- Tool Call/Result 本地配对和 Pending 推导。

### `src/components/aiAssistant/state/chatAtoms.ts`

- 用 `conversationItemsAtom/liveItemsAtom` 替换 `messagesAtom` 的事实源职责。
- 删除 `contextCheckpointAtom` 和 `runtimeContextCompactionMarkersAtom`。
- `automaticContextCompactionAtom` 只保留当前请求的临时进度；完成后清理，不转成历史
  Checkpoint Marker。
- `taskPlanAtom` 可保留为视图缓存，也可以改为从 Items 派生；不得再单独恢复另一份事实。

### `src/components/aiAssistant/hooks/useChatHistorySync.ts`

- `replaceWithHistory()` 直接校验并写入 `history.items`。
- 删除 Checkpoint 校验、Marker 回填、Plan Part 扫描。
- 如果页面仍需要释放本地 Blob URL，仅释放前一次本地预览，不把平台附件当 Blob 清理。
- 压缩完成后无需专门刷新“Checkpoint History”；若产品需要确保最终 Item 完整，只做
  普通 `/history` 重同步。

### `src/components/aiAssistant/hooks/useStreamRecovery.ts`

- 断流后重新请求 `/history`，以 Items 覆盖持久化 Store。
- 判断本轮是否已有输出时，按 `run_id/turn_id` 查找 Assistant/Tool/Plan，而不是按用户
  正文反向搜索。
- 删除 Checkpoint State 回填。

### `src/components/aiAssistant/hooks/useStreamRunControl.ts`

- `handleStreamEvent()` 新增 `conversation_item_upsert`：完整替换同 ID Item。
- `session_title` 直接更新 Session Atom。
- Delta 只更新 Live Store。
- 收到持久化 Assistant Upsert 时，清理同 `run_id` 的直播 Assistant 草稿。
- `final/cancelled/error` 只管理发送、停止和错误提示，不再次构造聊天事实。
- 保留正式 Cancel API 语义：`cancel_requested` 后继续消费 SSE，直到真实 `cancelled`。

### `src/components/aiAssistant/message/applyStreamEvent.ts`

可重写为 `applyConversationDelta.ts`：

- 只处理未持久化 Delta。
- Tool 直接用事件 `item_id`，不生成 `tool-call-${index}` 之类不稳定 ID。
- 不在 `final.deferred.calls` 中重建持久化 Pending；等待正式 Tool Upsert，字段只能作为
  网络时序中的瞬时兜底。
- Plan Delta 只显示临时占位，正式快照来自 `plan` Upsert。

### `src/components/aiAssistant/hooks/useSessionTitle.ts`

删除整个轮询 Hook。标题更新放入统一 Stream Reducer：

- `session_title.session_id/title` 更新当前标题和 Session List。
- 手动 Rename 仍使用现有 PATCH。
- 页面刷新从 Session 列表/详情读取最终标题。

### `src/components/aiAssistant/taskPlan.ts`

- 删除 `SUPPORTED_PLANNING_TOOLS` 中的 `read_plan/update_task_statuses`。
- 删除 `HistoryMessage[]`、Run Status 和 Pending UI Tool 扫描。
- 改为从 `PlanConversationItem[]` 选择当前 Turn 最新快照。
- `normalizeAgentPlanSnapshot()` 可保留为渲染层防御校验。

### `src/components/aiAssistant/capabilities/uiToolRegistry.ts`

- `hasPendingUiInteraction()` 直接扫描 `tool` Item：
  `mode=deferred && status=awaiting_input && registry.isUiTool(...)`。
- 删除 `restored` 特判和 `isRestoredUiTool()`。
- History 中仍 Pending 的 UI Tool 是可恢复交互，不应自动提示“确认已失效”。

### `src/components/aiAssistant/components/ToolCallLine.tsx`

- 使用服务端 `status/mode/input/output/artifacts`。
- `awaiting_input` 才渲染交互卡片。
- `failed/rejected/interrupted/removed` 分别展示，不统一伪装成“已使用”。
- 删除“历史还原一律只读”的分支。

### `src/components/aiAssistant/components/MessageList.tsx`

- 从 Conversation Group Selector 读取用户消息及其后的 Assistant/Tool/Plan Items。
- 不再注入历史或运行时 Checkpoint Marker。
- `context_compaction_progress` 可以保留成当前请求的临时提示，终态后消失。
- 保留 Context Preview、附件、Markdown、代码块应用和建议追问。

### `src/components/aiAssistant/hooks/useAgentChat.ts`

- 乐观用户消息和直播 Assistant 改写 Live Store。
- 首轮不再启动标题轮询。
- `loadSession()` 直接装载 `history.items`。
- `runDeferred()`、`submitToolResult()`、`cancelToolCall()` 请求体保持不变。
- `refreshCheckpointHistory()` 改为普通 History Reconcile 或直接删除。

## 5. 电子表格业务能力保留边界

以下不因 Conversation 改造而下沉或删除：

- Page/Sheet/Range Context Adapter 和稳定签名。
- `rangeId/baselineRevision/writeMask` 冲突检测。
- Smart Fill 预览、确认覆盖、应用和撤回。
- `config_fill` Markdown 解析与业务代码块操作。
- UI Tool Registry 的业务卡片注册。
- 附件上传、图片能力判断、附件和图片预览。
- 开场问题、建议追问、维度展示偏好。
- App/Space/Language Header。

Conversation 层只提供稳定、已配对、已定状态的展示数据；是否真的写入单元格以及如何
解决覆盖冲突，仍完全由 DeepTable 页面负责。

## 6. 测试替换清单

重点修改：

- `message/history.test.tsx`
- `message/applyStreamEvent.test.tsx`
- `components/TaskPlanPanel.test.tsx`
- `hooks/useAgentChat.test.tsx`
- `components/ToolCallLine` 和 UI Tool Registry 相关测试
- 标题轮询测试删除，增加 `session_title` Reducer 测试

使用 DeepAgent 的两个真实 Fixture：

- `docs/guides/fixtures/conversation-history-v1.json`
- `docs/guides/fixtures/conversation-sse-v1.ndjson`

至少覆盖：

- History、Delta、Upsert 和刷新后一致。
- 用户正文、Context、普通附件和图片。
- Assistant Thinking + Text。
- 单工具、多工具、失败、拒绝、中断和 Removed。
- Plan Pending、Interrupted、Completed 隐藏规则。
- Deferred 刷新后仍能确认/取消并跨 Run 恢复。
- 断流恢复与正式停止。
- 标题 SSE，无轮询。
- Smart Fill 的 Range 冲突和覆盖规则不受影响。

## 7. 验收标准

- 仓库不再包含运行时 `context_checkpoint`、History Part 解包或前端工具配对。
- `/history` 与 `conversation_item_upsert` 共用同一 Item Store。
- 页面刷新前后消息、工具、Plan、Deferred、附件和 Context 完全一致。
- 历史 Pending UI Tool 根据服务端状态恢复可操作性，不靠 `restored` 猜测。
- 已完成 Plan 不显示 Composer 面板，但历史 Item 未丢失。
- 标题不再轮询。
- 普通助手不请求 Event API，也不解析 Compaction/Title Event Payload。
- DeepTable 的 Context、Smart Fill、附件和业务卡片行为保持原样。
