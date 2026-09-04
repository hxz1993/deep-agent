# 企微消息链路与三表设计复核

核验日期：2026-09-04；产品 0.7.0；数据库 002。

## 结论

当前范围保留三张 Channel 表：配置、会话映射、入站消息台账。不是 SDK 或框架规定“三张表”，
也不应把 Channel 台账解释成第二套 Agent Run/聊天记录。现有 `chat_sessions/chat_session_events`
继续负责会话和唯一正文事实源。

## 当前网关并未完成多 Adapter 抽象

`ChannelRuntime` 直接创建官方企微 `WSClient`、解析企微帧、调用 `reply_stream/download_file`；
Account Schema 只接受 `channel_type=wecom`。当前是“一个进程管理多个企微 Bot”，
不是“一个通用网关注册多个平台 Adapter”。代码目录叫 `channel_gateway` 不代表此能力已经完成。

原先讨论的多 Adapter 结构仍需把平台连接/消息转换/回复能力移到 WeCom Adapter，
Gateway 通过统一接口管理 Adapter；会话映射、消息台账和 DeepAgent Client 作为公共业务层。
这属于尚未完成的结构边界，本次核查没有把它静默改成一套新的插件框架。

- 只要求接消息、调用 Agent、返回文字时，两张表加内存去重也能做。
- 要求重启后仍记住已经处理过的 msgid，并避免重复执行有副作用的工具，就需要持久去重记录。
  当前第三张表承担这个职责，保留合理。
- 三表足够支撑当前单 Gateway、被动回复范围，不等于“可靠消息队列”或“端到端恰好一次”。
  HTTP 已发出但结果未知时，不自动重跑；最终回复丢失时不自动补发；内存中尚未认领的排队消息
  在进程崩溃后不能由本系统恢复。是否重投由上游决定。
- 多副本抢占、可靠主动通知、跨进程恢复队列等是新的产品需求，不能靠多加几个时间戳解决。
  这些需求可能需要 Lease/共享队列/Outbox，但现在不预建。

## 已精简字段

- Account：`owner_user_id` 改为 `session_user_id`。控制台标签“会话用户”，管理员直接输入平台 ID。
  不再把“配置管理权限”与“会话归属用户”混在同一字段里；配置由企业管理员管理，聊天仍按原规则隔离。
- Binding：保留 `peer_type/peer_id`。私聊为 `userid` + `from.userid`；群聊为 `chatid` + 群 ID。
  UI/说明中读作 User ID / Chat ID，企微回调自身 `chattype=single/group` 不改变。
- Binding：删除 `generation/last_active_at`。普通消息不更新映射；更换 Session 才更新 `updated_at`。
- Run：只保留 `created_at/finished_at`。删除 `received_at/started_at/updated_at`；原 `completed_at`
  改为 `finished_at`，因为失败和中断也会结束。
- Run：取消 queued 过渡写入。成功消息实测为 1 INSERT + 3 UPDATE，不按 Token 写库。

## 简单示例

以下 ID 均为说明用假值，不是真实账户或凭据。

管理员已创建一个渠道：`id=c1, agent_id=a1, session_user_id=platform-u1, enabled=true`。
企微用户 `zhangsan` 私聊发送“你好”，消息 ID 为 `m1`。

| 步骤 | 执行动作 | 数据库变化 |
| --- | --- | --- |
| 1 | 官方 SDK 收到回调，解析 chattype/from.userid/msgid | 此时不改渠道配置 |
| 2 | 按 c1 + m1 检查去重，按 c1 + userid + zhangsan 找 Binding | 首次没有记录 |
| 3 | 创建 DeepAgent Session s1 和映射 b1 | `chat_sessions` INSERT：agent=a1、user=platform-u1；Binding INSERT：c1/userid/zhangsan→s1 |
| 4 | 认领 m1，生成企微回复流 ID w1 | Run INSERT：id=r1、message=m1、binding=b1、session=s1、stream=w1、received/pending、created_at |
| 5 | SDK 回复处理中；如有媒体则下载解密并上传 FileStore | 普通文本没有附件写入；媒体正文不进 Channel 表 |
| 6 | 即将调用 `/api/v1/chat/stream` | Run UPDATE #1：running/streaming，先提交再发 HTTP |
| 7 | API 校验内部 Token/Account；按配置读取会话用户，恢复 s1 历史并启动 Agent | 原 Event Store 保存本轮用户输入、模型/工具消息等；不是 Channel Run 保存正文 |
| 8 | 收到 session SSE，得知 Agent Run ID ar1 | Run UPDATE #2：agent_run_id=ar1 |
| 9 | Agent 输出“你好！”，Gateway 节流调用 SDK 更新 w1 | 文字只累计在内存和现有 Event Store；Channel Run 不随片段更新 |
| 10 | 最终正文已发出 | Run UPDATE #3：completed/sent、finished_at |

同一个人再发新消息 m2：Account 不变，Binding 不变，继续使用 s1；只增加一条新消息台账，
并由原 Event Store 增加新一轮历史。s1 的原有历史会传给 Agent。

同一个 m1 被重新投递：直接命中既有台账，不创建 Session、不调用 Agent、不重复写台账。

群 g1 中不同人发言：都使用 `c1 + chatid + g1`，共享一个 Session；`sender_id` 仍记录当条发送者。

管理员把 a1 改为 a2，或把会话用户改为 platform-u2：下一条新消息创建 s2，仅更新 b1 的 session_id
和 updated_at；s1 及旧正文不改。旧 Run 保留 s1，这是 Run 必须保存 session_id、不能只读 Binding 的原因。

## 两张运行表的记录生命周期

映射表一行代表“某渠道中的某个外部对话”，台账一行代表“某条入站消息”。不是每次发言新增一个
映射，也不是每次状态变化新增一行日志。一条 Binding 可关联多条 Run；Binding 换绑 Session 后，
旧 Run 的 session_id 不会跟着改写。

下表使用虚构的同一个渠道 C、企微用户 U、消息 M1/M2/M3。

| 发生的事情 | 映射表 | 消息台账 |
| --- | --- | --- |
| 只配置、启用机器人或测试连接 | 无记录 | 无记录 |
| U 首次发送 M1，轮到它执行 | 新建 B1：C + userid + U → S1 | 新建 R1：M1、B1、S1，received/pending |
| M1 开始请求 Agent、取得 Run ID、完成外发 | B1 不变 | 始终修改 R1：running/streaming → 记录 Agent Run ID → completed/sent |
| U 发送不同消息 M2 | 复用 B1，仍指向 S1；不 UPDATE | 新建 R2：M2、B1、S1；R1 不变 |
| 上游重复投递已完成的 M1 | 不变 | 查到 R1 后跳过；不新建 R3、不调用 Agent |
| U 正在处理 M2 时又发送 M3 | 不变 | M3 先在内存等待同 Peer 锁，尚未落台账；开始执行才 INSERT |
| 管理员修改 Agent/会话用户 | 保存配置时 B1 不变 | 所有旧 Run 不变 |
| 修改后 U 发送下一条新消息 | B1 改为指向新 S2，B1 的 id 保留 | 新 Run 指向 S2；R1/R2 仍指向旧 S1 |
| 在控制台删除当前 S2 | 外键把 B1.session_id 置空，B1 不删除 | 历史 Run 仍保留原 ID；Run.session_id 不设会话外键 |
| U 再发新消息 | 同一 B1 重新指向新 S3 | 新建一条对应 S3 的 Run |
| 停用机器人、断线后恢复、正常重启 | B1 保留 | 已结束 Run 保留，不重新执行 |
| Gateway 启动时发现未结束 Run | B1 不变 | 批量将 received/running 改为 interrupted/failed，记录 finished_at |

重启后只有明确未越过 Agent 调用边界的记录可在**上游再次投递时**复用原 Run 并把 attempt_count 加一；
已越过边界的未知结果不自动重跑。当前没有后台任务扫描并重放消息，也没有 Run 的自动过期清理，
因此终态台账会持续保留、按消息数量增长。已有 Binding/Run 的渠道通过管理 API 删除会返回409，
不提供级联删除这些历史数据的产品入口。

## 核查发现的状态边界（尚未修正）

用隔离临时 SQLite 和合成 SDK/SSE 回调核验了以下现状，不触碰本地验收会话：

- Agent 的 `final.status=failed` 仍会走通用收尾，只要发送成功就记 `completed/sent`。
- `final.status=pending` 发出“控制台继续”提示后也记 `completed/sent`；这里只能理解为渠道转交结束，
  不能理解成 Agent 工作已完成，后续控制台续跑不会自动更新这条渠道 Run。
- Agent 已完成但最后企微外发失败时，两列同时记 `failed/failed`，没有保留“处理完成、仅投递失败”的差别。
- 同 Peer 等待中的消息尚未落库；进程退出后本系统无法从 Run 表恢复这部分等待消息。

因此第三张表确实已用于去重，但还不能称为完整、准确的 Agent 生命周期日志或可靠投递队列。
这些问题应修正状态语义和调用边界，不能靠增加一张表或改数据库版本号来掩盖。

## 三张表仍保留什么

| 表 | 字段分组 | 职责 |
| --- | --- | --- |
| Account | id/name/channel_type/transport、bot_id/secret、agent_id/session_user_id、enabled/settings、created_at/updated_at | 控制台配置；Secret 不回显，空输入保留 |
| Binding | id/account_id、peer_type/peer_id/peer_display_name、session_id、created_at/updated_at | 外部标识到当前 Session 的指针，不复制 Agent 或聊天历史 |
| Run | id/account_id/binding_id、external_message_id/external_request_id/sender_id、session_id/agent_run_id/stream_id、payload_hash、processing_status/delivery_status/attempt_count、error_code/error_message、created_at/finished_at | 去重、关联与脱敏结果诊断，不保存消息正文和媒体地址 |

`processing_status` 和 `delivery_status` 是处理结果与外发结果，不是两个 Agent 执行器。
`attempt_count` 只在明确的调用前失败、上游再次投递时增加。没有定时自动重跑任务。
保留调用前的 running 写入，是为了界定未知结果；不能用“没收到 run_id”代替这个界定。

## 与上游的对照

### OpenClaw

本地核验 `openclaw/openclaw@79f0df31c8eb`。其设计同样区分稳定路由键与当前 Session ID；
本地 `src/state/openclaw-agent-db-session-nodes-migration.ts` 的 `session_nodes` 保存
`session_key/current_session_id`。因此“需要某种持久映射”的判断有实际源码依据，但不等于应复制
OpenClaw 的全部表结构。

当前官方文档说明运行时 Session 和 Transcript 存于每个 Agent 的 SQLite；sessions.json 是旧迁移来源。
入站去重文档描述的是按 agent、channel/peer/account/thread、message ID 建立的内存缓存，20 分钟或
5000 条上限。因此不能拿它来证明我们的“持久消息台账”是官方必选项，也不能宣称缓存重启后仍在。
来源：[Session 管理](https://docs.openclaw.ai/concepts/session)、
[消息去重](https://docs.openclaw.ai/concepts/messages)。

### LangBot（纠正此前看错项目的对照）

本地只读核验 `langbot-app/LangBot@ab52684a01ac9245957df04ac68c8bda2213dae8`。
本次参考对象是知识库的 `agent-labs/langbot`，不是 LangChain/LangGraph。

**多 Adapter 是真实实现。** `src/langbot/pkg/platform/botmgr.py` 的 `PlatformManager` 持有
`adapter_dict`，按 `bots.adapter` 创建 Adapter，再交由 `RuntimeBot` 统一调用 `run_async/kill`。
新增平台不必把平台协议写进公共消息调度层。这一分层值得借鉴。
来源：[固定版本的 Bot 管理源码](https://github.com/langbot-app/LangBot/blob/ab52684a01ac9245957df04ac68c8bda2213dae8/src/langbot/pkg/platform/botmgr.py#L973)。

**原生会话关联和模型历史主要是内存状态。** `SessionManager._session_index` 的键为
`instance + workspace + placement_generation + bot_uuid + launcher_type + launcher_id`。
`initialize()` 为空；查询命中内存则复用，否则创建新 Session。Conversation 中保存
`messages`，切换 Pipeline/Bot 时另建 Conversation；这条原生路径不会从 monitoring 数据库恢复
模型上下文。外接 Runner 或插件自行保存历史不属于这个默认保障。
来源：[SessionManager](https://github.com/langbot-app/LangBot/blob/ab52684a01ac9245957df04ac68c8bda2213dae8/src/langbot/pkg/provider/session/sessionmgr.py#L268)。

**数据库监控表不等于我们的 Binding/幂等 Run。**

- `bots`：配置表，保存 Adapter 名称、Adapter 配置、启停和 Pipeline。
- `monitoring_sessions`：首次 INSERT，后续消息 UPDATE 活跃时间、消息数和当前 Pipeline；用于监控统计，
  不是随机 DeepAgent Session 的持久映射。
- `monitoring_messages`：入站 INSERT pending，成功 UPDATE success，助手回复另 INSERT；每条记录的 ID
  是内部 `uuid4`，不是上游 msgid 唯一约束。监控写入异常会记录日志并继续执行，不是认领消息的前置门禁。
- 另有 LLM、Tool、Error、Embedding、Feedback 监控表，属于它的可观测体系，不应在已有 Event Store
  的 DeepAgent 中重复照搬。
- `pipeline_run_records` 虽有模型/迁移声明，本次源码搜索未找到实际 INSERT/UPDATE，不能只看表名就认定
  它实现了与我们相同的生命周期。

写入入口为 `pkg/pipeline/monitoring_helper.py:58/79/125/178`，
实际 SQL 位于 `pkg/api/http/service/monitoring.py:426/447/639`。

**去重是平台相关的内存逻辑。** 企微智能机器人 `wecombot` 的 `_msg_id_map` 最多4096项，按上游
msgid 判重，disconnect 时清空；HTTP Bot 的幂等缓存为600秒、最多4096项。它们不是跨重启的数据库台账。
企微的底层连接是 LangBot 仓库自带 `libs/wecom_ai_bot_api/ws_client.py`，并非我们使用的官方 Python SDK；
这里只参考 Adapter 分层，不复制其 WebSocket、心跳或加密实现。

因此最终取舍是：LangBot 证明多 Adapter 分层值得参考，但不证明“行业必须建三张表”。
我们的 Binding 来自重启后复用随机 DeepAgent Session 的要求；Run 来自跨重启保留上游 msgid 去重依据的要求。
当前不需要新增表，先补齐公共 Adapter 边界和已有状态语义，不能因为对方监控表多就跟着扩表。
