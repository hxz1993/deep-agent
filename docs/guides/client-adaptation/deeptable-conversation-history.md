# DeepTable 助手 Conversation/History 改造清单

本文只描述 `element-deep-table` 如何适配 DeepAgent `0.6.0`。文档保存在 DeepAgent 项目，
不授权修改、提交或推送 DeepTable 仓库。

## 数据边界

- 当前 Run：消费 `thinking/text/tool_call/tool_result/tool_call_removed/final` 等实时 SSE。
- 重新打开、刷新或断流恢复：读取 `GET /api/v1/sessions/{session_id}/history`。
- 标题：消费 `session_title`，不轮询。
- 自动压缩：只在当前 Run 展示 `context_compaction_progress`，History 不恢复标记。
- 普通聊天不请求 Event API，也不解析 Event Payload。

实时状态与 History 允许不同。实时页面可以展示思考、工具执行和压缩进度；History 只展示后端从
Event Store 投影的最终 `ConversationItem[]`。客户端无需维护直播草稿覆盖为持久化 Item 的状态机。

## History 表达

- `user_message`：`text/attachments/context_refs`。
- `assistant_message`：合并后的 `thinking/text/phase/model/provider/usage`。
- `tool`：服务端按 `call_id` 合并的 Call/Result；Deferred 使用
  `mode=deferred + status=awaiting_input`。
- `plan`：`write_plan`完整快照，Composer 只选择当前 Turn 最新项。

## 文件影响

### `src/components/aiAssistant/types.ts`

- 增加 `ConversationHistoryResponse` 和四类 Conversation Item。
- 删除旧 `HistoryMessage/HistoryPart/context_checkpoint/model_message_count/branchPoint`。
- 保留实时 Delta、`session_title`、`final/cancelled/error` 类型。

### `src/components/aiAssistant/client/sessions.ts`

- 保留 `/history` URL、鉴权 Header 和 `agent_id`。
- 返回 `ConversationHistoryResponse`。
- 删除标题轮询；列表、删除、取消和手动改名 API 不变。

### `src/components/aiAssistant/message/history.ts`

- 建议重命名为 `conversation.ts`。
- 只负责 Schema 校验、Item 排序、视觉分组、最新 Plan 和可交互 Deferred 选择。
- 删除 Message Part 解包、Assistant 合并、Checkpoint、Tool Call/Result 配对和 Pending 推导。

### `useChatHistorySync.ts` / `useStreamRecovery.ts`

- History 全量替换持久化会话视图。
- 断流后重新读取 `/history`。
- 删除 Checkpoint 和运行时压缩 Marker 回填。

### `useStreamRunControl.ts` / `applyStreamEvent.ts`

- 当前 Run 继续使用实时 Delta 和 `final`。
- 不增加持久 Item SSE 替换逻辑。
- 正式取消后继续等待真实 `cancelled`。
- `tool_result.plan`用于当前实时计划，Run 结束后隐藏 Composer 计划。

### `taskPlan.ts` / UI Tool Registry

- 历史计划读取 `kind=plan`，不扫描 History Message。
- 历史 Deferred 读取 `tool.mode/status`，不依赖 `restored`标记。
- 用户确认或取消仍提交原 Call ID。

## 业务能力保留

- Page/Sheet/Range Context Adapter 和稳定签名。
- `rangeId/baselineRevision/writeMask` 冲突检测。
- Smart Fill 预览、覆盖、应用和撤回。
- `config_fill`、业务 UI Tool、附件、图片和建议追问。
- App/Space/Language Header。

## 验收

- 实时 Thinking/Text、工具、Plan、Deferred 不重复。
- `final` 后正文只出现一次。
- 刷新后从 `/history`恢复消息、工具、Plan、附件和 Context。
- Deferred 刷新后仍可继续交互。
- 自动压缩当前 Run 可见，刷新 History 后不显示。
- 标题实时更新且没有轮询。
- DeepTable 的 Range 冲突、Smart Fill 和业务工具行为不受影响。
