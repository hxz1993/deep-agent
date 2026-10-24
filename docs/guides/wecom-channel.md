# 渠道网关与企业微信接入

DeepAgent 支持企微智能机器人 WebSocket 长连接。API 与 Gateway 使用同一代码和镜像，但
Gateway 是可选独立进程：不使用渠道的环境无需启动，也不会建立外部连接。公共网关通过 Adapter
管理渠道，企微与[飞书 Adapter](feishu-channel.md)共用同一网关，分别处理各自平台协议。

## 前置条件

- 数据库由 API 升级到当前 Head `011`；010 删除独立发送回执表，并增加会话主表来源类型和来源 ID 两列，011 增加[企微应用 Webhook 渠道](wecom-app-channel.md)凭据并允许其不使用 Bot 凭据；历史空来源按内部读取，详见 [会话来源](session-sources.md)。
- 已安装 `requirements.txt` 中固定的 `wecom-aibot-python-sdk==1.0.2`。
- API 与 Gateway 可以访问同一数据库，Gateway 可以访问 DeepAgent API 和企微公网地址。
- 生产环境生成高熵内部 Token，并只通过 Secret/私有环境变量注入。

## 启动

以下命令从仓库根运行，通过 `PYTHONPATH=src` 加载与部署镜像相同的源码。

API：

```bash
DEEP_AGENT_CHANNEL_GATEWAY_URL=http://channel-gateway:8010 \
DEEP_AGENT_CHANNEL_INTERNAL_TOKEN='<private-token>' \
PYTHONPATH=src uvicorn deep_agent.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Gateway：

```bash
DEEP_AGENT_CHANNEL_API_URL=http://deep-agent:8000 \
DEEP_AGENT_CHANNEL_INTERNAL_TOKEN='<same-private-token>' \
PYTHONPATH=src python -m deep_agent.channels.gateway --host 0.0.0.0 --port 8010
```

Gateway 不运行迁移，由 API 先完成当前代码的迁移。Gateway 不维护固定 Revision 白名单，健康接口仅返回启动时读取的实际 Revision。当前保持一个 API worker、一个 Gateway 副本：
当前取消注册表与消息队列不跨实例共享，相同 Bot 的多条长连接还可能互相顶替。DevBox 服务名为“渠道网关”。

Jenkins 网关启动版（`Jenkinsfile.gateway`）采用同一容器内的两个独立进程：`deep_agent.combined_runtime` 先启动 API，
等待迁移和健康检查完成后再启动只监听回环地址的 Gateway，并自动生成本次容器使用的内部 Token。
它不新增 Deployment 或 Service。不需要网关时使用仅 API 版（`Jenkinsfile.api-only`），显式恢复
Gunicorn 启动命令并清空 API 的网关地址；数据库版本和渠道配置保留。不要用原始归档版切换停用网关。
对应说明见 [`deploy/jenkins/README.md`](../../deploy/jenkins/README.md)；原始脚本只在
[`docs/archive/jenkins/`](../archive/jenkins/README.md) 归档，不是当前发布入口。

稳定本地 SQLite 可用相同监督顺序，同时保留`local_dev`的本地身份和FileStore能力：

```bash
PYTHONPATH=src python3.12 -m deep_agent.combined_runtime \
  --local-config ~/.config/deep-agent/sqlite.env \
  --api-port 8000
```

统一入口运行时不要再单独启动Gateway；两进程分别监听`127.0.0.1:8000/8010`。

可选私有运行配置：

| **环境变量** | **默认值** | **用途** |
| --- | --- | --- |
| `DEEP_AGENT_CHANNEL_GATEWAY_URL` | 空 | API 访问 Gateway；空表示未部署 |
| `DEEP_AGENT_CHANNEL_API_URL` | `http://127.0.0.1:8000` | Gateway 访问 API |
| `DEEP_AGENT_CHANNEL_INTERNAL_TOKEN` | 无 | 双向内部鉴权，API与Gateway必须一致 |
| `DEEP_AGENT_CHANNEL_POLL_INTERVAL` | `3` | 配置轮询秒数 |
| `DEEP_AGENT_CHANNEL_STREAM_INTERVAL` | `0.35` | 企微流式更新最小间隔 |
| `DEEP_AGENT_CHANNEL_TEST_TIMEOUT` | `10` | 连接测试等待秒数 |
| `DEEP_AGENT_CHANNEL_PLATFORM_AUTHORIZATION` | 空 | Gateway调用平台时的私有Authorization |
| `DEEP_AGENT_CHANNEL_PLATFORM_COOKIE` | 空 | Gateway调用平台时的私有Cookie |

Token、Cookie、Bot Secret 不得写入源码、日志、文档或测试夹具。

渠道请求没有浏览器登录态，`session_user_id` 也不签发平台凭据。可以在“关联 DeepFOS 应用”
中保存 App ID 和该 App 对应空间的 API Key（字段显示为 `X-Deepfos-Key`）。Key 应在平台
对应空间的 API Key 设置中创建，并授权当前 App。服务端推导 Space 并带上 Language；调用
继承上下文的 MCP 时使用 `X-Deepfos-Key`，移除 Cookie、Authorization、Token 和 user 等登录头。
平台按 Key 绑定的用户校验业务权限；DeepAgent 内部 Session 归属使用配置的会话用户。
四种渠道开启应用关联后，保存时必须提供 Key，并通过平台 `checkApiKey` 校验有效性、应用范围和所属 `userId`；所属用户必须与 `session_user_id` 一致。编辑保留原 Key 时同样校验，不要求 Key 所属用户是企业管理员。校验失败或平台不可用时不保存；关闭应用关联时不执行此校验。

本地 `local_dev` 会注入私有 Profile 的登录态，因此仅从本地聊天成功不能证明线上 Gateway
已配置凭据。排查时对照 `mcp_request_context_resolved` 日志的 App、Space、Language 和
鉴权载体名称（不记录凭据内容）；HTTP 401/403 是鉴权或权限失败，不应解释为服务宕机。
渠道会话创建时记录应用范围，已有渠道会话在下一次请求采纳新配置时同步，关闭关联会清空。

## Console 配置

在“配置中心 → 渠道 → 新建渠道 → 企微”创建账号，支持两种方式：

- **扫码创建**：点击生成二维码，用手机企业微信扫码，在官方页面确认创建或关联机器人。授权完成后，补齐渠道名称、Agent、会话用户，再手动保存；最后从列表启用渠道。
- **手动关联**：从企业微信智能机器人的 API 模式获取 Bot ID 和 Secret，手动填写后保存。

扫码区域提供「前往企业微信管理后台」外链；授权使用手机企业微信扫码，过期或失败可重新生成。官方扫码接口没有已确认的名称预填参数，因此渠道名称仅用于本地展示，机器人名称在企业微信侧确认。

扫码采用[企业微信官方 CLI 的二维码流程](https://github.com/WecomTeam/wecom-cli/blob/c4b9b6610c7ca2854441bfa336a5b458daeeb707/crates/wecom-cli/src/auth/qrcode.rs)，来源标识为 `deep-agent`。API 服务生成扫码会话并轮询结果，不需要额外公网回调，不依赖安装 CLI。任务及临时凭据仅保存在当前 API 进程，限定发起管理员访问，超时或取消后清理；保存失败可重试，成功后消费。浏览器不接收 Secret。仍要求单 API worker；服务重启后需重新生成二维码。

扫码只替代手填 Bot ID/Secret，不会自动关联企业微信应用、成员查询权限或 DeepFOS 应用，也不修改现有机器人消息与流式回复协议。

“会话用户”直接输入平台 User ID，对应 `session_user_id`；只有企业管理员可以配置。
它决定 DeepAgent Session 的归属，不负责签发平台权限或 Token。渠道可独立关联 DeepFOS App
和空间 API Key，Space 由 App ID 自动推导，不单独配置 Language。
Secret 按当前产品约定明文入库，
但管理接口只返回 `secret_set/secret_masked`，不返回原值；编辑时以首尾字符和中间星号回显，
留空或保留原掩码即保持原配置，实际输入新值才替换。配置备份仍必须按敏感文件保护。

v0.10.1 起，标题同行右侧切换“渠道账户 / 企微应用”。企微应用单独保存企业 ID、企微 Agent ID 与
App Secret，可以供多个机器人共用。渠道编辑器新增“查询用户详情”，默认关闭；开启后必须选择真实
企微应用。关闭时不查询用户资料，允许保留应用引用，不影响原有连接和对话。成员缓存统计、刷新／重置、
身份兼容与完整接口见[企微应用与成员资料](wecom-applications.md)。

扩展设置使用左侧纵向 Tab、右侧配置布局，开关位于详情标题左侧：

- **关联deepfos应用**：默认关闭。开启时填写 App ID 和该空间的 `X-Deepfos-Key`；例如 `iirjgi005` 自动推导 Space `iirjgi`。此配置决定渠道请求的应用上下文。
- **查询用户详情**：独立控制企微成员查询，仍使用企微应用配置。

关闭后隐藏对应输入区域并保留已填配置。应用关联存于
`settings.deepfos` 中的 `enabled/app_id/api_key`；缺省或关闭均为不分应用，不向 MCP 发送配置 Key。
管理 API 只返回 `api_key_set/api_key_masked`。编辑时省略 `api_key` 或传空字符串保留旧值，
`clear_api_key: true` 显式清空；该指令和脱敏回显字段不持久化。Key 随 Bot Secret 一样保存于
私有配置数据库，完整配置备份含凭据，应按敏感文件保护；Agent 安装包与日志不包含 Key。
配置变更从后续请求生效，进行中的请求保持开始时的上下文；历史消息和会话归属不会改写。
服务端使用内部鉴权通过后的渠道配置确定 App/Space，不采信渠道请求中的临时应用覆盖头。
实际平台权限以配置 Key 的空间、App 授权、绑定用户、启停状态和有效期为准。
复用现有 JSON 字段，扫码接入本身无需新增迁移。

渠道是否启用只在列表 Switch 中维护，不放在新建或编辑弹框中。新建渠道默认关闭，管理员确认配置后
再从列表启用。

顶部网关状态与列表机器人状态分开：

- `网关：未部署`：API 没有配置 Gateway URL，保存配置不受影响；部署并配置 URL 后需重启 API。
- `网关：不可达`：已配置 URL，但 Gateway 无响应。
- `网关：运行中`：API 能访问 Gateway，不代表每个机器人都已认证。
- `已关闭`：账号未启用，不建立长连接。
- `连接中 / 已连接 / 重连中 / 连接冲突 / 连接失败`：当前机器人在 Adapter 中的实时状态。

连接测试对启用账号读取当前认证状态；未启用账号会临时连接后断开，可能顶掉同一 Bot 的其他连接。

## 回复体验

普通消息开始处理后，企微 Adapter 通过官方 SDK 发送 `<think></think>`、`finish=false`，
使用与 WecomTeam OpenClaw 插件相同的原生思考占位，由企微客户端渲染加载动画，无需新增配置。
正文沿用现有流式输出，并复用同一个 Stream ID 替换占位；完成、失败或停止时发送非空文本和
`finish=true` 收尾。网关关闭时也会尝试有界收尾；断网或进程被强杀时无法保证客户端立即结束加载。

渠道采用“累加过程预览＋加载”：正文首段到来前保持加载；各轮中间正文以空行分隔累加展示，
再次思考、调用工具或压缩上下文时保留全部累计正文，在下方显示“处理中…”；后续正文替换
等待提示并继续累加。首轮尚无正文时使用原生三个点动画，有正文后的等待提示是文字，不是动画。
当前企微客户端会隐藏末尾`<think></think>`之前的内容，不能把它追加到累计正文后；前置标记
虽能保留正文，也不会同时显示原生三个点。
连续加载事件不重复发占位，正文沿用流式节流；结束时使用
`final.output`全量覆盖同一个气泡，只保留最后一轮答案，即使答案比预览短也不拼接旧内容。
显式空答案使用中性结束提示；只有缺少`output`字段的旧事件允许回退到最后一个正文阶段。

thinking、工具参数与结果不发到企微，加载占位不写入对话历史；真实过程与工具事件仍按原链路
保存。预览投递失败会停止本轮后续预览，但不打断或重跑Agent；最终投递仍尝试一次，并单独记录
成功或失败。停止、失败、等待控制台操作都会用非空提示结束加载。

不添加“回复：原问题”文字前缀，也不模拟可点击的引用条。当前SDK的流式回复接口未提供主动指定
原生引用消息的参数；收到用户引用消息的支持与此不同，见下方“引用消息”。

参考：[WecomTeam OpenClaw 原生思考占位](https://github.com/WecomTeam/wecom-openclaw-plugin/blob/main/src/const.ts)。

## 快捷命令

在企微里单独发送一条纯文本命令，不带参数或附件：

私聊直接发送 `/help`。仅当收到的整条正文是命令本身时才识别，不剥离任何位置的 `@`。
企微群聊若必须 @机器人 才能投递消息，本期不支持这类带 @ 的快捷命令，请在私聊使用；群聊普通问答仍可用。
命令仍会作为一条消息出现在企微聊天里；“拦截”指网关直接处理、不交给模型，不是阻止企微发送或撤回消息。

| **命令** | **作用** |
| --- | --- |
| `/help` | 查看帮助，不调用模型 |
| `/new` | 停止当前任务、取消此前已排队消息，确认后新开会话，旧历史保留 |
| `/stop` | 停止当前任务、取消此前已排队消息，保留当前会话 |
| `/status` | 查看 Agent、默认模型、Session、任务状态及等待消息数 |
| `/whoami` | 查看发送者 User ID、群聊 Chat ID 和配置的会话用户 |

没有 `/reset`。未知命令或参数会收到用法提示；正文、引用或附件内的命令字样不触发。控制台、DeepCube
和电子表格助手保持原客户端规则，服务端聊天接口不会全局拦截斜杠消息。命令不会写成模型对话轮次。

“配置中心 → 渠道 → 快捷命令”是同一帮助目录的只读说明，即使网关未部署也可打开，不会执行命令。
群聊共用 Session，群成员发出的 `/new`、`/stop` 会影响整个群会话；停止不撤销已完成的外部操作。

### 每条消息的发送者上下文

Binding 只代表来源会话，群内 A、B 发言仍指向同一个 Session；不把当前发送者写成群的固定属性。
Runs 的 `sender_id` 继续用于该条消息的处理记录；群聊逐条、私聊仅每个 Session 首轮增加 `channel.message_source`
Context Reference，基础字段保存 `channel_type/account_id/peer_type/peer_id/sender_id`，随 User Event
进入历史和模型输入。模型能看到“A 说了什么、B 说了什么”，不必反查 Runs，也不拆分群历史。
私聊后续沿用首轮身份，不再向每条消息重复追加；网关重启不会重带，`/new` 后的新 Session 首轮重新携带。
首轮按是否已有持久化用户消息判断，命令、标题占位与未入会话的失败请求不占首轮。
旧消息不伪造发送者、不回写历史；已有私聊不补写来源上下文。

“查询用户详情”开启时，来源 payload 额外保存 `userid/name`，查询未成功为 `null`；`sender_id` 和
`peer_id` 仍是原始 ID。群聊每轮独立解析当前发言人；私聊仍只注入首轮。超时后可以后台补全资料及
当前自动标题，但不回写首轮上下文、不在后续每轮追加身份。关闭时不增加这两个字段。

经过内部 Token 验证的渠道请求才追加中文渠道系统说明：识别不同发送者、优先回答当前问题、区分引用、
不将其他人的个人信息或授权混用、明确群内可见性与 UI/HITL 边界。普通客户端不追加该说明；外部
`sender_id` 不替代平台会话用户，不增加业务权限，不能仅凭正文或 Context Reference 获得渠道身份。

执行中收到 `/new` 时，控制命令不会排在 Agent 后面等待。网关立即暂停派发、清理命令之前的队列并
请求停止，确认退出后才换绑；新到消息暂存，成功后进入新 Session。15 秒仍无法确认时会提示“仍在停止中”，
不新建会话、不派发新消息；确认退出后恢复原会话队列。如仍需新会话，请再发 `/new`。

## 引用消息

企微回调的 `body.quote` 与 `body.text.content` 是独立字段，不能只取正文。Adapter 支持官方定义的
文本、图片、图文混排、语音转写、文件和视频引用；正文保留为本次问题，引用进入现有
`context_refs`（`type=channel.message_quote`），随本轮 User Event 保存并参与模型上下文。
Console 可通过原有“引用消息”上下文查看，无需额外查询群历史或通讯录。

引用媒体与普通附件共用官方 SDK 下载解密及现有 FileStore/上传接口，再以文件引用送入当前轮；
原始帧、下载 URL 和媒体 AES Key 不进入模型或数据库。引用没有原消息 ID、发送者等字段时不推测补值。
媒体未返回有效下载信息或下载失败时明确报错，不静默丢掉引用后继续回答；文件格式、大小、模型模态
仍受现有附件链路限制。此能力只读取本条回调附带的引用，不等于获取完整企微聊天历史。

引用中的 `/new`、`/stop` 等永远不会触发命令；携带引用的整条消息也只按普通提问处理。
依据：[企微接收消息与引用结构](https://developer.work.weixin.qq.com/document/path/100719)、
[长连接消息结构及媒体密钥](https://developer.work.weixin.qq.com/document/path/101463)。

## 会话和故障处理

- 私聊按 `account + single + from.userid` 复用 Session；群聊按 `account + group + chatid` 复用 Session。企微 `peer_type` 使用原生小写 `single/group`，界面中的标识名称仍展示 User ID / Chat ID。
- Account 切换 Agent 或会话用户后，下一条消息创建新 Session；旧历史及其用户归属保留。
- 删除 DeepAgent Session 后，下一条企微消息自动重建绑定。
- 关闭用户详情查询时，标题为 `User｜User ID` 或 `Group｜Chat ID`，实际收到名称时为 `User｜名称（User ID）` 等，不按聊天内容生成；来源保存在 Session 的标题事件，
  `/new` 后旧 Session 仍保留来源，人工改名不会丢失它。控制台列表图标和详情可查看渠道来源。
- 开启查询后的私聊为 `user/姓名/原始 ID`，没有姓名时为 `user/原始 ID`；群标题保持不变。
  回调名称与查询资料分开处理；成员查询、刷新、关闭查询或更换应用均不修改已有标题及历史上下文。
- Binding 的 `peer_metadata` JSON保存来源会话的用户/群ID及实际收到的名称；查私聊身份不依赖Runs。
  私聊可同时保留回调的Chat ID；群内每条消息的发送者仅记在Runs，不作为群绑定身份。
- 以下只适用于未完成历史修复的早期 002 本地结构，**不是 `005 → 006` 升级步骤**。先停止 API 和 Gateway，运行
  `PYTHONPATH=src python3.12 -m maintenance.channel_peer_metadata --sqlite data/deep-agent-local.db`只读预检，
  确认后追加`--apply`。脚本先生成带`TMP_to_delete`的私有完整备份，再转换旧名称列、来源快照和
  自动渠道标题；保留Session/Binding ID、人工标题与历史正文，Revision仍为002。共享环境需维护窗口，不自动执行。
- 重复 `msgid` 不会重复执行；Gateway 重启会把未完成 Run 标为 interrupted。只有明确在调用 Agent
  之前失败的记录允许重新认领；HTTP 已发出而结果未知时，即使没有拿到 Run ID 也不自动重跑。
- 企微不负责给模型补历史；上下文始终以 DeepAgent Event Store 为准。

普通回复向企微输出模型 Markdown 文本，内置 Message 另行发送用户请求的附件；thinking、工具参数、轨迹和内部错误不会外发。

## Agent 发送消息与附件

可信企微渠道对话自动获得内置 `channel_message`，无需再给 Agent 配置发送用 MCP。支持向本轮群或私聊发送文字、图片、文件，以及符合格式的已有语音、视频；PNG/JPEG 作为原生图片发送，其他图片格式及不适合原生发送的音视频按普通附件投递。暂不提供原生引用回复选项，也不接受其他用户或群作为目标。

媒体由已授权的 `file_id` 引用，上传后取得 media_id 的过程封装在企微适配器中。上传复用当前机器人连接与公开 SDK 扩展命令，按官方分片协议处理；独立发送使用新的请求 ID，不占用现有流式回复句柄。无需额外 Node 服务或第二条机器人连接。

v0.14.8：媒体也可填写公开HTTP(S) `url`，与`file_id`二选一；受控下载仅留在内存，不先存平台。
已知JPEG/PNG原样转发无需Python，裁剪／转码仍需Python；开启PTC后同一工具也可在`run_code`中调用。
各组件全部完成预检才开始发送；PTC到期后已分发的发送结果可能未知，不能直接补发。

普通回答、首段加载与持续流式效果保持原链路。一个 Message 调用可有多个有序内容；发送失败或结果未知停止后续项，已成功项不重发。工具不负责语音识别、生成音视频或转码，权限与格式限制不会因内置而绕过。

Message 不再另存发送回执，也不新增 Runs 字段或缓存替代；逐项结果仅描述本次调用，不承诺跨请求、跨重启发送去重。经用户单独确认，009→010 删除旧 channel_message_operations 表及其中回执，并增加会话来源两列；不自动修复历史来源，保留 009 迁移和其他业务数据；升级前备份数据库。现有 MCP 可继续用于其他业务，当前对话发送优先用内置 Message。

已在本地测试企业验证私聊、群聊、超过 512 KiB 的真实多分片上传、原生音视频与双渠道并发；取消、重连和权限边界由受控回归测试覆盖。

## 已有本地会话来源修复

以下是旧 0.7.0 / 002 测试数据的可选维护工具，不是当前版本安装或升级必做步骤；当前已正常的来源不要重复修复。工具补充来源，不新增表：

```bash
PYTHONPATH=src python3.12 -m maintenance.channel_origins --sqlite data/deep-agent-local.db
```

默认只检查；先停止 API 和 Gateway，再加 `--apply` 执行。工具会先生成权限 `0600`、名称带
`TMP_to_delete` 的完整 SQLite 备份。只修复能从 Binding/Run 唯一关联的 Session，保留人工标题和
无法判定来源的旧标题，不修改历史正文或活跃时间；重复执行会跳过已有来源。

维护后仍按同一配置先启动 API，再启动 Gateway。不要用该本地工具处理共享 Test 数据库。


### 统一用户资料展示与搜索

渠道统计以会话主表来源筛选，再从 Events 计算提问，Runs 仅关联真实发送者。控制台续问计入会话来源渠道，无真实发送者的提问单列，不计入成员人数。企微官方 open_userid ↔ userid 映射继续用于身份归一，不按姓名合并。同一资料服务负责企微、飞书、钉钉的入站身份、列表、详情及姓名搜索；企微复用成员缓存表和原有有效期，飞书使用 SDK worker 内存缓存。

页面姓名仅来自资料服务。资料不可用或查询关闭时显示 ID，不以 Runs.sender_name、历史上下文或旧标题中的姓名兜底；这些历史内容仅保留当时快照。企微成功解析后，在当轮既有 Run 写入 sender_name、可信关联应用的 tenant_id，以及带 wecom_ 前缀的 source_metadata，不新增表或列，不回填旧 Run。

姓名搜索覆盖当前渠道、选定时间及筛选条件内的会话用户，按资料姓名匹配后分页，同时支持原始及映射 ID。资料未完整取得时提示结果可能不完整；不是企业通讯录搜索。普通页补查预算 3 秒，姓名搜索 10 秒，不扫描历史消息正文。

保存校验按平台 `checkApiKey.errorMsg` 分别提示 Key 不存在、空间不匹配、停用或过期、应用未授权；通过后再比较会话用户。平台当前不能区分停用与过期，也不返回失败时的所属用户；按首次失败提示，不声称同时验证全部条件。未知上游原因只给安全通用提示，不透传原文。
