# 结构化输出公共协议与持久化设计

> 2026-10-22；配套[完整设计](structured-output-design.md)。本文契约已在 0.14.0 实施，正式使用说明同步至 [SSE 指南](../../guides/sse-protocol.md)。0.13.9 不支持新协议；客户端代码与实际业务采用仍由用户另行安排。

## 1. 协议原则

1. 开关关闭保持当前文字、工具、Pending、Usage 和 History；不注入 output 事件、output_info 或快照空值。
2. 开启后的结构化接受结果是 JSON 值，不能使用 Python `str(dict/list)`，不能包装成 Markdown 后让客户端再次解析。
3. 普通工具、Deferred、输出尝试和 Run 终态分别表达，不把“格式失败一次”当成运行失败，不把 Pending 当成成果完成。
4. `final` 仍是运行完成通知；结构化结果靠已提交的接受事实确认。外部连接断开不改写数据库已经接受的结果。
5. `name`、调用 ID、尝试身份来自冻结配置和 SDK 真实调用，不根据 JSON 键或现有 Agent 名猜测。
6. 没有对用户展示的协议/Schema 版本号。内部仍遵守 Event Store schema_version 和包格式版本，两者不是输出业务版本。
7. 客户端应忽略认识事件中的未知扩展字段；未知事件/History kind 不可作为文本自动应用。开启此功能的客户端必须支持下述新类型。

## 2. 身份、顺序与关联

| **字段** | **含义 / 约束** |
| --- | --- |
| `session_id` | 现有会话身份，所有读写检查现有所有者与 Agent 归属 |
| `turn_id` | 一次用户消息及其 Deferred 续跑链；新用户消息创建新 Turn |
| `run_id` | 本次实际执行；Deferred 恢复可创建新的 Run，仍继承 Turn |
| `name` | 当时冻结的输出编码，例如 lease_result，不跟随 Agent 后来的改名 |
| `tool_call_id` | SDK 输出调用 ID；只是关联字段，不能单独作为跨 Run 主键 |
| `attempt` | 同 Turn 的完整输出调用尝试序号，从 1 递增；partial 不增加 |
| `output_id` | 一次输出尝试的持久化事件 ID，稳定并可用于去重；失败和成功是不同 ID |
| `sequence` | 现有 Session Event 顺序；不按时间戳猜顺序 |
| `source` | 原始响应事件/消息 ordinal/part index 的定位信息；只读引用，不能由客户端提交改写 |
| `schema_digest` | 服务端规范化 Schema 指纹，校验缓存/快照一致性，不作为用户业务版本 |

尝试序号与纠错机会不完全相同：同一被拒响应含两个输出调用，可以形成两个失败调用事实，但只消耗一次“要求模型重做该响应”的机会。预算必须按拒绝响应去重，不能通过调用数或 SSE 帧数推断。

平台envelope的turn_id/run_id/output_id不等于业务JSON内部同名字段：BOM的turn_id、submission_id、schema_version、revision/hash和租赁的patch/问题/复核版本均按原业务保留，不重命名或覆盖。平台去重不替代业务幂等；平台不新增虚构版本也不删除业务真实schema_version。详见[三方设计确认](client-design-alignment-20261022.md)。

## 3. HTTP `/api/v1/chat`

### 3.1 关闭

沿用现有 ChatResponse，不新增空字段；`output` 的正文字符串、Pending 的 deferred 字段和既有状态保持。

### 3.2 开启且成功

以下为现有响应相关字段加拟新增 `output_info` 的完整语义示例，既有 Usage/context 字段按实际请求填写：

```json
{
  "session_id": "session-demo",
  "conversation_id": "deepagent:agent-demo:session-demo",
  "run_id": "run-final",
  "status": "completed",
  "output": {
    "text": "已提取合同，请确认出租方。",
    "contract": {"contract_name": "示例租赁合同"}
  },
  "output_info": {
    "name": "lease_result",
    "output_id": "output-accepted-2",
    "tool_call_id": "call-final-2",
    "attempt": 2,
    "turn_id": "turn-demo"
  },
  "usage": {}
}
```

`output_info` 只在接受结构化结果时存在。根数组、字符串、false、0、null 均按 Schema 原样放在 `output`；不得用 `if output` 决定是否有结果。

Pending 仍是现有 `status: pending`、`output: null`、`deferred`，不包含接受结果的 `output_info`。普通端点的失败继续既有 HTTP 错误模式，不用 200 加一个伪完成结果掩盖异常。

## 4. SSE `/api/v1/chat/stream`

### 4.1 新增 `output` 事件

每个已经完成处理并提交结果事实的完整输出调用，有一次结果通知；仅收齐参数、尚未校验/提交的调用不保证有结果通知，也不能伪造失败或成功。半截候选不作为接受结果。

| **字段** | **类型** | **说明** |
| --- | --- | --- |
| `type` | `"output"` | SSE 数据类型；线上 SSE envelope 沿用现有编码 |
| `session_id/turn_id/run_id` | string | 当前身份 |
| `output_id` | string | 语义事件 ID |
| `sequence` | integer | 已提交事件顺序 |
| `name` | string | 英文输出编码 |
| `tool_call_id` | string | 真实 SDK 输出调用 ID |
| `attempt` | integer >= 1 | 完整调用计数 |
| `status` | `"error"` 或 `"completed"` | 调用尝试状态，不是 Run 状态 |
| `accepted` | boolean | 只在 completed 时为 true |
| `output` | JSON 值，可省略 | 仅成功时必有；包括 null 也必须保留键 |
| `errors` | array，可省略 | 失败原因，路径与规则，不包含原始敏感值 |

失败示例：

```json
{
  "type": "output",
  "session_id": "session-demo",
  "turn_id": "turn-demo",
  "run_id": "run-final",
  "output_id": "output-rejected-1",
  "sequence": 12,
  "name": "lease_result",
  "tool_call_id": "call-final-1",
  "attempt": 1,
  "status": "error",
  "accepted": false,
  "errors": [{
    "code": "schema_validation_failed",
    "path": "/contract",
    "keyword": "required",
    "message": "缺少必须字段 contract_name"
  }]
}
```

失败帧不携带候选业务 JSON，普通客户端无需展示失败草稿；Console 轨迹通过授权 Event API 查看原始调用。JSON 无法解析时仅保存原始 SDK 参数字符串为候选事实，不能把它当 JSON 值继续传播。

成功示例：

```json
{
  "type": "output",
  "session_id": "session-demo",
  "turn_id": "turn-demo",
  "run_id": "run-final",
  "output_id": "output-accepted-2",
  "sequence": 16,
  "name": "lease_result",
  "tool_call_id": "call-final-2",
  "attempt": 2,
  "status": "completed",
  "accepted": true,
  "output": {"contract": {"contract_name": "示例租赁合同"}}
}
```

### 4.2 `final` 扩展

保留已有 `type/status/output/usage/context/conversation_id/run_id/run_metrics` 语义；只有结构化成功增加 `output_info`，字段与 HTTP 一致。

```json
{
  "type": "final",
  "status": "completed",
  "conversation_id": "deepagent:agent-demo:session-demo",
  "run_id": "run-final",
  "output": {"contract": {"contract_name": "示例租赁合同"}},
  "output_info": {
    "name": "lease_result",
    "output_id": "output-accepted-2",
    "tool_call_id": "call-final-2",
    "attempt": 2,
    "turn_id": "turn-demo"
  },
  "usage": {}
}
```

成功 output 事件和 final.output 是**同一个结果的两种通知**，客户端以 output_id 关联，不渲染两份、不应用两次 patch。只使用 final 的客户端也能读取完整原生 JSON；需要进度/轨迹的客户端可以订阅 output。

不得把仅结构合法但还未保存的候选发为 completed output。SDK `FinalResultEvent`、OutputToolResultEvent 与产品已持久化的 final 不是同一个承诺层级，适配层必须延迟产品成功通知。

### 4.3 输出调用与普通工具事件

当前 SDK 的 `OutputToolCallEvent/OutputToolResultEvent` 应显式映射，不能只注册 FunctionTool 事件。

- 输出调用不发普通 tool_call/tool_result 卡片，不进入 Deferred。
- 原始参数和重试仍保存在 SDK 消息事实，输出语义事件提供稳定分类。
- 失败不能经普通 `tool_call_removed(reason=model_retry)` 路径删除；普通工具自己的原有移除行为继续保留。
- 自定义输出编码与其他工具冲突应阻断装配，不能仅按同名工具猜测事件类型。

### 4.4 事件序列

```text
正常：text/thinking/普通工具过程 → output(completed, accepted=true) → final(completed)
修正：output(error, accepted=false) → 后续模型请求 → output(completed) → final(completed)
耗尽：output(error) → ... → 最终一次失败 → error(运行终态)
UI：普通工具过程 → final(pending,deferred) → 用户回传 → 新Run过程 → output(completed) → final(completed)
取消：过程/已提交失败尝试 → cancelled；不存在未提交的成功output
```

先前失败尝试不发运行终态 error。只有最终失败按现有执行失败规则记一次 execution/error；不把每个 required 错误都写进“最近异常”。

纯文本却没有输出调用时，框架在预算内要求输出，不伪造 tool_call_id 或输出调用事件。原始文本/RetryPrompt 和纠错预算必须保留；最终有效输出从真实首次调用开始编号。

### 4.5 中间正文

开启时模型仍可能有过程 text/thinking，这些不构成被接受的业务结果。Console 可保留过程区，最终回复区域只按接受 JSON 展示。业务客户端不得从中间文本提取围栏并提前应用结构。

已经发出的过程文本不能自动撤回，本次协议也不承诺撤回。所有业务动作以 accepted/output_info 和自身业务校验为准。

## 5. 只读 Live 与断线

现有 `/sessions/{id}/live` 的 `observation` 包装原样携带新 output/final 数据，不另造一套 Live 输出协议。

- `observation_sync/baseline_sequence`、回放缓冲和 `observation_end/reset` 沿用现有语义。
- 回放、POST 源流、History 对同 output_id 只能形成一条展示项。
- 不因刷新/重连重新调用模型、重做查询或重新应用业务修正。
- 只读观察者离开不取消源运行；显式停止沿用现有取消 API。
- 缓冲超限不拼接残缺 JSON，按 reset/history 恢复。
- 接受结果已提交、final 未收到：History 能恢复 completed，同一个 ID 与值。
- 原始调用已提交、接受事实未提交：只能显示未完成尝试；不能凭合法参数自行认定完成。
- 不新增“收到final就立即二次拉history”的全局行为；沿用现有原页面和观察页的不同收口策略。

## 6. Event Store：一份事实源

### 6.1 Turn 输出快照

在本 Turn 首条已经要保存的真实用户 `ModelRequest` 的应用 metadata 中新增专用键：

```json
{
  "deepagent_output_contract": {
    "enabled": true,
    "name": "lease_result",
    "description": "本轮按需组合结果。",
    "json_schema": {
      "type": "object",
      "properties": {"text": {"type": "string"}},
      "required": [],
      "additionalProperties": false
    },
    "schema_digest": "服务端计算的规范化Schema摘要"
  }
}
```

- 这是应用 metadata，不作为用户可提交的配置，也不把完整 metadata 追加进用户提示正文。
- 快照写入前验证，之后不可变；关闭 Turn 不加此键。原始用户事件无快照且后续消息无任何当时启用证据的旧 Turn，按旧普通模式处理；不能把有启用证据但快照丢失也解释成关闭。
- 配置变化不更新旧快照，History Schema 页展示当时定义，不查询当前 Agent 覆盖历史。
- Deferred 在 Session 锁内先解析原始来源和 Turn，再按原始用户事件取快照、装配输出。不能在 API 早期构造 Runner 时就按最新无效配置拒绝旧 Pending。
- 开启 Turn 的后续真实模型请求/响应 metadata 增加 `deepagent_output_contract_ref: {event_id, schema_digest}`，其中 event_id 指向首条用户快照事件。Deferred 来源响应必须保留此引用；引用查无、指纹不符或快照损坏时恢复失败，不能当旧模式继续。关闭 Turn 不添加此引用。
- 不依赖内存缓存；压缩后的模型 history 会删 metadata，必须从原始 Event Store 按 Turn 读取。
- Schema 指纹由规范化 JSON 计算并复核，不能接受客户端提供的任意指纹。
- 输出纠错使用次数应随真实模型响应/重试的应用 metadata 保存并在恢复时校准；同一响应多 Part 不能重复计费，不能只凭进程内变量在恢复后清零。

### 6.2 不可变 `output/attempt` 语义事件

复用 `chat_session_events`，不新建输出历史表。event_type 为 `output/attempt`，payload 示例：

```json
{
  "name": "lease_result",
  "tool_call_id": "call-final-2",
  "attempt": 2,
  "accepted": true,
  "schema_digest": "冻结Schema摘要",
  "source": {
    "event_ids": ["source-response-part-id"],
    "message_ordinal": 4,
    "part_index": 0
  },
  "output": {"contract": {"contract_name": "示例租赁合同"}}
}
```

行级 session_id/turn_id/run_id、sequence 和时间使用现有事件字段；payload 不再次保存所有通用行字段。`output/attempt` 的行级 status 成功为 `completed`、失败为 `failed`；公共 output SSE 对应映射为 `completed/error`。失败 payload 使用 `accepted:false/errors`，不重复复制完整候选；原始 ModelResponse 是候选来源。只有来源调用但尚未处理出结果时，不伪造一条 failed 语义事件。

成功语义事实保存“系统实际接受的 JSON”，原始 SDK ToolCall 保存“模型原来交了什么”；两者同属 Event Store 的不同事实，不建立第二套会话数据库，也不靠前端本地缓存保存接受结果。

### 6.3 写入与幂等

1. 捕获完整原始 SDK 响应；当前 message_persistence 会过滤未被普通工具接受的调用，必须给输出及整批拒绝增加专用保留规则。
2. 混调检查拒绝前也需保证可保存真实完整响应和输出调用证据；不能抛错后只留下一个无来源语义事件。
3. 失败尝试经归属/来源检查后追加；重试时仍把合法 SDK RetryPrompt/acknowledgement 与调用正确配对，避免下一请求出现孤立调用。
4. 成功接受在完整校验通过，且 SDK output call/acknowledgement、重试预算 checkpoint 和必要会话终态都已可靠保存后追加。优先在同一会话事务保存这些最终事实与 accepted JSON；若现有分阶段 flush 不可合并，必须先验证前置事实已提交，再把接受事件作为最后一次成功提交。不能先在 output Hook 写 accepted，之后才尝试保存 SDK ack。
5. 事务中验证来源 Session/Turn/Run、工具编码、tool_call_id 和 Part 位置一致，复用 Session 锁及 next_sequence 分配。
6. output_id 使用 `session_id + run_id + source message ordinal + part index` 的确定性身份生成。相同事实重复 flush 返回同一事件；相同身份内容不同返回冲突，不能后写覆盖。
7. 同一调用重复回调、partial 完成通知和进程重连不增加 attempt 或重复 accepted。语义 ID 不仅依赖调用 ID。
8. 一 Turn 只接受一个最终输出；多个同响应调用先拒绝。多个 Run 在现有会话执行锁下不能同时接受两个结果。
9. 数据库提交确认丢失时，按确定性 ID 回读核对；结果未知不能自动重跑输出业务或发第二份成功。
10. 专用 `append_output_attempt` 使用普通不可变事件写入路径；不要借用目前只允许 compaction 的 `append_lifecycle`。
11. 接受提交之后的日志、metrics、连接发送或非必要清理错误不能反转已完成结果，不能随后向同一轮写相矛盾的 execution/error/interrupted。清理失败按独立诊断处理，重连以已提交接受事实为准。

### 6.4 被拒批次的未分发标记

保留混调的完整原始响应，不代表其中的普通/UI调用已经执行。分发前检查在本次真实响应的应用 metadata 记录 `deepagent_dispatch: {rejected:true, reason, not_dispatched_part_indices:[...]}`，在任何持久化 flush 之前冻结此标记，不事后修改原始消息。

- 对整批拒绝，列出本批所有未分发调用的原始Part索引，包括普通/UI/native标识；不能写一个伪造的成功ToolReturn。
- SDK纠错所需的真实RetryPrompt/未执行ack负责闭合协议；它们不构成工具已执行的事实。
- 这些未分发的普通/MCP/UI调用**不投影为普通History工具项**，不出现已执行/awaiting_input卡；原始被拒批次仅由Event API/轨迹查看并明确“未执行”，不能成为查询或原生审批证据。
- 业务工具执行次数、Pending数量、投递和费用统计按真实派发/Provider事实判断，不能仅见ToolCall就累加。
- 供应商托管native已发生行为不能通过本地not_dispatched标记抹掉，应区别为“宿主未分发、远端行为另有证据”。

### 6.5 统一纠错预算检查点

本Turn输出修正上限为2。所有输出纠错统一由一个控制器记账，SDK和自定义Hook不能各自拥有2次额度。以 `run_id + source_message_ordinal` 作为拒绝响应身份，去重记录：

```json
{
  "deepagent_output_budget": {
    "correction_limit": 2,
    "rejected_response_keys": ["run-a:response-1"],
    "rejected_responses": 1,
    "remaining_corrections": 1,
    "last_reason": "schema_validation_failed"
  }
}
```

- 一次响应的混调、多个output、JSON/Schema不合法或纯文本缺输出只计一次拒绝；同一事件重放/多次Hook不能重复扣减。
- `remaining_corrections=max(0,correction_limit-rejected_responses)`；第三次非法已无修正机会，终止。记录可含最后这次拒绝，不能因为已耗尽就丢掉最后失败事实。
- 成功输出、正常中间工具、Deferred停点不消耗纠错；数据库/校验器/网络系统故障不伪装格式拒绝。
- 在即将重试时，把检查点放入本次真实 SDK RetryPrompt 所在 ModelRequest 的应用 metadata，与对应源响应和失败尝试一同可靠保存后才允许下一次模型请求/停点返回。
- 若SDK在耗尽时不再生成RetryPrompt，最后的检查点写入现有 execution/error 事实的专用白名单payload，和最终失败一起保存；不新增正常Run表，也不为预算伪造ModelRequest。
- 恢复在原始Event Store中校准检查点，验证单调性、响应身份和已拒绝来源；向SDK传入剩余输出重试数，自定义校验仅消费同一份额度。剩余0不阻止合法输出本身，但任何下一次输出错误均不得再纠正；正常中间工具仍受总请求预算约束。
- SDK参数解析错误通过对应output-error Hook汇入控制器；自定义ModelRetry不能再额外触发另一层独立重试。控制器必须覆盖纯文本拒绝而非只扫描output/attempt。
- 两次原始拒绝最多保留两个可用于修正的机会，最后终止拒绝另记；身份列表受固定预算约束，不无限增长。总请求上限仍优先约束实际请求。

### 6.6 模型历史与普通历史分开

`output/attempt` **不加入 `MESSAGE_EVENT_TYPES`**，不由 SDK message decoder 解码，不算一次模型响应或预算请求。

- 原始 ModelMessage/RetryPrompt/acknowledgement 负责模型后续上下文及安全切分。
- 快照和语义接受事实负责 UI、审计、重连和普通客户端 History。
- SDK 重放需要闭合 output call/ack，不能为方便 UI 删除原调用或伪造普通工具已执行。
- 压缩可以改变后续模型看到的摘要，不改原始事件、不删接受结果和快照。
- Conversation projector 显式消费新语义事实，其他模型历史读取继续忽略它。
- 诊断 `REAL_EVENT_TYPES` 白名单、事件计数、导出/备份和可见范围同步检查，不能产生新事件却查不出来。

## 7. `/history` 普通客户端投影

保持现有响应外壳及 `schema_version:1`；只在有新输出事实的会话返回新增 `kind:"structured_output"`。关闭和历史普通会话不出现新 kind。

```json
{
  "schema_version": 1,
  "session_id": "session-demo",
  "event_head_sequence": 16,
  "items": [{
    "item_id": "output-accepted-2",
    "kind": "structured_output",
    "status": "completed",
    "turn_id": "turn-demo",
    "run_id": "run-final",
    "order": {"sequence":16,"part_offset":0},
    "source_event_ids": ["source-response-part-id", "output-accepted-2"],
    "created_at": "2026-10-22T09:00:00Z",
    "updated_at": "2026-10-22T09:00:00Z",
    "name": "lease_result",
    "tool_call_id": "call-final-2",
    "attempt": 2,
    "output": {"contract": {"contract_name": "示例租赁合同"}}
  }]
}
```

`order` 的具体字段遵循现有 ConversationOrder；实现时复用公共类型，不为输出发明另一套排序。

身份规范强制 `structured_output.item_id === 对应持久化output/attempt.event_id === SSE output.output_id === final.output_info.output_id`，Live同一结果也使用该ID。不需要重复新增History.output_id键，但不能让客户端从例子猜映射或使用不同ID重新生成项。

- 只把 accepted=true 的完整值投影为正式输出项，失败候选不进普通回复。
- 成功输出和原始 output ToolCall 不各显示一张卡。
- 同响应伴随过程文字不提升为另一个成功最终结果；按 process 展示，防止一份业务内容重复出现。
- 无 accepted 的调用不能由当前 Schema 重新校验后补认成功。
- 旧工具名称恰好叫 lease_result，没有当时快照/语义事件也仍是旧普通工具。
- 原始事实中的删除/取消/失败状态遵循现有会话规则；不根据当前 Agent 开关改变旧项类型。
- 使用全原始事实投影，不依赖 compaction cutoff 之后的模型输入视图。

## 8. `/events` 与轨迹详情

Event API 返回现有原始 ModelMessage 和新输出语义事实。Console 把关联调用与输出结果合并成一个可查看行：

| **轨迹部分** | **展示** |
| --- | --- |
| 类型标签 | 输出；成功与失败都是同类型 |
| 标题 | 当时 name 英文编码 |
| 状态 | Error / Completed；不可拿已修正覆盖掉原失败状态 |
| Summary | 尝试序号、真实状态、耗时（有来源才显示）、错误路径；不加虚构版本 |
| 输出 | 完整 JSON / 按结构分组，接受值或该失败调用原始候选；失败明显标记不可作为正式结果 |
| Schema | 冻结快照定义；不显示编辑入口 |
| 原始记录 | 关联 source 事件与语义事件；受现有权限控制 |

校验失败详情与输出详情属于同一逻辑，不单建“校验”标签或业务修正表单。耗时未知显示“—”，不能沿用原型固定 1.6s。

## 9. 完成、取消、错误与故障切面

| **时点** | **事实和对外行为** |
| --- | --- |
| 参数仍分片 | 只有直播过程，不校验为最终，不 accepted |
| 完整调用已收齐、校验失败 | 可提交失败尝试；预算内重试 |
| 合法参数、accepted 事务未提交 | 还没有完成；取消/DB失败不能发成功 |
| 原调用已提交、attempt 失败 | History 不显示最终；轨迹保留真实未完成调用 |
| accepted 提交成功、SSE 发送失败 | 数据库已完成，重开按同 output_id 恢复 |
| accepted 提交成功后收到取消 | 不撤销已完成结果；不再写一个相矛盾的 interrupted |
| 失败修正后成功 | 失败尝试保留，Run completed，诊断不新增执行异常 |
| 修正耗尽/校验器故障/Provider失败 | 一次真实运行终态错误，未接受候选不作为回复 |
| 回读发现来源或摘要不一致 | 明确完整性错误，不按最新 Agent 重算并覆盖 |

错误码初始集合：`schema_validation_failed`、`invalid_json`、`mixed_output_calls`、`multiple_output_calls`、`output_too_large`、`output_config_invalid`、`output_retry_exhausted`、`output_validation_timeout`、`output_persistence_failed`。模型纠错错误和系统故障必须区分；系统错误不反复要求模型改内容。

## 10. 客户端最小接入流程

1. 保留原 POST SSE 和 Pending UI 交互入口，不新增模型运行接口。
2. 忽略未接受的 output 候选；final.completed 带 output_info 时，直接读取原生 output。
3. 以 output_id 去重展示；业务修正仍按自己的 revision/hash/patch ID 做幂等，不能把 UI 去重等同业务授权。
4. 刷新/重新打开按 History 新 kind 恢复同一份结果；不解析原始 Event Store payload。
5. 根对象可分区显示，根数组/标量直接显示；null/false/0/空值不丢失。
6. 无 output_info 的关闭路径保持原文字解析；不要仅凭 typeof output === object 判断所有结果种类。
7. 外部业务校验失败可走现有有界纠错新轮次，不能篡改已接受输出或把客户端保存失败伪装成后端格式失败。
8. 未适配客户端应继续使用关闭 Agent；不能静默把新对象塞进旧字符串字段。
9. 平台同Turn格式修正未结束时不另开业务repair；格式预算耗尽不自动用新客户端轮次重置预算。只有accepted后的业务基准/引用错误才可按客户端有界规则修正，网络/下载/业务保存失败停在真实环节。
10. 首版不提供结构字段partial；租赁已确认采用过程进度＋accepted后完整展示，不能声称保留原字段级逐段预览。BOM和租赁的普通final问题数据不自动变成Deferred。
11. 原生JSON路径与旧围栏历史分别解码后进入同一业务校验，不生成假围栏；同会话旧/新项按各自类型重放。省略业务产物不自动清空旧基线，accepted也不等于客户端已采用或用户已批准保存。

## 11. 不变项与实施检查

- Session 所有者、跨用户诊断管理员、App/Space/模型授权、取消和删除语义保持。
- 关闭时 final/context/usage/工具展示/普通 History 的 JSON 字段集合保持，不添加 null 占位的新字段。
- 新 output/attempt 不重复计入提问、业务工具次数、Token、模型请求及“最近异常”。
- 会话标题仍根据原首轮安全投影，不拿完整合同 JSON 或 Schema 作为额外标题请求输入。
- 模型压缩安全切分需要识别 output call/ack 配对，Schema 指纹进入请求上下文一致性；用户 History 不受模型摘要裁剪影响。
- 原始响应与接受事件权限相同；文本诊断日志仅白名单元数据，不输出 Schema 或业务正文。
- 此协议落实后再同步正式 `guides/sse-protocol.md`、Conversation 类型和客户端迁移说明；本设计不能替代发布后的真实接口检查。


## 12. 0.14.3混合输出增补

上述JSON语义继续完整适用；Agent二级allow_text_output:true时另允许普通文字结束，默认false。文本复用现有text/final与assistant_message，不产生output_info或接受事件；结构化字符串仍依真实调用携带output_info。客户端必须完整消费事件流，按output_info/kind判别、output_id去重，不能依据字符串内容或首个text提前终止。

为保证失败候选不在live可见而History不可见，mixed响应文字经完整响应检查／持久化后发布，沿用text事件；JSON/工具混调、多output、非法Schema响应的文字不发布。同响应合法说明+JSON保留说明及唯一结构化结果。策略随Turn冻结，旧快照缺省false，恢复请求／纠错预算不清零；新策略不改变统计、权限或最终接受提交点。渠道文本与JSON仍分流投递。BOM等客户端未显式适配时保持false。
