# 前端上下文引用与自动压缩生命周期

## 目录

- [结构化上下文引用](#结构化上下文引用)
- [快照构造与发送条件](#快照构造与发送条件)
- [实时与历史恢复](#实时与历史恢复)
- [自动上下文压缩](#自动上下文压缩)
- [状态清理与测试](#状态清理与测试)

## 结构化上下文引用

DeepAgent 0.3.6+ 将用户正文和页面上下文分开传递：

```json
{
  "message": "用户原始问题",
  "context_refs": [{
    "key": "deepcube.editor",
    "type": "deepcube.editor_context",
    "schema_version": "1",
    "label": "DeepCube 元素信息",
    "payload": {},
    "metadata": {"source": "element-python3"}
  }]
}
```

公共字段边界：

- `key` 是同类引用的稳定实例标识；DeepCube 编辑器固定为 `deepcube.editor`。
- `type` 与 `schema_version` 决定模型如何解释 `payload`；DeepCube 当前使用 `deepcube.editor_context@1`。
- `label` 只用于 UI 展示，可包含元素名称，不应成为模型必须依赖的事实。
- `payload` 进入模型输入，保存 Cube、数据加载范围、运行参数、变量摘要和当前脚本等业务事实。
- `metadata` 持久化供历史/UI 识别来源，不进入模型，不承载身份、权限或业务规则。
- `message` 始终是纯用户正文；不要再拼 `<reference>`、`[@context]`、`<user>` 或其它文本信封。

## 快照构造与发送条件

先规范化快照，再用稳定 key 排序序列化生成 signature：

- 删除空对象、无效项和纯展示字段，避免同一业务上下文因对象顺序变化而重复发送。
- Cube 只保留元素和模型定位所需字段；范围只保留 `dimension/scope`；参数和变量只保留必要摘要。
- 当前脚本设置明确长度上限；发生截断时在 `payload` 语义内可辨认，不靠 `metadata` 告诉模型。
- 不把完整维度树、全量成员、token、用户 UUID、Cookie 或权限信息放入上下文。

发送 `context_refs` 的条件：

1. 新会话首轮。
2. 当前规范化快照 signature 与上次实际发送值不同。
3. 从历史列表恢复会话后的第一次新消息；恢复时清空上次发送 signature 或设置 `forceContext`。
4. 用户或页面显式要求刷新上下文。

普通连续追问不重复相同快照。自动压缩成功后也不因此重发；只有页面快照本身变化或会话刚恢复时才发送。

## 实时与历史恢复

- 只在当前用户消息实际附带 `context_refs` 时展示 `@context 元素信息` Chip；不要按“页面当前有上下文”给每条消息都加标签。
- 会话标题只基于纯 `message`，不得包含序列化引用。
- History 从 `items[kind=user_message].context_refs` 恢复 Chip 和展示详情；不要从正文反向解析新协议。
- 旧 XML 历史若产品决定不兼容，按普通文本展示，不继续维护信封剥离逻辑。
- 历史重新打开后，下一次续聊发送当前页面的新快照；历史中的旧引用只用于恢复当时 UI，不自动覆盖当前编辑器状态。
- 编辑、重试或分支接口若支持 `branch_from`，以当前 OpenAPI 为准；不要同时发送 `branch_from` 和 `deferred_tool_results`。

## 自动上下文压缩

DeepAgent 0.6.0 的自动压缩由两部分组成：

1. SSE `context_compaction_progress`：非持久过程事件，常见字段为 `phase`、`message`、`elapsed_ms`、`attempt`；对未知 phase 保持向后兼容。
2. `final.context.compaction`：本轮最终遥测，可能包含 `triggered`、`estimated_tokens_before/after`、`reduction_ratio`、`summary_applied`、`summary_failed`、`target_met`、`strategy`、`compaction_event_saved`。

前端状态机：

- `context_compaction_progress` 使用独立 UI state，锚定在当前 Assistant Run 之前；不写入 Conversation Store、正文或 segments。
- 首个事件延迟约 150ms 展示，避免瞬时压缩闪烁；后续事件更新同一行和后端 `message`。
- thinking、工具或正文开始后仍可保留过程行，直到 `final` 收口。
- `compaction_event_saved=true` 时将本轮状态收口为“已自动压缩上下文”，继续锚定当前页面中的直播 Assistant；普通客户端没有持久 Conversation Item SSE 替换流程。
- `compaction_event_saved` 不是 true、策略为 `sliding_window_fallback`、摘要失败或仅临时裁剪时，只显示约 2.5 秒的轻量状态后移除，不弹错误 Toast。
- 打开或刷新 History 时不恢复压缩标记；锚点消失且找不到同 Run Assistant 时直接隐藏，不能退化为列表底部标记。
- 用户原本位于底部自动滚动区时才随状态行滚动；用户正在回看历史时不要抢滚动位置。

当前 DeepCube 助手只消费自动压缩协议。除非用户明确要求主动压缩交互，否则不调用 `/sessions/{id}/context-status` 或 `/compact`，不增加按钮。

压缩只改变后端后续模型输入投影，不能折叠、隐藏或删除用户可见历史。`/debug-ui` 展示运行态和直播完成态时复用生产组件，只注入静态样例，不模拟历史 Checkpoint。

## 状态清理与测试

以下情况清理压缩状态、150ms 显示计时器和 2.5s 移除计时器：

- `cancelled`、`error` 或请求异常。
- 新建会话、切换会话、停止生成。
- 组件卸载。

目标测试至少覆盖：

- `context_compaction_progress` 不污染 Conversation Store、正文和 segments，同一过程行随 phase 更新。
- 150ms 延迟、`compaction_event_saved=true` 的直播收口、未保存/临时 fallback 的短暂收口。
- `final` 收口后标记仍锚定本页同 Run Assistant；找不到锚点时不追加到底部。
- 取消、错误、切换会话和卸载能清理状态与计时器。
- 刷新历史后不显示压缩标记，原始消息数量与内容不丢失。
