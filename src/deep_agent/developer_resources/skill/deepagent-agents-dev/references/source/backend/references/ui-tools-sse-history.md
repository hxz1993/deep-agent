# UI 工具、Deferred、SSE 与历史

## 产品与运行时模型

UI 工具按产品 `ToolPackage` 分组；在运行时，每个可运行的包通过一个 Pydantic AI `ExternalToolset` 暴露。

```text
Agent capabilities contains ui:<package>
  -> load schema-valid package members
  -> validate member schemas
  -> build one ExternalToolset
  -> model calls a UI tool
  -> DeferredToolRequests/Pending
  -> browser submits DeferredToolResults
  -> model resumes
```

当前由代码维护的包 `ui:builtin` 包含 `builtin__confirm` 和 `builtin__choice`。手动包保存在 `ui_tool_packages` 中；手动工具引用 `package_code`。Agent 选择包，不选择单个 UI 工具名。

## Schema 准入校验

在以下环节进行校验：

- 创建和更新 UI 包/工具时。
- Agent 工厂加载已有数据库记录时。
- 任何能够引入外部工具的 MCP/导入路径。

策略：

- 拒绝新的无效 schema，并返回可据此采取行动的 422 详细信息。
- 跳过历史无效工具并提供诊断；不要导致每个 Agent 都崩溃。
- 将空包或成员全部无效的包视为不可运行。
- 继续禁止修改和删除内置包/工具。

`builtin__choice` 必须保留统一的结果结构：

```json
{
  "selected": ["option-value"],
  "custom_input": null
}
```

单选、多选和自定义输入均使用此结构；选择结果与自定义输入不能同时为空。

## 重试与事件过滤

模型可能在发出有效 UI 调用前先生成无效参数：

```text
invalid call
  -> schema/semantic validation
  -> bounded ModelRetry
  -> rejected call hidden from SSE/history
  -> final valid call published once
  -> final.status=pending
```

- 不要依赖重试之间复用 `tool_call_id`。
- 在将已接受的调用映射为可渲染的 SSE 之前，先标记/识别这些调用。
- 保留普通非 UI 工具的流式行为。
- 不要让 Console 补偿服务端泄漏的无效事件。

与普通执行中工具相比，已接受的 UI/Deferred 调用具有更强的持久化边界：

```text
validated UI call
  -> append Assistant Message Event successfully
  -> publish actionable tool_call SSE
  -> final.status=pending
```

如果 Event 持久化失败，不要发布可操作的 UI 卡片。普通工具可以在最终持久化之前继续流式发送执行生命周期，因为它们不是可以由浏览器恢复的检查点。

## 事件事实、Conversation 投影与恢复

Tool Call 和 Result 保留在各自原始、完整的 Pydantic Message Event 中。服务端 Conversation 投影器通过 `call_id` 将两者配对成一个稳定的 Item：

```text
assistant Message ToolCall + tool-results Message ToolResult
  -> tool:<call_id> or plan:<call_id>
```

`/history` 调用服务端投影器。普通客户端渲染当前 Run 的实时 Delta 事件，并在正常完成后保留实时结果；切换、刷新、重连或重新打开时才加载 History Item。只有 Console 轨迹读取原始 Event。

绝不要全局移除所有共享同一个 `tool_call_id` 的 Part。应使用局部序列上下文：

- response/request 位置；
- 工具名；
- 存在时的调用 ID；
- 重试关系；
- 最终接受的调用及 Deferred 结果。

验证以下情况：

1. 先无效后有效，使用不同 ID。
2. 先无效后有效，使用相同 ID。
3. ID 缺失或复用。
4. 混合 ToolReturn/RetryPrompt/普通工具。
5. 重试耗尽，未产生有效 Pending。
6. Pending 期间刷新或重新打开。
7. Deferred 结果恰好恢复预期的调用。
8. 取消/失败不会产生虚假的已完成 UI 结果。
9. 即使没有单独的 `run/terminal` Event，中断/失败的 Assistant Event 也会结束 `/history` 中同一个 Run 内待处理的 Tool/Plan Item。

取消时，对每个已发布但尚无实时结果的调用发送 `tool_call_removed(reason=cancelled)`，让客户端无需推断状态就能关闭临时卡片。持久化的调用仍保留为 Event 事实，并在重新打开的 History 中投影为 `interrupted`。

实时 SSE 必须展示已接受的控件和当前完成状态，且不重复内容。`final` 结束 Run/Usage/上下文状态；刷新后的 History 是持久化 Conversation 的事实来源。仅实时展示的进度和压缩标记不必在重新打开后出现。

### Console跨Run配对

- UI Call属于暂停前Run，Result属于Resume Run；优先使用持久源Event／Part关联，只在同一Turn能唯一确定待完成Deferred调用时跨Run匹配。`tool_call_id`不是全局唯一键，不能合并其它轮次的同ID调用。
- 真实持久ToolCallPart可能没有widget。Console读取原Assistant Event的pending事实识别跨Run候选，不以当前工具配置、名称前缀或展示kind代替执行事实；普通客户端仍直接读已配对的History Item，不复制这套原始Events投影。
- Result或Call事实先到／后到、重复帧、部分返回和同ID同值重放都要覆盖。来源不足时等持久事实；直播行被事实替换需双向唯一匹配，保留选择、顺序、状态和调用计数。
- `deepagent_timing`真实执行计时优先。无计时的跨Run UI等待显示Duration unknown，不把Call到人工返回间隔算成工具执行耗时；普通工具的既有估算规则不一并删除。

## UI工具与原生Output Tool

Output Tool已随0.14实现、0.14.1完善Console配置，默认关闭。UI Tool暂停等待外部/用户数据再恢复；Output Tool校验并持久化最终原生结果，不需要浏览器伪造deferred result。普通澄清JSON不自动变Pending。

开关、当前Run装配、格式预算、SSE output/final与structured_output History、Live身份、安装和012／013迁移，统一读[结构化输出](structured-output.md)。UI续跑按当前配置构造Output Type，不恢复Schema快照，但请求／纠错预算不能重置。失败output尝试不是Run终态；只有accepted结果进入普通History。不得用UI工具的参数重试次数代替原生输出预算，也不将输出调用计作已执行MCP证据。
