# DeepAgent 企微 Channel 设计

> 实施版本：DeepAgent 0.7.0；数据库 Alembic `002`；传输：企微 AIBot WebSocket。

## 已实施结论

企微双向聊天作为 **Channel** 接入，不是 MCP、Skill 或 DeepAgent Tool。DeepAgent API 与
Channel Gateway 位于同一代码仓和镜像，但以两个进程运行：API 始终部署，Gateway 仅在需要企微的
环境部署一个副本。Gateway 直接使用官方 `wecom-aibot-python-sdk==1.0.2`，不自行实现认证、心跳、
重连、媒体解密或流式回复协议。

```text
企微用户
  → 企微 AIBot WebSocket
  → deep_agent.channel_gateway（可选单副本，端口 8010）
       ├─ 官方 SDK：连接、认证、重连、媒体下载解密、流式回复
       ├─ ChannelStore：配置、会话映射、幂等与生命周期
       └─ 内部 REST/SSE
  → deep_agent.main:app（固定部署，端口 8000）
       └─ Agent / Tool / MCP / Skill / FileStore / Event Store
```

0.7.0 不实现 Webhook、多副本 Lease、Outbox、主动消息、卡片 Action、企微身份到平台身份映射。
这些能力不能通过预埋空表或伪高可用扩张首版边界。

## 进程与代码边界

| 模块 | 职责 |
| --- | --- |
| `deep_agent/channel_gateway/runtime.py` | 单 Bot 单 Client、配置轮询、同 Peer 串行、消息生命周期、SSE 到企微流 |
| `deep_agent/channel_gateway/messages.py` | text/image/file/voice/video/mixed 与私聊/群聊解析 |
| `deep_agent/channel_gateway/deepagent_client.py` | 解密后字节上传 FileStore、调用现有 Chat SSE |
| `deep_agent/db/stores/channel.py` | Account、Binding、Run 的事务与约束 |
| `deep_agent/api/v1/channel_accounts.py` | Console 管理接口和 Gateway 状态代理 |
| `deep_agent/channel_gateway/app.py` | Gateway 健康、状态、连接测试内部接口 |

Gateway 不执行 Alembic。启动时数据库不是 `002` 就拒绝运行；API 进程先完成升级。
API 与 Gateway 使用内部 Token 双向校验。Account 的 `session_user_id` 由企业管理员在“会话用户”
输入框填写。渠道配置属于管理员管理资源，不按会话用户过滤；普通聊天接口仍保持原用户鉴权。
Gateway 通过内部 Token 和 Account ID 调用 API，API 从数据库读取会话用户，不信任请求体里的用户。
平台 Token/Cookie 只能从 Gateway 私有环境变量注入；修改会话用户不等于签发该用户的平台凭据。

## 会话和身份

每个渠道账号绑定一个 DeepAgent Agent 和一个平台会话用户：

```text
私聊键 = account_id + userid + from.userid
群聊键 = account_id + chatid + chatid的值
```

首次消息为该键生成随机 DeepAgent `session_id`，后续消息通过 Binding 复用；企微不会向 DeepAgent
补发完整历史，模型上下文只由 `chat_sessions + chat_session_events` 恢复。所有这些 Session 的
`user_id` 都是 Account 的 `session_user_id`。`peer_type` 表示上游标识键名 `userid/chatid`，
不是另造的私聊/群聊枚举；展示写作 User ID / Chat ID。企微的原始 `chattype=single/group` 不改写。
企微 Peer ID 只用于外部会话映射，不冒充平台 User ID。

Account 改绑 Agent 后，下一条消息将 Binding 当前 Session 的 Agent 与 Account 当前 Agent 比较；
不一致就创建新 Session 并更新 Binding；切换会话用户同理。旧 Session 和历史不删除、不转移归属。控制台删除
Session 后，外键将 Binding 的 `session_id` 置空，下一条企微消息自动重建。

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
删除 `generation` 和 `last_active_at`；普通新消息不更新 Binding。`updated_at` 仅在重新关联 Session
时更新，活跃时间由既有 `chat_sessions.updated_at` 表达。

### `channel_message_runs`

以 `account_id + external_message_id` 唯一约束完成幂等，并记录 received/running/completed/
failed/interrupted 与 pending/streaming/sent/failed。只保存 ID、状态、Payload Hash 和脱敏错误；不保存
企微正文、媒体 URL、AES Key 或 DeepAgent 回复。只保留 `created_at/finished_at` 两个时间字段。
Gateway 重启把未完成记录标记为 interrupted；只有明确在 HTTP 调用前失败的消息可再次认领。
即便 `agent_run_id` 为空，已经跨过调用边界的未知结果也不自动重跑。

成功路径是一条 INSERT 加三条 UPDATE：调用前标记 running、收到 SSE 后记录 Agent Run ID、最终结束。
初始 stream_id 随 INSERT 保存，不再为 queued 单独更新。文字增量和工具事件不写此表。
这是一张入站消息台账，不是 Agent 步骤日志、可靠任务队列或第二份聊天历史。

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

## 管理、状态与部署

Console“渠道”页面维护 Account，并显示：未部署、运行时不可达、已关闭、连接中、已连接、重连中、
连接冲突、连接失败。Gateway 未部署时仍可保存配置，连接测试明确返回 503。

管理 API：

- `GET/POST /api/v1/channel-accounts`
- `GET/PUT/DELETE /api/v1/channel-accounts/{id}`
- `POST /api/v1/channel-accounts/{id}/test-connection`
- `GET /api/v1/channel-runtime`

Gateway 内部 API：

- `GET /health`
- `GET /accounts/status`
- `POST /accounts/{id}/test`

账号产生 Binding 或 Run 后不允许删除，只能停用。未启用账号的连接测试会临时认证后断开，可能顶掉
同 Bot 的其他长连接，因此 UI 必须展示警告。

## 后续演进触发条件

- 需要多副本时再引入 Account Lease、fencing token 和共享队列。
- 需要主动通知或可靠补发时再引入 Outbox。
- 需要企微用户获得不同平台权限时再设计可信身份绑定和迁移界面。
- 需要公网无状态接入时，再把 HTTP Webhook 实现为第二个 Transport。
- 需要确认/选择等交互时，再按企微可表达能力设计 Action/Deferred 映射。
