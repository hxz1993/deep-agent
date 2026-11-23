# 前端结构化交互协议

本页区分普通工具、Deferred/HITL与既有文字config_fill；原生最终JSON采用[原生输出协议](frontend-structured-output.md)，不能套用Deferred或围栏解析。

## 目录

- [工具调用展示](#工具调用展示)
- [HITL 表单](#hitl-表单)
- [重试与非法参数](#重试与非法参数)
- [config_fill](#config_fill)
- [历史恢复](#历史恢复)

## 工具调用展示

- 保持 SSE 顺序，不把无法确认并行的调用强行合并。
- 工具详情默认收起，展示工具名、状态、必要参数和结果摘要；工具标题直接使用 SSE 返回的名称，不在前端维护业务名称映射。renderer 识别与标题展示分开处理。
- 实时 Delta 按当前Session、轮次及实际调用身份更新草稿；`tool_call_id`可能跨轮复用，不全局合并。Deferred的Call在原Run、Result在Resume Run，沿原待确认身份关联；不能只按结果的当前Run另建一张卡。
- History 直接消费服务端投影的 `kind/status/mode/call_id/input/output/widget/metadata/artifacts`，不在前端重新配对 Tool Call/Result。
- 来源不足或候选冲突时等History校准，不猜测最新同ID就是目标；修复配对优先复用已有身份，不默认增加SSE字段。工具真实计时优先，未知不伪造零或把人工等待算作执行时长。
- 普通工具完成后标 success；error 事件或明确错误结果标 error。
- thinking、工具、正文分别建 segment，实时和历史共用一个归一化器。

## HITL 表单

阻塞式 UI 工具链路：

1. SSE 收到合法 Tool 草稿；后端持久化失败时流以明确错误收口，前端不能伪造持久事实。
2. 实时 Deferred 依据合法 `widget/metadata` 进入待交互；History 中只有 `tool.mode=deferred && status=awaiting_input` 可恢复操作。
3. 前端按 `widget`、`metadata.form_type`、工具名选择 renderer。
4. 用户确认/取消后提交原 `call_id`。
5. 同一 session 续跑，收到 `tool_result` 和新的 `final`。

表单防御：

- `args` 必须是 object；`fields` 先 `Array.isArray`。
- `fields=[]` 不进入 `.map` 崩溃路径；显示准备态或不可提交状态。
- 字段 key 优先级建议为 `field → id → name → dimension → label → field_<index>`。
- 文本类型支持 `text/input/textarea`；select 的 `options` 必须是数组。
- option 展示可兼容 `label/name`、`description`，提交值优先 `code/value/label`。
- 直接输入兜底提交 `custom=true`、`needs_lookup=true`；跳过提交 `skipped=true`。
- pending 未解决时锁定普通输入，避免旧 deferred call 被新消息废弃。
- `metadata` 是后端随事件提供的前端展示提示，不会进入模型工具说明；不能把只写在 metadata.rules 的规则视为模型已收到。

## 重试与非法参数

后端按当前工具JSON Schema及UI语义进行有界纠错；次数以目标运行配置为准。只公开校验通过且实际分发的调用，不把UI调用的预算套用到独立原生输出预算。`deepcube_clarify` 至少要求非空 fields、稳定且唯一的字段 key、合法字段类型，以及 select 的非空有效 options。

前端验收时仍要分开记录：

- 原始 JSON 是否可解析。
- parsed object 是否满足可渲染语义。
- 页面是否能消费字段别名并保留稳定 values key。

前端仍要防御异常输入，但不要替后端猜业务：

- 非 object、空 fields、无 key、select 无 options 时不崩溃。
- 同一实际调用的合法后续事件更新旧状态；跨轮或不同源调用即使ID相同也不覆盖。
- 不吞掉合法 `args_valid=true` 调用，也不把非法中间调用持久化到前端状态。
- 如果实时未显示非法调用但刷新历史出现空占位，按后端持久化协议问题交接；不要只在历史 UI 隐藏证据。

## config_fill

在仍采用文字产物的DeepCube客户端，`config_fill`是最终Markdown代码块，不是UI Tool；它不是BOM/租赁等原生输出的新协议：

````text
```config_fill
{"sections": {...}}
```
````

前端职责：

- 只用标准 `JSON.parse` 解析代码块内容。
- 解析成功后渲染常驻配置表单；用户点击后才写入页面。
- 填充运行参数、数据加载范围和元素信息时保存撤回快照。
- 历史会话重新打开后仍能渲染并允许再次填充。
- 非法 JSON 显示明确错误并保留原文，不替换引号、不删尾逗号、不猜字段。
- 流式尚未闭合代码块时显示轻量准备态，不提前报红色错误。
- `config_fill` 不提交 deferred result，也不宣称已写入页面。

## 历史恢复

- 按 `ConversationHistoryResponse.items` 的 `sequence + part_offset + item_id` 顺序恢复。
- `structured_output`按name/output进入业务校验，item_id=output_id；只读恢复不自动填充、保存或发模型请求。
- `user_message/assistant_message/tool/plan` 归一化成与实时 SSE 相同的页面消息/segment 结构，并复用 renderer。
- `mode=deferred && status=awaiting_input` 的 Tool 用原 `call_id` 恢复可操作表单。
- 被后端拒绝的非法 retry attempt 不应出现在历史；若出现，记录 session ID、parts 和页面占位表现。
- 附件从历史引用恢复预览，不依赖已失效的本地 Blob URL。
- 实时和历史必须使用同一字段归一化、Markdown 和结构化表单组件，避免两套行为漂移。
