# 飞书渠道接入调研：官方 SDK、扫码配置与 OpenClaw / AstrBot 对照

> 2026-09-27 · 调研完成，尚未开始接入开发。本文将官方能力、第三方实现和 DeepAgent 建议分开。
> 核验方式：官方文档、官方 SDK 主分支及 PyPI 发布包静态检查；本地 OpenClaw / AstrBot 固定版本源码审查。没有扫码创建应用、使用真实凭据或执行消息收发。

## 1. 结论

**飞书可以优先采用“企业自建应用机器人＋WebSocket 长连接”，不需要为接收消息开放公网回调地址，也不必等待平台 Web Gateway 的免登录改造。** 我们的服务主动连接飞书，仍然需要可用的出站 HTTPS/WSS、DNS 和证书链。回复消息、下载附件和更新卡片主要走飞书 HTTP API，不是把 Agent 的 SSE 原样送进 WebSocket。[官方事件订阅配置](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)、[官方 Channel 快速开始](https://github.com/larksuite/channel-sdk-python/blob/main/docs/quickstart.md)

扫码与手动配置是**取得应用配置的两种入口**，不是两种消息传输协议：

- **扫码创建/授权应用**：管理员或有相应权限的用户在飞书确认创建或更新应用，服务端获得应用凭据，再启动长连接。不是登录并托管用户个人聊天账号。
- **手动配置**：在飞书开放平台创建企业自建应用、启用机器人，填写 App ID/Secret，再配置事件、权限、可见范围与发布。
- 两种入口最终可以使用同一个 Adapter；应用管理权限、租户政策和发布要求仍然有效，不能理解为任何人扫一下就能把机器人无限分享给任意企业。

**建议产品形态：一个飞书渠道账号绑定一个现有 Agent，提供“扫码创建”和“已有应用”两种入口，默认长连接。** 实施先以手动应用打通协议和权限，再接扫码向导；扫码失败能够回到已有应用配置。流式卡片与附件适配属于渠道层，不改变既有业务客户端的 SSE 协议。

## 2. 先分清四个容易混淆的概念

| **概念** | **实际用途** | **是否适合本项目** |
| --- | --- | --- |
| 企业自建应用的应用机器人 | 私聊、群聊接收消息，调用发消息 API，可加卡片与业务能力 | 是，首选 |
| 群内“自定义机器人”的 Webhook | 我们的服务向飞书生成的 Webhook URL 发送请求，推送群通知；不是飞书向我们推送用户聊天 | 单向通知可用，不能代替本次双向 Agent 渠道 |
| 事件订阅 Webhook | 飞书向我们的公网服务器发送事件/回调 | 可选；会重新遇到公网入口及平台鉴权放行问题 |
| WebSocket 长连接 | 我们主动连飞书，由连接接收已订阅事件 | 企业自建应用首选，无公网入站接口 |

自定义机器人的 Webhook 与事件回调 URL 方向相反，不要在配置页都只写“Webhook”而不解释。通知机器人的行为可参考[飞书官网说明](https://www.feishu.cn/content/7271149634339422210)。

企业自建应用主要服务本租户及允许的使用范围。向其他客户企业分发产品需另研究商店应用、安装授权和多租户隔离；不能把每租户自建应用的方案等同于跨企业一键分发。外部群、跨租户分享是否可用需按具体应用能力和账号验证，首版不承诺。

## 3. 官方 SDK：已有能力比传统裸长连接封装更多

### 3.1 SDK 层次与版本证据

| **SDK / 组件** | **职责** | **本次判断** |
| --- | --- | --- |
| Python `lark-oapi` | 完整 OpenAPI 类型、访问凭据管理、事件分发、基础 WebSocket、回调校验；也公开扫码注册函数 | DeepAgent 可直接使用，适合 API 与扫码 |
| Python `lark-channel-sdk` | 更高层 `FeishuChannel`：消息归一化、连接生命周期、媒体、策略、卡片回调、流式回复 | 优先做适配验证，避免重复开发成熟渠道能力 |
| Node.js `@larksuiteoapi/node-sdk` | 官方 Node 服务端 SDK，包含 API 和长连接 | OpenClaw 仓内飞书实现的基础，不作为 Python 服务的直接依赖 |
| Go / Java 服务端 SDK | 官方 `oapi-sdk-go`、`oapi-sdk-java` | 平台其他服务可用；本项目无须因此增加 Java/Go 转接进程 |

2026-09-27 查询 PyPI：`lark-oapi=1.7.3`、`lark-channel-sdk=1.4.0`。本次在内存中读取 wheel，确认前者公开导出 `register_app`、`aregister_app`，后者包含 `FeishuChannel` 及异步启动/停止方法；**未安装到项目环境**。这不是仅存在于 README 的计划功能。[PyPI lark-oapi](https://pypi.org/project/lark-oapi/)、[PyPI Channel SDK](https://pypi.org/project/lark-channel-sdk/)

官方代码核验快照：

- `larksuite/oapi-sdk-python`，`v2_main`：`0b9e6e48b74bb4b34462fc67b7e738b27e73e697`。
- `larksuite/channel-sdk-python`，`main`：`52ba7968a4a6898f653eb0690ce98b69ddb1354d`。

主分支文档可能领先发布包，实施时固定版本逐项验收，不无条件依赖最新主分支。官方已将高层 Channel 从 `lark_oapi.channel` 拆为独立包，新开发优先用 `from lark_channel import FeishuChannel`；需要完整 API 与扫码时并用 `lark-oapi`。[官方迁移说明](https://github.com/larksuite/channel-sdk-python/blob/main/docs/migration-from-lark-oapi.md)

### 3.2 推荐怎样复用

先验证 `lark-channel-sdk` 能否满足现有 `ChannelAdapter` 的生命周期、入站接纳和回复契约；通过则用于飞书传输与内容适配。若它的自动 ACK、内部去重或队列时序妨碍我们“持久接纳后确认”的要求，则该部分退回基础 `lark-oapi`，而不是重写整个 WebSocket 协议。

官方高层 SDK 默认安全模式为兼容模式，不应把默认配置直接等同于生产策略；本项目须显式确认准入、缓存与连接边界，按锁定版本测试。依赖兼容性也要先验证，不能为接飞书盲目升级整个 Agent 环境。

我们仍保留自己的 Agent、Event Store、平台权限、渠道账号、会话绑定和执行记录。SDK 的消息归一化可复用，SDK 的用户白名单不能替代 DeepFOS 业务权限，SDK 的内存缓存不能替代持久幂等。

基础 `lark.ws.Client.start()` 是阻塞入口，不能直接塞到运行中的 FastAPI asyncio loop。高层 SDK 已有 `connect_until_ready()`、`disconnect()` 等公开生命周期方法；如果使用基础 SDK，必须设计明确的独立运行边界，不照抄调用 SDK 私有 `_connect()` 的实现。[基础客户端](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py)、[Channel API](https://github.com/larksuite/channel-sdk-python/blob/main/docs/reference.md)

## 4. 扫码到底发生了什么

### 4.1 注册应用，不是个人微信式登录

```text
DeepAgent 管理员点击扫码创建
  → 后端发起官方应用注册流程
  → 得到限时确认链接 / 二维码
  → 用户用飞书扫码，确认租户、应用及权限
  → 后端轮询注册状态，取得 App ID / App Secret
  → 保存渠道账号并绑定 Agent / 平台执行身份
  → 启动 WebSocket，检查权限与订阅，测试实际收发
```

官方 `lark-oapi` 的 `register_app/aregister_app` 封装了 device flow，使用 accounts 域名的 `/oauth/v1/app/registration`。返回应用凭据，而不是用户个人 IM session。国内飞书与 Lark 的 accounts/API 域名不同，不能混用。[官方注册说明及源码](https://github.com/larksuite/oapi-sdk-python/blob/0b9e6e48b74bb4b34462fc67b7e738b27e73e697/lark_oapi/scene/registration/__init__.py)

注册可携带应用名称/描述、增量权限、事件和回调配置；当前 SDK 支持创建专用模式及指定已有 App ID 的更新流程。默认模板可能包含不需要的权限；源码还支持 `addons.preset=false` 选择最小模板，实施时仍需真实账号核对最终确认页。不要为了聊天渠道复制办公插件的全量文档、日历和通讯录权限。[官方 SDK README](https://github.com/larksuite/oapi-sdk-python/blob/0b9e6e48b74bb4b34462fc67b7e738b27e73e697/README.md)

配置向导应区分：等待扫码、等待确认、过期/拒绝、凭据已取得、连接已就绪、真实消息测试通过。凭据只在后端保存，前端成功响应返回渠道标识和脱敏信息；关闭向导停止轮询，防止旧二维码结果覆盖新配置。保存失败时说明应用可能已在飞书侧创建，提供手动接管，不能直接再次创建一堆应用。

### 4.2 不要混淆另外两种“授权”

- **使用机器人准入/配对**：哪些飞书用户可以与已接入机器人聊天，是我们/OpenClaw 的访问策略。
- **用户身份 OAuth**：让工具代表用户读写飞书文档、日历等，获得用户访问凭据；与机器人收发所用应用凭据不同。

只做飞书聊天渠道，通常不需要为了每一位聊天用户新增飞书文档 OAuth。若未来要以用户身份操作飞书办公对象，再单独设计权限、撤销与凭据生命周期。

## 5. 手动配置的完整路径

1. 登录飞书开放平台，在目标企业创建**企业自建应用**；添加机器人能力，取得 App ID/Secret。
2. 在 DeepAgent 创建飞书渠道，填写凭据、选择飞书/Lark，绑定 Agent 和平台执行身份；先启动长连接。
3. 在飞书“事件与回调”选择长连接。控制台通常要求先检测到有效连接才能保存相应配置；按实际后台提示执行。
4. 订阅 `im.message.receive_v1`，配置最小消息权限；事件名称与权限 scope 是两回事，不能只开权限却没订阅事件。
5. 设置应用可用范围，按企业流程创建版本并发布/审批。改权限后检查是否需重新发布或重新安装，不能只看本地配置保存成功。
6. 在飞书找到机器人私聊；允许群聊时，把机器人加入获准群，先以 @ 触发测试。
7. 实测一条文本、一张图片、一个文件，再测断连恢复和重复事件；需要流式时另加卡片权限并验收。

建议权限分层（最终以目标租户控制台及 API 所需权限为准）：

| **需求** | **常见 scope / 事件** | **建议** |
| --- | --- | --- |
| 单聊消息 | `im:message.p2p_msg:readonly`；事件 `im.message.receive_v1` | 首版 |
| 群 @ 消息 | `im:message.group_at_msg:readonly`；同一接收事件 | 首版按允许群启用 |
| 发消息 | `im:message:send_as_bot`，相关消息接口权限 | 首版 |
| 图片、文件收发 | `im:resource` 及所调用媒体 API 权限 | 附件阶段 |
| 历史引用回读 | 对应消息读取权限，例如 `im:message:readonly` | 确定需要引用原文时申请 |
| 流式卡片 | `cardkit:card:write`、相关卡片更新权限 | 流式阶段，按锁定 SDK/API 核对 |
| 全群消息、完整通讯录、云文档、日历 | 范围更大的独立权限 | 不因接入聊天默认申请 |

CardKit 权限在不同文档/租户版本中存在拆分描述，不能保证只加一个 scope 覆盖所有创建和更新接口。以实际返回码、开发后台和锁定版本的最小样例确认。[官方 CardKit 示例说明](https://github.com/larksuite/channel-sdk-python/blob/main/docs/cardkit-streaming.md)

## 6. 长连接与公网回调的差异

| **维度** | **WebSocket 长连接** | **HTTP 事件回调** |
| --- | --- | --- |
| 谁主动连接 | DeepAgent → 飞书 | 飞书 → DeepAgent 公网 URL |
| 公网入站 / 企业网关改造 | 不需要开放入站接口 | 需要可达路由，并独立验签/解密 |
| 外网要求 | 出站 HTTPS、WSS、媒体/API 可达 | 入站可达，发送和下载仍需出站 |
| 应用范围 | 官方长连接面向企业自建应用 | 商店应用等按平台支持方式使用 HTTP 订阅 |
| 故障重点 | 心跳、重连、代理、进程生命周期、重复投递 | URL 验证、签名、重试、网关超时、限流 |
| 我们首选 | 是 | 暂不纳入首版 |

SDK 通过 HTTP 请求发现 WebSocket 连接地址，不应把握手返回的 WSS 地址永久写死。只允许 `open.feishu.cn` 的普通 HTTP 请求，也不代表全部连接和附件一定可用；部署联调需要实测 SDK 发现的目标、WebSocket Upgrade 和实际媒体下载。[官方快速开始](https://github.com/larksuite/channel-sdk-python/blob/main/docs/quickstart.md)

官方长连接资料说明有快速处理窗口、连接数限制和集群分发语义（常见文档为 3 秒、单应用最多 50 连接，同一事件分给其中一个连接而非广播）。本次官方门户正文未能由文本抓取完整展开，**这些数字不作为已实测配额或容量设计保证**；实施前在目标账号控制台复核。确定的设计要求是：不能在协议事件处理器里等待 LLM 完整回答；不要认为开多个实例就会每实例各收一份。[官方事件订阅入口](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)

卡片交互应按具体回调类型核对长连接支持，不能把“能发卡片”理解成“所有旧卡片回调都支持长连接”。官方 SDK 已提供新版卡片动作处理，但我们现有 UI 工具不会自动转换为飞书按钮/表单。

## 7. OpenClaw 的两套飞书插件

必须区分两个维护来源，二者渠道名都可能叫 `feishu`，安装方式和配置契约不能混用。

| **实现** | **已确认接入方式** | **可借鉴部分** |
| --- | --- | --- |
| OpenClaw 仓内 `@openclaw/feishu` | 当前源码既可扫码创建，也可手填；默认 WebSocket，HTTP 可选；并非保证每个发行包预装 | 配置向导兜底、owner 准入、群策略、持久入站、会话隔离 |
| 飞书团队 `@larksuite/openclaw-lark` | 官方安装器提供新建/关联已有应用；新建可扫码，已有应用可填 ID/Secret | 注册、权限说明、流式卡片；办公工具与用户授权应独立看待 |

### OpenClaw 仓内源码观察

固定版本 `ffb50bc0479d8a52b6f5b7575632c8726b6cff41`，本地工作树干净：

- 当前入口为 `openclaw channels login --channel feishu`，向导提供扫码及手填，代码默认手填。扫码能力不可用或轮询返回拒绝、过期等状态时可回到手填；不能保证所有网络异常都自动兜底。两种方式最终保存应用凭据并使用长连接。
- 扫码调用官方 accounts 应用注册接口，取得 `open_id` 时向导会限制私聊使用者；普通配置的 DM 默认配对策略与向导生成的 owner allowlist 不同，不能合并描述成一个默认值。
- 群策略与 @ 策略分开；向导放开群时仍明确要求 @。群共享会话、群内按人、按话题、话题＋人是不同模式。
- 消息等部分事件先进入持久队列再处理，支持重启恢复；并非所有事件类型都有相同持久保证。消息重放保护还有内存降级，不能宣称严格 exactly-once。
- 流式卡片、卡片动作的用户/会话/过期校验可参考；文档、Wiki、Drive、Bitable 工具是额外功能，不应随聊天接入全部带入 DeepAgent。

源码定位：`extensions/feishu/src/setup-surface.ts:236`、`app-registration.ts:155`、`monitor.account.ts:555`、`monitor.message-handler.ts:435`、`dedup.ts:42`、`conversation-id.ts:7`、`card-action.ts:307`。路径均相对本地 `agent-labs/openclaw/`，对应[上游固定版本](https://github.com/openclaw/openclaw/tree/ffb50bc0479d8a52b6f5b7575632c8726b6cff41/extensions/feishu)。

### 飞书官方 OpenClaw 插件观察

官方资料使用 `@larksuite/openclaw-lark` 安装器，除聊天外覆盖文档、表格、日历、任务等，并提供用户身份授权。本文只核对官方说明和公开入口，未将其全部运行源码逐项审计；上面 OpenClaw 仓内的持久队列结论不能直接归给它。[飞书官网说明](https://www.feishu.cn/content/article/7613711414611463386)、[飞书官方插件仓库](https://github.com/larksuite/openclaw-lark/blob/main/README.zh.md)

## 8. AstrBot 的实际做法与边界

固定版本 `95e98b8aed75d56713666eff39e31bafffd95426`（4.28.1），本地工作树干净。

- Dashboard 有扫码创建与手动填写。扫码直接 HTTP 调官方应用注册流程，轮询 pending / slow_down / denied / expired 等状态，成功回填 App ID/Secret/domain；还需保存配置并确认连接与消息可用，不是扫码即全部完成。
- 支持 socket 和统一 Webhook，默认 socket。其文档对国际版/企业自部署有不同建议；这不是官方 Lark SDK 一律不支持 WebSocket的证明，具体租户/域名需另测。
- 基于 `lark_oapi`；该版本 socket 启动触及 SDK 私有 `_connect()`，不建议直接复制这层。消息回调调度后台 task，避免同步等待模型，但这本身不保证持久接纳。
- 该 Adapter 的显式 event_id 去重主要在 Webhook 路径，是进程内、约 30 分钟缓存；不能把它当成跨重启、多副本可靠收件箱。
- 已实现 CardKit 流式更新、节流、结束态及创建失败降级。能展示流式卡片，不代表已经实现任意卡片按钮回传和我们的 UI 工具。
- 旧文档消息表标注 v4.15，仍称部分附件不能接收；当前源码已有文件、音频、视频接收分支。本次未做真实媒体互通，不按旧表误判，也不据代码分支承诺全部格式可用。

具体位置（相对 `agent-labs/astrbot/`）：

| **源码** | **观察点** |
| --- | --- |
| `astrbot/core/platform/sources/lark/app_registration.py:105` | 注册 begin/poll、凭据转换及国内/海外分支 |
| `dashboard/src/components/platform/PlatformRegistrationAction.vue` | 二维码、轮询、过期与成功回填 |
| `astrbot/core/platform/sources/lark/lark_adapter.py:67` | SDK 回调调度后台任务 |
| 同文件 `:320`、`:483`、`:749` | 媒体转换、Webhook 内存去重、socket 生命周期 |
| `astrbot/core/platform/sources/lark/lark_event.py:989` | CardKit 流式发送和结束 |
| `tests/test_lark_app_registration.py` | 当前主要是域名/响应解析测试，不是完整扫码 E2E |

参考[固定版本源码](https://github.com/AstrBotDevs/AstrBot/tree/95e98b8aed75d56713666eff39e31bafffd95426/astrbot/core/platform/sources/lark)与[当前接入文档](https://docs.astrbot.app/platform/lark.html)。适配器的 Webhook 安全细节仍需独立审计，不照搬；我们首版采用长连接，不引入这条公网路径。

## 9. DeepAgent 如何接入

### 9.1 复用现有渠道边界

当前核对 DeepAgent HEAD `c3aaa70f208eb85fdeb53fa1c8ac3ce51c10e7a6`；`default_registry()` 只注册 `wecom`，飞书尚未实现。

| **位置 / 能力** | **建议变化** |
| --- | --- |
| `src/deep_agent/channels/registry.py` | 增加飞书 Adapter 显式注册，不引入另一个插件宿主 |
| `channels/contracts.py` | 将飞书消息映射为现有 InboundMessage / Media / Quote；必要时补充线程引用，不改变外部聊天协议 |
| `channels/service.py`、`gateway/runtime.py` | 复用账号生命周期、入站 claim、会话与执行逻辑；核对 SDK 快速 ACK 与持久接纳的顺序 |
| `channels/metadata.py` | 当前元数据白名单偏现有企微/通用字段；新增飞书租户/线程等信息时不能被悄悄丢弃 |
| 渠道账号 API / Console | 配置类型、手动凭据、扫码任务及状态、Agent/身份绑定、连接测试 |
| 媒体与回复 | 官方 SDK 下载/上传＋现有附件设施；卡片状态映射 loading / reply / finish |

数据优先复用账号配置、Peer 绑定、执行记录和 Event Store，不因增加一种 IM 就新建一套模型历史库。是否需要补持久收件字段、线程键或二维码短期状态存储，在实现前验证后决定；**本调研不修改数据库、不承诺完全零迁移**。凭据长期存储遵循[课题 31](../31-credential-encryption/README.md)。

### 9.2 不能漏掉的语义

**接纳与执行分离。** 收到事件后先验证来源上下文、做权限/去重并可靠接纳，迅速让 SDK 完成协议确认；模型、工具和媒体重处理在后台执行。当前 ChannelService 已有持久 claim 和异步 worker，可复用但要审计崩溃窗口。官方高层 SDK 的两层去重默认也不是跨进程原子处理保证，不能重复套一层缓存后就称可靠。[官方去重架构](https://github.com/larksuite/channel-sdk-python/blob/main/docs/dedup-architecture.md)

**身份与会话隔离。** 用渠道账号/应用、租户、chat_id、sender.open_id 和必要的 thread_id 形成稳定上下文，不用显示名当主键。首版建议私聊独立，群仅白名单＋@响应；敏感业务不要默认把所有群成员历史和权限合并。群共享历史还是群内按人隔离必须在产品上确认，回复即使只触发一个人也可能对全群可见。

**附件不用公网临时裸链。** 接收后以机器人应用权限下载进入现有附件链路；输出经飞书上传再发送。不要依赖飞书服务器能访问 DeepFOS 登录态文件 URL。复用文件大小、类型、权限检查；不把音视频“能下载”说成模型一定能理解。

**流式只是展示。** 正文更新到卡片，节流并保持 sequence 单调，最后关闭流式状态；取消和异常也处理终态。思考、内部工具参数不默认发到群。权限不足可降级为最终文本；发送失败只重试投递，不重新执行模型里的写操作。

**交互表单另定。** 飞书原生卡片按钮可以承载部分确认/审批，但需要回执映射、防重复提交、权限与过期校验。当前 Console UI 工具和“同 Run 等用户”课题不是本次普通文本渠道的前置条件，也不会自动获得飞书支持。

## 10. 建议实施顺序与验收

建议批准开发时分三步交付，而不是一次承诺所有办公工具：

1. **最小渠道闭环**：企业自建应用、手填凭据、WebSocket、私聊＋允许群 @、最终文本、平台身份绑定、去重、断线重连。先真实部署证明无公网回调可收发。
2. **完整对话体验**：图片/文件、引用上下文、流式卡片、取消与异常收尾；结合真实业务助手验收。持久接纳与 SDK 生命周期测试不推迟。
3. **扫码配置**：复用官方注册函数，最小权限模板、过期/取消/拒绝、已有应用兜底；真实企业验证后开放。文档/日历用户授权、复杂卡片表单、多租户商店应用另立范围。

这三步是开发拆分，最终配置页仍可同时提供扫码与手填。扫码属于有明确复用能力的产品优化，不必先自研账号登录协议。

验收至少覆盖：

- 不开放任何公网入站端口，服务器仍能接收消息、发送回复和下载附件；限制出站/断开代理时能明确定位连接失败。
- 事件订阅、权限、可见范围或发布缺失时，区分“连接成功但收不到消息”与“凭据无效”。
- 两个用户、两个群、话题回复不串会话；未知用户/群、机器人自身消息不误触发。
- 重复事件、重连补发、持久接纳前后重启不重复执行写操作；多连接分发不当作广播。
- 同一 App 的生产与测试不能随意同时连接，避免消息被另一实例消费；使用独立应用或明确连接归属。
- 大文件、引用不可读、媒体权限失败、卡片限频/撤回/sequence错误、发送失败均有可理解的终态。
- SDK 启停、配置变更和连接测试不遗留线程/task；测试连接不能启动第二个会抢生产消息的消费者。
- 二维码过期/拒绝/关闭、重复点击、保存失败、应用已存在及 Secret 轮换；注册凭据不在日志或前端查询中泄露。
- 普通客户端 SSE/History、现有企微渠道和附件权限回归不变。

## 11. 尚需真实账号验证的事项

- 本企业是否允许扫码注册，模板最终权限、审批与可见范围；不同角色的创建权限。
- 锁定版本高层 SDK 的 ACK/去重/队列与我们持久接纳的衔接；必要时使用基础 SDK。
- 目标环境出站 WSS、API、媒体路径和代理行为；不能由“浏览器能打开飞书”推断服务端可用。
- 具体 CardKit 权限、配额、卡片交互和客户端展示；国际版 Lark / 私有部署域名暂不默认承诺。
- 群内历史采用共享、按人或按话题；平台执行身份是否首版只由管理员绑定。

**最终建议：飞书有条件先于企微自建应用 HTTP 回调落地。** 它不依赖目前卡住的企业公网回调链路，官方 Python 能力也足够复用；真正工作量在渠道身份、消息可靠性、附件和卡片，而不是打通公网。是否启动实施仍由用户确认，本次仅交付调研。
