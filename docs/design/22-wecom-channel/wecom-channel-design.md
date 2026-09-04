# DeepAgent 渠道网关与企微 Adapter 设计

> 实施版本：DeepAgent 0.7.0；数据库 Alembic `002`；传输：企微 AIBot WebSocket。

## 已实施结论

企微双向聊天作为 **Channel** 接入，不是 MCP、Skill 或 DeepAgent Tool。DeepAgent API 与
Channel Gateway 位于同一代码仓和镜像，但以两个进程运行：API 始终部署，Gateway 仅在需要企微的
环境部署一个副本。公共 Gateway 通过注册表管理 Adapter；当前仅提供 WeCom Adapter，内部直接使用
官方 `wecom-aibot-python-sdk==1.0.2`，不自行实现认证、心跳、重连、媒体解密或流式回复协议。

```text
企微用户
  → 企微 AIBot WebSocket
  → deep_agent.channel_gateway（可选单副本，端口 8010）
       ├─ channels：公共消息、Peer 队列、命令、会话与消息生命周期
       ├─ adapters/wecom：格式转换 → 官方 SDK 原生连接、媒体与回复
       ├─ ChannelStore：配置、会话映射、幂等事务
       └─ 内部 REST/SSE
  → deep_agent.main:app（固定部署，端口 8000）
       └─ Agent / Tool / MCP / Skill / FileStore / Event Store
```

0.7.0 不实现飞书 Adapter、Webhook、多副本 Lease、Outbox、主动消息、卡片 Action、企微身份到平台身份映射。
这些能力不能通过预埋空表或伪高可用扩张首版边界。

## 进程与代码边界

| **模块** | **职责** |
| --- | --- |
| `deep_agent/channel_gateway/runtime.py` | 公共进程宿主、Account 轮询、注册表创建 Adapter、启停和状态代理 |
| `deep_agent/channels/contracts.py`、`registry.py` | 公共消息与 Adapter 接口、按渠道类型选择实现 |
| `deep_agent/channels/service.py`、`commands.py` | 去重认领、同 Peer 队列、快捷命令、停止确认、SSE 文本投影及统一帮助目录 |
| `deep_agent/channels/adapters/wecom/` | 唯一引用企微 SDK 的渠道实现；SDK 事件到公共消息、下载解密、回复和连接状态 |
| `deep_agent/channel_gateway/deepagent_client.py` | 解密后字节上传 FileStore、调用现有 Chat SSE |
| `deep_agent/db/stores/channel.py` | Account、Binding、Run 的事务与约束 |
| `deep_agent/api/v1/channel_accounts.py` | Console 管理接口和 Gateway 状态代理 |
| `deep_agent/channel_gateway/app.py` | Gateway 健康、状态、连接测试内部接口 |

Gateway 不执行 Alembic。启动时数据库不是 `002` 就拒绝运行；API 进程先完成升级。
本版固定一个 API worker 和一个可选 Gateway 副本，取消注册表和 Peer 队列尚不跨进程共享。
每个 Bot 只创建一个 SDK Client；名称、Agent 或会话用户变化只更新业务配置，不重建长连接。
公共层不解释企微原始帧、AES Key 或协议字段；这些内容只通过 Adapter 的不透明句柄传递。
后续飞书只增加对应 Adapter、配置校验/表单和测试，复用公共队列、命令、Binding、Runs 和 DeepAgent Client；
这不等于当前已支持飞书，也不增加插件安装器或独立仓库。
API 与 Gateway 使用内部 Token 双向校验。Account 的 `session_user_id` 由企业管理员在“会话用户”
输入框填写。渠道配置属于管理员管理资源，不按会话用户过滤；普通聊天接口仍保持原用户鉴权。
Gateway 通过内部 Token 和 Account ID 调用 API，API 从数据库读取会话用户，不信任请求体里的用户。
平台 Token/Cookie 只能从 Gateway 私有环境变量注入；修改会话用户不等于签发该用户的平台凭据。

## 会话和身份

每个渠道账号绑定一个 DeepAgent Agent 和一个平台会话用户：

```text
私聊键 = account_id + single + from.userid
群聊键 = account_id + group + chatid
```

普通消息轮到执行时为该键生成随机 DeepAgent `session_id`，后续消息通过 Binding 复用；企微不会向 DeepAgent
补发完整历史，模型上下文只由 `chat_sessions + chat_session_events` 恢复。所有这些 Session 的
`user_id` 都是 Account 的 `session_user_id`。企微 `peer_type` 沿用原生会话类型 `single/group`，
不是另造的私聊/群聊枚举；展示写作 User ID / Chat ID。企微的原始 `chattype=single/group` 不改写。
企微 Peer ID 只用于外部会话映射，不冒充平台 User ID。

Account 改绑 Agent 后，下一条消息将 Binding 当前 Session 的 Agent 与 Account 当前 Agent 比较；
不一致就创建新 Session 并更新 Binding；切换会话用户同理。旧 Session 和历史不删除、不转移归属。控制台删除
Session 后，外键将 Binding 的 `session_id` 置空，下一条企微消息自动重建。

渠道 Session 创建时通过 Event Store 保存 `source=channel` 的 `session/title` 事件及渠道/Peer 来源快照。
默认标题为“User｜User ID”“Group｜Chat ID”，收到名称时为“User｜名称（User ID）”或“Group｜名称（Chat ID）”，不调用首轮模型标题生成，
不查询通讯录；用户手动改名仍保留。来源来自该 Session 自己的事件，不依赖当前 Binding，因此 `/new`
之后旧 Session 仍能在列表和详情中看到来源。普通会话标题策略不变。

## 三张表

### `channel_accounts`

保存渠道名称、类型、Bot 配置、Agent、会话用户、启用状态和扩展 Settings。唯一键为
`channel_type + bot_id`。Secret 按本版产品约定明文入库，但管理 API 只返回 `secret_set`，不返回原值；
编辑请求不带 Secret 或传空字符串时保留原配置。Secret 不得出现在日志、异常、文档或测试夹具，配置
备份因此继续属于敏感文件。

企微渠道不绑定 App、Space 或 Language。Gateway 以管理员配置的会话用户调用 DeepAgent；需要业务范围时由
Agent、主数据元数据或未来统一的 `settings` 扩展协议表达，不增加零散标准字段。

### `channel_session_bindings`

保存 `account + peer_type + peer_id → session_id`，唯一键为
`account_id + peer_type + peer_id`。不重复保存 Agent，Agent 切换由 Session 事实判断。
收到命令也可先建立 `session_id=NULL` 的 Binding；帮助等只读命令不会因此创建空 Session。
`/new` 原子更新同一行的当前 Session 指针，不增加 `is_current`、代数或映射历史表。
删除 `generation` 和 `last_active_at`；普通新消息不无条件更新 Binding。`updated_at` 仅在重新关联 Session
或来源元数据确实变化时更新，活跃时间由既有 `chat_sessions.updated_at` 表达。

`peer_display_name` 字符串列替换为 `peer_metadata` JSON，记录来源会话，而非单条消息。
私聊保存 `user_id`（实际 `from.userid`）和回调包含的 `chat_id`；群聊保存 `chat_id`，不把首个
发送者当作群身份。真实收到名称时才包含 `display_name`，不预填空姓名，不猜测或通过模型提取姓名。
只保存白名单来源字段，不复制原始消息、正文、`msgid/req_id`、回复URL或媒体密钥。
查私聊对象不需要访问Runs；Runs只承担每次消息处理的去重和状态记录，正文仍在Event Store。
来源JSON同时写入Session来源快照，使旧Session在换绑后仍能独立定位外部会话。

### `channel_message_runs`

以 `account_id + external_message_id` 唯一约束完成幂等，并记录 received/queued/running/completed/
failed/interrupted 与 pending/streaming/sent/failed。只保存 ID、状态、Payload Hash 和脱敏错误；不保存
企微正文、媒体 URL、AES Key 或 DeepAgent 回复。只保留 `created_at/finished_at` 两个时间字段。
普通消息与命令共用此台账；命令不创建模型 Run，`agent_run_id` 为空。接收时先认领，再进入 Peer 队列，
队列阶段不创建 Session。Gateway 重启把未完成记录标记为 interrupted；只有明确在 HTTP 调用前失败的消息可再次认领。
即便 `agent_run_id` 为空，已经跨过调用边界的未知结果也不自动重跑。

仅在认领、排队、关联 Session、即将派发、取得 Agent Run ID、处理终态和投递结果等节点写入；
文字增量、工具事件、心跳和 `/status` 轮询不更新原消息台账。处理结束与回复送达分开记账，
例如 `completed/failed` 表示 Agent 已完成但外发失败，不能因为回复失败重跑 Agent。
`/new` 的新 Session、标题来源事件、Binding 切换和命令完成标记在同一事务提交，重复消息不会再次切换。
这是持久入站去重台账，不是 Agent 步骤日志、可重放队列或端到端恰好一次保障；不保存正文意味着
重启后等待消息仍需上游重新投递，已完成回复也不会后台补发。

配置备份复制 `channel_accounts`；Binding、Run 和两张会话事实表只复制空结构。

本轮仍属未发布的渠道需求，字段纠正直接合并回 `002`，不增加新版本。已有本地试验库需先备份，
按新结构校验并保留原有渠道、映射、消息和会话数据；不能只修改版本号冒充迁移。
发布需先停 Gateway，再升级 API，最后启动匹配版本的 Gateway。

三表取舍、上游对照和逐条 SQL 示例见 [消息链路与表设计复核](message-lifecycle-review.md)。

## 消息与附件链路

1. SDK 的通用 `message` 事件接收所有消息，避免 typed event 与 generic event 重复处理；视频也由此薄解析。
2. text 直接作为 Prompt；mixed 保留文本并收集所有媒体。
3. 图片、文件、语音、视频由 SDK 下载并解密，字节直接上传现有 `/api/v1/uploads`，不落临时文件。
4. 文件按真实文件名恢复 MIME；企微 AMR 语音纳入上传白名单。实际模型能否原生理解仍由所选模型
   的 input modalities 决定，普通文件继续遵守 DeepAgent 既有 FileStore/read_file 规则。
5. Gateway 调用 `/api/v1/chat/stream`，只累积 `text`；thinking、工具参数、轨迹不外发。
6. 文本按固定间隔更新同一个企微 `stream_id`，final 发送完整累计正文并结束；pending 返回控制台继续提示。

同一 `account + peer` 在 Gateway 内串行。首版只有一个 Gateway 副本，因此不声称跨实例队列或 HA。

## 快捷命令与控制路径

| **命令** | **语义** |
| --- | --- |
| `/help` | 公共目录生成固定帮助，不调用模型 |
| `/new` | 停止当前任务并取消此前已排队消息，确认停止后创建新 Session；旧历史保留 |
| `/stop` | 停止当前任务并取消此前已排队消息，保留当前 Session |
| `/status` | Agent、默认模型、当前 Session、任务状态和等待消息数 |
| `/whoami` | 发送者 User ID、群聊 Chat ID、配置的会话用户 |

只在已认证渠道入口识别独立纯文本命令，不支持参数；未知命令或参数返回帮助。普通正文、引用或附件
中的命令字样不触发。没有 `/reset`；Console、DeepCube、电子表格和共享聊天接口不增加全局命令拦截。
命令不写成模型对话轮次，帮助、状态、身份查询和空闲停止不创建空 Session 或 Agent Run。

`/stop`、`/new` 走独立控制路径，收到后立即阻止普通队列继续派发，不等待当前 Agent 完成。
先取消命令之前的排队消息，再调用既有取消接口；控制期间新到的普通消息暂存。确认本地任务退出和
API 真实状态空闲后，`/new` 原子换绑并恢复派发；`/stop` 保留原绑定。15 秒无法确认时只返回“仍在停止中”，
不切换 Session，继续阻止派发直到确认退出；超时的 `/new` 不会在后台偷偷新建，需要再次发送。
群内可向机器人发消息的成员均可执行，两种命令影响共享群会话。停止不撤销已完成的外部操作。

## 管理、状态与部署

Console“渠道”页面维护 Account；顶部“网关：运行中 / 未部署 / 不可达”只表达宿主状态，
列表的已关闭、连接中、已连接、重连中、连接冲突、连接失败表达机器人连接。Gateway 未部署时仍可保存配置，
连接测试明确返回 503。“快捷命令”打开只读帮助，直接读取 API 中与 `/help` 相同的目录，不依赖 Gateway 存活。

管理 API：

- `GET/POST /api/v1/channel-accounts`
- `GET/PUT/DELETE /api/v1/channel-accounts/{id}`
- `POST /api/v1/channel-accounts/{id}/test-connection`
- `GET /api/v1/channel-runtime`
- `GET /api/v1/channel-commands`：`{commands:[{command,description}],notes:[string]}`，仅帮助，不执行命令。

Session 控制复用 `POST /api/v1/sessions/{session_id}/cancel`；新增只读
`GET /api/v1/sessions/{session_id}/run-status`，返回 `idle/running/cancel_requested`。
Gateway 调用校验内部 Token、Account 与 Session 归属；按实际任务 Agent/Session 取消，配置改绑不能
取消其他会话。Session 列表/详情增加可选 `channel` 来源快照，普通客户端可忽略。

Gateway 内部 API：

- `GET /health`
- `GET /accounts/status`
- `POST /accounts/{id}/test`

账号产生 Binding 或 Run 后不允许删除，只能停用。未启用账号的连接测试会临时认证后断开，可能顶掉
同 Bot 的其他长连接，因此 UI 必须展示警告。

## 后续演进触发条件

- 需要飞书等新平台时增加 Adapter 及其真实配置，不向公共服务加入平台协议判断。
- 需要多副本时再引入 Account Lease、fencing token 和共享队列。
- 需要主动通知或可靠补发时再引入 Outbox。
- 需要企微用户获得不同平台权限时再设计可信身份绑定和迁移界面。
- 需要公网无状态接入时，再把 HTTP Webhook 实现为第二个 Transport。
- 需要确认/选择等交互时，再按企微可表达能力设计 Action/Deferred 映射。
