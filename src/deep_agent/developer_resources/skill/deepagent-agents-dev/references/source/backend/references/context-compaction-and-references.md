# 上下文压缩与结构化引用

## 事实、投影与模型可见上下文

```text
chat_session_events                         immutable ordered facts
latest completed compaction anchor          derived model-input replacement
anchor + Message Events after cutoff        effective model history
```

压缩期间绝不能改写或删除原始 Message Event。一次压缩尝试对应一个 `compaction`
Event，按实际序号插入，并在原记录上从 `running` 更新为终止状态。只有最新有效的
`completed` Event 才会改变模型可见上下文（Surface）；失败、超时、取消、过期、中断或无实际操作的尝试
都不会替换此前成功的锚点。

当前处理顺序：

```text
assemble effective history from Events
-> estimate budget
-> automatic trigger check (manual skips it)
-> choose the oldest safe complete-message prefix
-> prune only oversized summary-input tool results
-> generate deterministic Tool, Context, and Attachment evidence
-> one DSH-style structured summary with current Agent model
-> validate headings, references, size, target, source hash, and status CAS
-> complete the same Compaction Event
```

保持完整的 Message 边界、Tool Call/Result 配对、当前活动 Turn 和 Deferred Pending 状态不被破坏。

## 会话查询与保存点

`get_history_pair` 在同一数据库快照内取得原始事件对应的消息和模型可见历史。原始消息用于核对文件授权和证据，模型收到最近成功的摘要及其后的消息。两份视图中的可变工具结果应彼此独立。

- 用户输入已保存、且本轮未启用原生结构化输出时，已识别为只读的普通工具在 Run 内存中追加结果并估算预算。结构化输出接受和 Deferred 续跑沿用各自的保存规则。用 SQL 监听器核对这段区间，包括一次成功压缩后的下一次只读调用。单独计量入场、实际压缩、审批、外部写操作和收尾，它们有各自的数据库边界。
- 真实压缩先保存其依赖的消息，提交摘要前再核对来源。如果上一问尚无回复，SDK 可能合并相邻的用户提问。核对正文、顺序、指令和工具事实，保留原始事件截止点；期间若新增消息，拒绝过期摘要。
- 回归用同一提示词、模型配置和固定工具响应。在压缩后分别点停止、断开发送流、正常关闭服务、模拟模型错误和审批续跑。重新打开历史核对已完成工具、已显示正文和待确认项，再追问或确认，断言模型收到原证据且工具只执行一次。硬退出只能恢复到最后一次成功提交。

## 预算、性能与估算器

触发与目标决策、摘要输入检查、Compaction 统计、
摘要生成后的验证、context-status 和 Console 展示均使用同一个 `TokenEstimator`。计算时纳入当前指令、工具 Schema、
上下文引用投影、此前锚点、输出预留和安全余量；不要在
Console 中重复实现公式。

必须保持的性能约束：

- 截断点选择为 O(n)：安全边界只构建一次，可复用请求封装只构建一次，后缀
  Token 总量只计算一次，仅为选中的可行边界渲染摘要提示词。
- 场景构建器增量维护 Token 总量；不要每轮重新计算持续增长的完整历史。
- CPU 密集的准备工作在异步事件循环之外执行。等待 Provider 和准备阶段均须允许心跳
  调度。
- 长测试夹具或历史数据的体量可以很大，但准备阶段不得阻止外部观测到
  `context_compaction_progress`。

## 自动与手动压缩

- 使用所选模型的 `context_window_tokens`；没有有效窗口时，压缩不可用。
- 使用当前生效的 Agent 模型，包括请求覆盖的模型。不要添加辅助摘要模型。
- 默认摘要超时上限约为 300 秒。大约每 10 秒发送一次不持久化的
  `context_compaction_progress`，携带真实阶段（`preparing`、`summarizing`、`validating`、
  `committing` 或 `retry_wait`）和已用时间；不要编造百分比。
- 超时、截断、结构无效或未达到目标时不重试。只有快速返回的 429/502/503/504，才可在
  剩余总预算允许时重试一次。
- 手动压缩保持为专用的会话 SSE 操作，不能用聊天提示词替代。它跳过触发阈值，
  但使用同一套算法和校验。
- 客户端断开或取消时，取消摘要任务并关闭同一个生命周期 Event。

端点：

```text
POST /api/v1/sessions/{session_id}/compact?agent_id=...
body: {"model_id": optional}
```

## 压缩成功条件

仅在以下条件全部满足时，将 Event 标记为完成：

- 固定的八节摘要完整存在、顺序正确、内容非空，且未因长度限制被截断；
- 确定性生成的 Tool/Context/Attachment 证据引用有效的 Event ID 和 Part 偏移；
- 被替换的前缀不包含未闭合的工具或 Deferred 边界，也不包含不受支持的 Part；
- 锚点小于被替换的前缀，且最终历史达到目标；
- 源历史哈希、截断点单调性和 `status=running` 的 CAS 条件仍然满足；
- 序列化安全，且操作未被取消或中断。

持久化失败时，必须将同一个 Event 以失败状态关闭，不能让生命周期记录停留在 `running`。
派生压缩结果存储失败，不得使已经完成的正常回答变成失败回答。

## 工具结果与证据

- 对新增的超大工具结果，优先使用 Harness 的 `ToolOutputLimits + Spill`。
- 对超过 8192 个 Unicode 码点的历史或内联摘要输入，仅在
  摘要副本中保留开头 4096 个码点、固定标记和末尾 1024 个码点。保留原始 Event/Artifact。
- 工具证据采用确定性生成，包含来源 Event/Part、Call ID、工具名称、参数与结果哈希、
  完整性、Artifact 引用、稳定 ID、名称与计数，以及副作用执行结果。
- 证据用于索引和检索定位，不是透明的业务工具缓存，也不是第二张证据表。

## 结构化上下文与附件引用

将原始 `context_refs` 和附件引用持久化到对应的 User Message Event。公开的
`context_refs` 外层结构须保持严格且仅作用于当前请求，业务数据放在 `payload`/`metadata` 中。

- 模型输入使用稳定的 key/type/schema/label 和安全的 payload；排除 metadata。
- 压缩时以确定性方式生成 Context/Attachment 证据，不能让摘要模型改写 ID、
  标签、对象或成员标识、文件名、MIME、哈希或来源位置。
- 较小且安全的 payload 可以进入锚点；较大的 payload 保留稳定字段、哈希和精确的 Event 或文件检索
  路径。绝不能将 JSON 文档截断为不完整片段。
- `read_file` 的授权来自原始 Event，不能来自摘要证据。
- 历史搜索可以匹配中文标签与文件名，并返回 Event/Part 位置；精确内容通过
  已授权的 Event 或文件工具读取。

## SSE、历史与控制台

- `/history` 返回普通 `ConversationItem[]`；Compaction 不进入重新打开的普通聊天。
- `/events` 和 `session_event_appended/updated` 按实际序号提供 Compaction，供 Console 轨迹与审计使用。
- 当前实时 Run 期间，不持久化的 `context_compaction_progress` 可以渲染一个轻量标记，并将其锚定
  到触发压缩的 Run。收到 `final.context.compaction.compaction_event_saved=true` 时，在原位关闭该标记。
- 如果客户端无法将实时标记关联到对应 Run，则隐藏它；绝不能稍后将其追加到底部。
- `context-status` 根据最新完成的 Compaction Event、截断点之后的历史尾部和当前估算器计算。

## 测试矩阵

- 估算器与截断点选择的调用次数呈线性增长，以及大型场景的构建耗时。
- CPU 准备阶段与 Provider 无响应期间的心跳；300 秒超时或取消后的生命周期关闭。
- 八节摘要、源哈希与 CAS、截断点单调性，以及拒绝未知 Part。
- Tool/Context/Attachment 证据，以及精确检索原始 Event/Artifact。
- 8192/8193 个 Unicode 码点的裁剪边界、Emoji 安全性，以及 Spill 成功与失败。
- 多次成功压缩；失败、过期或超时的尝试保留此前的模型可见上下文（Surface）。
- 实时压缩标记始终归属于对应 Run，重新打开的 `/history` 不包含标记。
