# 微信 Channel 与 AstrBot 初步调研

> 日期：2026-09-23。状态：仅调研归档，用户明确暂不开发；不纳入当前版本实施、数据库迁移或发布。
> 原属课题 22，2026-09-26 独立归入课题 32「新渠道接入」。本文区分上游能力、本地观察和候选方案，不能当作已批准的开发计划。

## 结论

1. 个人微信可以作为独立 Channel 研究接入。腾讯已有 OpenClaw 微信插件及协议说明，AstrBot 的 Python 实现与用户本地扫码连续对话，证明接入不局限于 OpenClaw 宿主；不据此承诺任意账号、群聊、无限主动推送或稳定性 SLA。
2. 微信采用客户端向微信服务端发起 HTTPS 长轮询收消息，另发 HTTP 请求回复；不是微信回调我们的公网地址。当前核验的接入没有提供企微式 WebSocket 模式。
3. DeepAgent 已有公共 Gateway、Adapter 注册表及渠道三表。未来原则上可在现有 Gateway 增加微信 Adapter，无需第二套网关；但配置校验、扫码凭证生命周期、投递和恢复逻辑都需适配，不能只新增一个渠道选项。
4. AstrBot 统一的是渠道消息和会话身份，不是 DeepFOS 业务用户和权限。我们的平台会话归属及授权层应继续保留。
5. 本次只保存调研、证据和待决问题，不修改业务代码，不扫码新增账号、不外发消息。

## 微信接入方式与边界

| 问题 | 已知结论 |
| --- | --- |
| 怎么建立机器人连接 | 客户端申请二维码，用户微信扫码确认；成功后取得 Bot Token、Bot ID、服务地址及扫码用户标识。不是手工填写企微 Bot ID/Secret 的相同流程 |
| 与个人微信账号的关系 | 存在扫码授权/绑定关系；机器人标识、扫码用户标识和后续消息发送者是不同概念。不能把 Bot 当成与授权账号完全无关的随意 ID，也不能推断获得个人微信全部好友、通讯录或历史权限 |
| 谁连接谁 | 我们向微信服务端发起 `getupdates`，服务端有消息或等待超时后返回，客户端带返回游标继续下一次请求；不是每隔几分钟才检查一次 |
| 时延和开销 | 空闲时主要是挂起的 HTTP 请求，不是持续忙轮询；AstrBot 本地默认等待 35 秒。网络中断后仍需重试、退避、游标恢复。开销随机器人连接数增长；本次未做长时间稳定性或容量测试，不能与 WebSocket 作无条件等价保证 |
| 主动发消息 | 有独立发送接口，AstrBot 有按会话发送的抽象；但目标用户、上下文 Token、有效期、服务端频控和许可条件仍需真实验证，不承诺任意陌生人或任意时间可发 |
| 媒体 | 有独立上传参数接口及 CDN 链路；上传加密文件后发送媒体引用，收到媒体后下载解密再交给宿主。概念类似企微，但协议、密钥和引用结构不同，不能复用企微 SDK 的媒体对象 |
| 流式 | AstrBot 微信适配器明确 `support_streaming_message=False`。可聚合最终回答或分段发送，并使用正在输入提示；不等于同一气泡逐 Token 更新。协议中的状态字段不能单独证明流式能力已开放 |
| 用户信息 | 收到不透明的 `from_user_id`、接收者、消息标识、内容项及回复上下文等。当前 AstrBot 用发送者 ID 同时填昵称；没有证据表明能据此反查真实姓名、手机号或 DeepFOS 用户 |
| 群聊 | 本地 AstrBot 微信接收转换固定为私聊；不把协议类型中出现群字段当作群聊已经可用 |

普通收发链路：

```text
微信用户 → 微信服务端
               ↑ 我们持续发起长轮询（携带上次游标）
          微信 Adapter → 公共渠道消息 → Agent/历史/工具
               └──── 发送接口 → 微信用户
```

消息识别需区分三个值：发送者 ID 用于路由/隔离，消息 ID 用于去重，`context_token` 用于微信回复上下文；后者不是模型历史，也不是平台登录凭据。不在日志或公开配置响应中暴露 Token、媒体密钥。

上述协议说明主要来自腾讯客户端仓库及文档；该文档自身也说明它不是完整的服务端契约。开放范围、消息保留、凭证失效和发送窗口必须在正式设计时复核。

## 官方 SDK、社区 SDK 与 AstrBot

| 来源 | 定位 | 本次判断 |
| --- | --- | --- |
| 腾讯 `@tencent-weixin/openclaw-weixin` | 官方 TypeScript OpenClaw 渠道插件；配套 CLI 安装和扫码登录 | 可作为协议主参考，不是可直接装进 Python 的通用 SDK，也不是微信全功能 MCP |
| `nightsailer/wechat-clawbot` | 社区 Python iLink SDK/网关 | 非腾讯官方；此前提到该 SDK 时必须区分来源，尚未作为 DeepAgent 依赖选型 |
| AstrBot `weixin_oc` | 自带 Python Adapter 和 `WeixinOCClient` | 使用 `aiohttp` 请求及 `Crypto.Cipher.AES` 处理媒体，未调用上述 Python SDK，也无需额外运行 OpenClaw。不能仅凭接口相同断言复制或改写了官方代码 |

当前核验未找到腾讯官方通用 Python iLink SDK。AstrBot 依赖中的 `wechatpy` 不能作为其个人微信 iLink 使用官方 SDK 的证据；不同微信/企微接入是不同适配器。

## AstrBot 渠道、账号与会话抽象

核验版本：本地 AstrBot **4.28.1**，提交 `95e98b8aed75d56713666eff39e31bafffd95426`。

| 层 | 实现 |
| --- | --- |
| 渠道类型 | `register_platform_adapter` 注册类型、配置模板、表单元数据及流式/主动发送能力，不是数据库渠道定义表 |
| 机器人实例 | `PlatformManager` 按配置创建实例；同类型可有多个实例 ID；同进程异步运行各自的连接或轮询任务 |
| 账号配置 | `data/cmd_config.json` 的 `platform` 数组；不同渠道保存不同凭证和配置。微信还保存账号状态、游标及用户回复上下文 |
| 统一入站 | `AstrBotMessage` 保存机器人、会话、发送者、群、消息组件、原始消息和时间；`AstrMessageEvent` 进入公共队列/处理流水线 |
| 统一出站 | `send_by_session` / `send_message` 按可保存的会话标识发送；实际支持范围仍取决于 Adapter 和渠道服务端 |
| 运行辅助 | 连接状态、错误状态、配置驱动表单、命令/唤醒和权限过滤、媒体处理；不意味着每种渠道具备相同能力 |

统一会话标识 `unified_msg_origin`（UMO）格式：

```text
机器人实例 ID : 消息类型 : 对端会话 ID
wx_assistant:FriendMessage:用户A
qq_assistant:FriendMessage:用户B
```

不同渠道用户默认互不关联，即使是现实中的同一个人。群聊通常按群区分上下文，发送者单独记录；个别适配器的会话隔离选项需分别核对，不能推广成所有渠道共有行为。

### 用户与业务权限的差异

- AstrBot 渠道会话直接归属 UMO；`conversations.user_id` 实际承载该组合标识，不是统一业务用户主键。
- AstrBot 有 WebUI 登录账号及 WebChat 创建者，但微信/企微发送者不自动映射成这个登录账号。
- AstrBot 企微智能机器人从 `from.userid` 取用户，昵称也直接填该值；群会话用 `chatid`。当前这段适配代码没有我们已有的企微成员资料补全链路。
- **纠正讨论中的简化表述**：DeepAgent 当前是每个 Channel Account 由管理员配置一个 `session_user_id`，外部用户/群分别映射 Session，但这些 Session 归属该 Account 的平台会话用户。不是每个外部发送者都已绑定到不同 DeepFOS 用户。成员姓名补全也不等于用户级授权或签发该用户凭据。

### 持久化及控制台查看

| 存储 | 含义与核验范围 |
| --- | --- |
| `conversations` | 模型对话历史，含渠道实例及 UMO；微信连续对话已在本地实际落库 |
| `platform_message_history` | 独立消息历史模型，含发送者和内容；本次本地仅观察到 WebChat 数据，不能宣称它记录了所有微信原始消息 |
| `platform_sessions` | 页面聊天会话/创建者；本次创建链路和数据库观察主要用于 WebChat |
| `umo_aliases` | UMO 的自动名称/用户别名，不是跨渠道账号绑定表 |

控制台 **数据 → 会话**：`http://127.0.0.1:6185/#/data/conversations`，可按机器人筛选渠道历史。
WebChat 左侧聊天列表不会自动混入微信会话。该渠道查看页展示模型对话上下文，不是微信聊天记录的完整同步镜像。

2026-09-23 用户反馈已扫码并完成微信连续对话；本次只读核验 SQLite `data/data_v4.db`，观察到一条微信模型会话，含 5 条用户、9 条助手及 7 条工具记录。数量是取证时快照，助手工具调用记录不等于微信回复气泡数。未把真实用户标识、消息正文、凭据或二维码写入本文；未做全部媒体、重启恢复或主动发送验收。

## 对 DeepAgent 的候选接入方向（未批准实施）

现有三表及公共 Gateway 已有可复用边界：

| 现有部分 | 候选处理与尚缺内容 |
| --- | --- |
| `channel_accounts` | 已有 `channel_type`、`transport`、`bot_id`、`secret`、`settings`、Agent 和会话用户。可以研究 Bot ID/Token 的通用语义映射；但当前创建接口只允许 `wecom/websocket` 且要求非空凭证，扫码前草稿、凭证更新、敏感字段输出需单独设计，不能直接声称无需改结构 |
| `channel_session_bindings` | 可继续按 Account＋Peer 类型＋Peer ID 映射 DeepAgent Session；无需把微信 ID 伪装成平台用户 |
| `channel_message_runs` | 可继续承担入站去重和处理/投递状态；微信消息 ID、游标推进与持久化顺序需专项验证，不能把长轮询等同可靠队列 |
| Gateway / Registry | 同一个 Gateway 加微信 Adapter，复用公共调度、命令和 API Client。当前默认注册表仅注册 WeCom，尚未支持微信 |
| 历史与诊断 | 继续写既有 Session/Event Store，复用普通历史和管理员渠道诊断；不为微信再复制一套模型历史表 |
| 平台身份 | 沿用 Account 配置的会话用户和可信平台凭据边界；将来需要逐个微信用户授权时，另外设计绑定和撤销流程 |

可借鉴 AstrBot 的配置差异化、统一消息组件、UMO 和能力声明；不照搬其明文 JSON 凭证存储、模型历史查看页或本地权限模型。运行游标、回复 Token 是否保存于现有扩展字段或独立运行状态，待恢复需求明确后决定，不在本次预建列/表。

## 恢复课题前需要确认

1. 首版是否仅本人绑定、私聊、文本与附件；谁可使用机器人、以哪个平台身份执行业务。
2. 扫码入口、取消/重扫/解绑、凭证失效、刷新及备份脱敏规则。
3. 主动发送的服务端条件、频控和上下文有效期；不以客户端发送函数存在代替实测。
4. 游标恢复、重复消息、进程退出、已执行但未投递等失败场景；同 Bot 多连接限制需验证。
5. 媒体类型/大小/编码限制及模型支持范围；流式降级采用最终回复还是克制分段。
6. SDK 选型与许可证、维护成本；以腾讯最新协议为准，不能直接把 AI Labs 学习副本作为生产 import 依赖。

以上为待决问题，不是排期。后续只有用户明确恢复课题才进入设计与开发；跨课题状态见 [TODO](../TODO.md)。

## 关键来源与代码入口

- [腾讯官方插件及安装说明](https://github.com/Tencent/openclaw-weixin/blob/main/README.zh_CN.md)
- [腾讯客户端协议说明](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)：接入时重新核验最新字段与服务端限制。
- [社区 Python SDK](https://github.com/nightsailer/wechat-clawbot)：仅候选参考，非官方。
- [AstrBot 固定版本微信 Adapter](https://github.com/AstrBotDevs/AstrBot/tree/95e98b8aed75d56713666eff39e31bafffd95426/astrbot/core/platform/sources/weixin_oc)
- [AstrBot 本地学习索引](/Users/wuzhiruge/PycharmProjects/AI-hub/agent-labs/astrbot_代码索引.md)
- AstrBot 本地源码根：`/Users/wuzhiruge/PycharmProjects/AI-hub/agent-labs/astrbot/`。重点：`astrbot/core/platform/{platform.py,register.py,manager.py,message_session.py}`、`sources/weixin_oc/{weixin_oc_client.py,weixin_oc_adapter.py,weixin_oc_event.py}`（后三者相对 platform 目录）、`astrbot/core/db/po.py`、`astrbot/dashboard/services/conversation_service.py`、`dashboard/src/views/conversation/ConversationWorkspacePage.vue`。
- DeepAgent 当前依据：[渠道设计](../22-wecom-channel/wecom-channel-design.md)、[企微成员资料指南](../../guides/wecom-applications.md)；源码 `src/deep_agent/db/models.py` 的三种 Channel 模型、`schemas/channel.py`、`channels/registry.py`、`channels/gateway/deepagent_client.py`。旧设计中的发布版本和迁移号是历史信息，当前行为以源码及现行指南为准。

本地研究环境：AstrBot 4.28.1 已通过 DevBox 的 AstrBot 服务启动，监听 `127.0.0.1:6185`；用于学习和用户自测，不是 DeepAgent 运行依赖。不在文档保存启动密码或微信凭据。
