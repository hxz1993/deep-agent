# 渠道身份与企微应用：前端理解和交接

资料核对：2026-11-06，DeepAgent {{DEEP_AGENT_VERSION}} / Head013；目标环境须另读 /info 和 OpenAPI。适用于解释渠道配置、排查企微姓名/发言人与会话展示、阅读管理员诊断证据。内置 Console 实现和后端修改归 `$deepfos-dev-agent-backend`；普通元素聊天前端不调用内部解析接口，也不增加跨用户管理能力。

## 设置的三个层次

| 对象 | 配置和职责 | 不代表什么 |
| --- | --- | --- |
| DeepAgent Agent | 提示词、模型、Skill 与工具选择 | 不是企微应用的 Agent ID |
| 渠道账户 | Bot ID、Bot Secret、绑定 DeepAgent、平台会话用户和启用开关，负责机器人连接及会话路由 | 不是企业通讯录凭据 |
| 企微应用 | 名称、企业 ID、企微 Agent ID、App Secret，为一个或多个渠道查询成员账号与姓名 | 不负责 Bot 长连接，不替换平台身份 |

“渠道”页标题右侧切换“渠道账户 / 企微应用”。先创建企微应用，再编辑渠道，开启“查询用户详情”并选择应用。企业 ID 与企微 Agent ID 创建后不可改；更换企业或应用应新建，名称与 App Secret 可更新。连接测试成功只证明取得 Access Token，不证明全部成员可查；还取决于应用可见范围、接口权限与实际出口可信 IP。

渠道的“会话用户”是平台 `session_user_id`，决定 DeepAgent 会话归属；企微原始 `sender_id`、查询得到的官方 `userid/name` 都不会替换它或增加工具权限。原始 ID、规范化 ID 和姓名须分别保留，不能按姓名去重，也不能靠 ID 前缀判断是否超管。

## 关联、凭据和缓存的界面语义

- 渠道保存 `settings.wecom.user_details_enabled` 与 `settings.wecom.application_id`，保留 `settings` 中其他扩展项。开关默认关闭；开启必须关联存在的应用。
- 关闭查询可以保留应用引用，且不关闭机器人。保留引用仍阻止删除应用；删除前须在相关渠道清空引用。
- Bot Secret 与 App Secret 是不同字段。管理响应只返回掩码/是否已设置。编辑时省略、留空或原掩码表示保留，取消弹窗不写入。App Secret 的显式“清空”并保存才删除；不要把该标志套用到 Bot Secret，也不要把掩码当新密钥提交。
- App Secret 的主动删除使用 `clear_app_secret:true`；不与非空新 Secret 同时发送。轮换会使旧 Token/查询结果失效，旧异步结果不能覆盖新配置。
- 应用页只展示已缓存用户/待更新、最早创建、最早更新统计，不存在可供前端读取“全部成员”的明细接口。
- “重置缓存”保留姓名和 ID 映射，只标记过期并清除失败等待；后续消息按需刷新。它不是删除通讯录，也不是批量拉取企业所有用户。页面只提供重置，后台兼容的刷新 API 不等于页面仍有刷新按钮。
- 解析成功资料有效期为 7 天；有旧姓名时先使用旧资料并后台刷新，未知身份失败有重试等待。未确认时保留原始 ID，不阻塞正常对话。前端不自己重做 ID 转换或通讯录请求。

## 会话与消息展示

- 私聊绑定以渠道账户与原始私聊对象为准，群聊以账户与 Chat ID 为准；群内不同发言人仍属于同一 Session。
- 开启查询后，自动私聊标题为 `User｜姓名(原始 ID)`，无姓名为 `User｜原始 ID`；群聊为 `Group｜chat_id`。保护人工标题，不批量改写结束会话或历史上下文。
- `channel.message_source` 保存原始 `account_id/peer_type/peer_id/sender_id`；查询开启时增加 `userid/name`，未解析到时为 null。私聊只在每个 Session 首条持久化用户消息注入；群聊每轮注入当前发言人。后台迟到姓名不会回写首轮或补注入私聊后续消息。
- 企微消息引用是 `channel.message_quote`，是本条回调带来的引用上下文，不代表抓取完整群历史。展示和取证仍按已有 History/context_refs 协议。
- 姓名是显示与模型上下文信息；看到姓名不能证明业务授权，也不能证明历史每条消息都已补齐姓名。

## 渠道详情与筛选

管理员 Console 的入口为 `/console/#/diagnostics/sessions/channels`。API 前缀 `/api/v1/diagnostics/channels`：

| GET 路径 | 用途 |
| --- | --- |
| `/accounts` | 渠道账户目录、关联与平台会话用户信息 |
| `/{account_id}/overview` | 当前时间范围的活跃用户、会话、提问等统计 |
| `/{account_id}/peers` | `kind=user/group` 的统计列表或 `options_only=true` 的独立候选查询 |
| `/{account_id}/sessions` | 指定 `peer_id` 的历史会话；群聊可按 `sender_id` 查询 |

候选由服务端按当前渠道和时间范围从数据库聚合去重并分页，不从表格当前加载数组生成，也不是企微企业通讯录全量。支持 `query/limit/offset`；群聊发言人候选使用 `kind=user&options_only=true&include_group_senders=true`，覆盖在群里发言但没有私聊的用户。服务返回原始 ID 或已确认映射，不由前端猜测合并。

用户条件固定放在工具栏右侧；切换用户与群聊时保留已选用户和搜索状态，群聊额外增加群条件。群聊“用户”表示发言人条件，作用于提问事实及相关会话；它不是平台 owner 过滤，也不是只在当前已加载消息中找文字。点击活跃用户应联动同一用户条件。退出登录或管理员权限失效时清空姓名候选和查询缓存，避免跨身份残留。

## 核验入口

- 产品规则：`<knowledge-base>/deepfos-AI/deep-agent/docs/guides/wecom-applications.md`、`diagnostic-sessions.md`。
- 管理页面：`console/configuration/wecom-applications.js`、`channels.js`；筛选：`console/diagnostics-channels.js`、`channel-peer-picker.js`。
- Schema/API：`src/deep_agent/schemas/channel.py`、`api/v1/wecom_applications.py`、`diagnostic_channels.py`；标题事实：`channels/metadata.py`。
- 解析、缓存、Token、迁移与 Gateway 的细节由后端 skill 核验。不要把前端配置资产 ZIP 当成渠道/企微应用迁移包；Agent 包不带应用、渠道或凭据。
