# 渠道网关与企业微信接入

DeepAgent 0.7.0 支持企微智能机器人 WebSocket 长连接。API 与 Gateway 使用同一代码和镜像，但
Gateway 是可选独立进程：不使用渠道的环境无需启动，也不会建立外部连接。公共网关通过 Adapter
管理渠道，当前只实现企微 Adapter；飞书需要后续增加适配，不能直接填配置启用。

## 前置条件

- 数据库已由 API 升级到 Alembic `002`。
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

Gateway 不运行迁移，由 API 先完成当前代码的迁移。Gateway 不维护固定 Revision 白名单，健康接口仅返回启动时读取的实际 Revision。0.7.0 保持一个 API worker、一个 Gateway 副本：
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

## Console 配置

在“配置中心 → 渠道”创建企微账号：填写渠道名称，选择 Agent，并填写 Bot ID、Secret、会话用户。
“会话用户”直接输入平台 User ID，对应 `session_user_id`；只有企业管理员可以配置。
它决定 DeepAgent Session 的归属，不负责签发平台权限或 Token。平台凭据仍由私有运行环境提供，
应与实际业务访问账号匹配；渠道不配置 App、Space 或 Language。
Secret 按0.7.0产品约定明文入库，
但管理接口只返回是否已配置，不返回原值；编辑时留空即保持原配置。配置备份仍必须按敏感文件保护。

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
Context Reference，白名单保存 `channel_type/account_id/peer_type/peer_id/sender_id`，随 User Event
进入历史和模型输入。模型能看到“A 说了什么、B 说了什么”，不必反查 Runs，也不拆分群历史。
私聊后续沿用首轮身份，不再向每条消息重复追加；网关重启不会重带，`/new` 后的新 Session 首轮重新携带。
首轮按是否已有持久化用户消息判断，命令、标题占位与未入会话的失败请求不占首轮。
旧消息不伪造发送者、不回写历史；已有私聊不补写来源上下文。

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
- 默认标题为 `User｜User ID` 或 `Group｜Chat ID`，实际收到名称时为 `User｜名称（User ID）` 等，不按聊天内容生成；来源保存在 Session 的标题事件，
  `/new` 后旧 Session 仍保留来源，人工改名不会丢失它。控制台列表图标和详情可查看渠道来源。
- Binding 的 `peer_metadata` JSON保存来源会话的用户/群ID及实际收到的名称；查私聊身份不依赖Runs。
  私聊可同时保留回调的Chat ID；群内每条消息的发送者仅记在Runs，不作为群绑定身份。
- 本地已安装早期002结构时，先停止API和Gateway，运行
  `PYTHONPATH=src python3.12 -m maintenance.channel_peer_metadata --sqlite data/deep-agent-local.db`只读预检，
  确认后追加`--apply`。脚本先生成带`TMP_to_delete`的私有完整备份，再转换旧名称列、来源快照和
  自动渠道标题；保留Session/Binding ID、人工标题与历史正文，Revision仍为002。共享环境需维护窗口，不自动执行。
- 重复 `msgid` 不会重复执行；Gateway 重启会把未完成 Run 标为 interrupted。只有明确在调用 Agent
  之前失败的记录允许重新认领；HTTP 已发出而结果未知时，即使没有拿到 Run ID 也不自动重跑。
- 企微不负责给模型补历史；上下文始终以 DeepAgent Event Store 为准。

Gateway 只向企微输出模型 Markdown 文本；thinking、工具参数、轨迹和内部错误不会外发。

## 已有本地会话来源修复

旧 0.7.0 测试数据可通过幂等维护工具补充来源，不新增表，不修改 Alembic `002`：

```bash
PYTHONPATH=src python3.12 -m maintenance.channel_origins --sqlite data/deep-agent-local.db
```

默认只检查；先停止 API 和 Gateway，再加 `--apply` 执行。工具会先生成权限 `0600`、名称带
`TMP_to_delete` 的完整 SQLite 备份。只修复能从 Binding/Run 唯一关联的 Session，保留人工标题和
无法判定来源的旧标题，不修改历史正文或活跃时间；重复执行会跳过已有来源。

维护后仍按同一配置先启动 API，再启动 Gateway。不要用该本地工具处理共享 Test 数据库。
