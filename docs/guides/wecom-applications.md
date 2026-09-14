# 企微应用与成员资料

企微机器人负责接收和回复消息，企微应用负责查询成员账号及姓名。一个企微应用可以供多个渠道账户使用；成员资料按应用保存，不按机器人重复建表。此功能从 v0.10.1 / Alembic `006` 开始提供，升级后已有渠道默认关闭资料查询。

## 配置入口

“渠道”标题同行右侧提供 **渠道账户｜企微应用** 切换。渠道账户仍管理机器人连接和 DeepAgent 归属；企微应用管理企业凭据和成员缓存，不新增一级导航。

1. 在“企微应用”中新建应用，填写应用名称、企业 ID、企微 Agent ID 和 App Secret。
2. 首次进入默认选择左侧首个应用，之后保留有效选择；可点整张应用卡查看右侧应用配置（包含企业 ID）及缓存统计；“测试连接”验证是否可以取得 Access Token。
3. 返回“渠道账户”，编辑机器人，开启“查询用户详情”并选择该应用，然后保存。
4. 由用户给机器人发送消息；首次遇到的成员会按需解析，之后复用缓存。

企业 ID、企微 Agent ID 创建后不可修改；换企业或应用应新建配置。名称可修改，App Secret 可以更换，轮换后 Token 缓存和旧查询结果失效。

App Secret 与 Bot Secret 是不同凭据。Bot Secret 用于机器人长连接；App Secret 用于企业应用接口。两者按当前产品约定明文存库，但普通管理接口只返回掩码。编辑时留空、保留原掩码或未修改均不替换，实际输入新值才更新。需要删除凭据时，使用字段标题右侧“清空”并保存；待删除时明确显示“保存后删除密钥”，保存前可以点“保留原密钥”取消删除，取消弹窗不会修改配置。手动留空与主动清空是两种不同操作。配置备份包含凭据，仍按敏感文件保护。

连接测试成功只说明能取得 Token，不代表应用对所有成员都有查询权限。查询还受企微应用可见范围、接口权限和可信 IP 限制；可信 IP 应配置企微实际看到的请求出口地址，不能仅根据本机网卡地址判断。

## 查询开关与应用引用

渠道设置保存在已有 `channel_accounts.settings` 中，其他扩展字段保持不变：

```json
{
  "wecom": {
    "user_details_enabled": true,
    "application_id": "已创建的企微应用记录 ID"
  }
}
```

- 缺少 `wecom`、缺少开关或开关为 `false`：不解析 ID，不请求成员资料，标题和上下文保持原行为。
- 开关为 `true`：必须选择存在的企微应用，否则保存失败。
- 关闭开关可以保留应用引用，方便重新开启；不影响渠道启用状态和机器人连接。
- 保留的应用引用仍参与删除保护。要删除应用，先在相关渠道中清空引用；删除会同时移除该应用的可重建成员缓存。
- 不提供“是否超管”或“明文／密文”选择。查询服务根据官方接口返回确认身份，不依据 ID 前缀猜测。

## 身份与缓存

API 进程统一持有企微 HTTP 客户端、Token 缓存和刷新任务，Gateway 通过带内部 Token 的受控接口请求解析。调用者不能指定任意应用，应用取自认证后的渠道配置。

先按已知 Open User ID 或规范化 User ID 查找缓存；未命中时调用官方密文转换。只有转换整体成功、且官方明确把输入列入无效 ID，才尝试按明文账号读取成员。网络、Token、权限或可信 IP 错误不会触发“明文”判断。

成员记录包含 Open User ID（可空）、官方 User ID 和姓名。明文入口先出现时只保存 User ID，后续确认对应密文时补充 Open User ID；同应用下按规范化 User ID 去重，不把明文复制到密文字段，也不按姓名合并。未确认身份不创建占位成员记录；外部微信用户或无法解析的 ID 保留原始值，姓名为空，不阻塞对话。本期不接入客户联系接口、群名接口或企微会话 MCP。

成功取得资料后有效期为 **7 天**。资料过期但已有姓名时，当前消息先使用旧姓名，后台刷新；失败保留旧资料，并记录安全错误码和下次重试时间。未知身份失败等待 5 分钟后再尝试，避免每条消息重复请求。

普通消息先向企微显示原生加载反馈，模型请求前最多等待首次解析 5 秒；超时使用原始 ID 继续，查询后台最多执行 20 秒。控制命令和取消不等待资料查询。外部请求最多并发 4 个，同应用同身份查询合并；Token 根据 `expires_in` 提前刷新，明确失效时仅重试一次。

### 应用级操作

右侧只展示三项统计，不开放成员明细表：

| 统计 | 含义 |
| --- | --- |
| 已缓存用户 | 成员记录总数及待更新人数；未成功取得姓名的用户也计入待更新 |
| 最早创建 | 最早一条成员映射的创建时间 |
| 最早更新 | 各成员最近一次成功取得资料时间中的最早值；无成功资料显示“—” |

Console 仅提供“重置缓存”，不再提供主动批量刷新入口；资料在收到消息时按需刷新。已有后台刷新 API 保留兼容，但页面不会发起该任务。

官方批量接口的范围与此不同：[`user/list`](https://developer.work.weixin.qq.com/document/path/90201) 按部门读取；[`export/user`](https://developer.work.weixin.qq.com/document/path/94851) 异步导出应用有权限的成员。当前已知成员名单使用 [`user/get`](https://developer.work.weixin.qq.com/document/path/90196) 逐人读取、最多 4 个请求并发，不为了刷新缓存扩大读取通讯录的范围。ID 转换接口的批量能力不等同于批量查询姓名。

**重置缓存**经按钮旁确认后，保留姓名及 ID 映射，将资料标记为过期并清除失败等待。待更新数随统计回读更新；后续消息按需刷新。旧任务不能覆盖重置后的失效状态。

重置结果显示在缓存操作按钮下；连接测试结果单独显示在应用操作按钮下。

刷新任务是进程内任务。进程重启或任务记录丢失后，页面显示“结果待确认”并重新读取统计，不假定成功或回滚。

## 会话标题与模型上下文

开启查询后的私聊标题：有姓名时为 `user/姓名/该会话原始 ID`；未取得姓名时为 `user/原始 ID`。普通机器人保留收到的密文 ID，超管机器人保留收到的明文 ID。群聊标题沿用原逻辑，不查询群名。

只更新当前绑定私聊的渠道自动标题，保护人工修改的标题；应用切换或关闭查询后按当前配置重新计算自动标题。已经结束的历史会话及已持久化上下文不会批量改写。

现有 `channel.message_source` 的原始字段含义不变；仅在查询开启时增加 `userid`、`name`，没有结果时为 `null`：

```json
{
  "channel_type": "wecom",
  "account_id": "channel-record-id",
  "peer_type": "group",
  "peer_id": "original-group-chat-id",
  "sender_id": "original-member-id",
  "userid": "official-member-account",
  "name": "成员姓名"
}
```

私聊只在首条实际持久化的用户消息注入来源，`/new` 后重新计算首轮；群聊每轮注入当次发送者身份，不把成员信息当作群属性。私聊首轮超时后，后台成功可以更新缓存与标题，但不重写首轮、不在后续每轮补注入。平台会话所有者、执行账号和工具权限不随企微身份变化。

## 接口与数据范围

管理接口复用 Console 管理员鉴权，响应禁止缓存。应用列表为数组，详情返回 `id/name/corp_id/wecom_agent_id/app_secret_masked/secret_set/settings/revision/created_at/updated_at/channel_count/channels`，不返回原始 Secret。

| 方法与路径（前缀 `/api/v1`） | 用途 |
| --- | --- |
| `GET /wecom-applications` | 应用列表 |
| `POST /wecom-applications` | 新建应用，成功返回 201 |
| `GET /wecom-applications/{id}` | 应用详情 |
| `PUT /wecom-applications/{id}` | 修改名称、凭据或应用扩展设置；身份不可变 |
| `DELETE /wecom-applications/{id}` | 删除无渠道引用的应用及成员缓存 |
| `POST /wecom-applications/{id}/test-connection` | 返回 `success/message`，只验证 Token |
| `GET /wecom-applications/{id}/cache` | 返回 `total/pending/earliest_created_at/earliest_synced_at` |
| `POST /wecom-applications/{id}/cache/refresh` | 返回 202 和任务状态 |
| `GET /wecom-applications/{id}/cache/refresh/{task_id}` | 查询任务；不存在时 404，结果待确认 |
| `POST /wecom-applications/{id}/cache/invalidate` | 标记资料过期，返回最新统计 |

应用更新时，显式 `clear_app_secret:true` 清空已有凭据；不能与非空 `app_secret` 同时提交。省略、空字符串或原掩码仍保留凭据。凭据为空时，测试连接明确提示未配置，不发起企微请求。

刷新响应包含 `task_id/application_id/status/total/completed/succeeded/failed`；状态包括 `running/completed/failed/unknown`。没有公开的成员列表或单成员管理接口。

内部 `POST /api/v1/internal/channels/wecom/resolve-user` 必须同时通过渠道内部 Token 和渠道账户校验；传入原始 `sender_id`、`peer_type` 及可选 `peer_id/session_id`。有 Session 时还检查其渠道归属。返回 `disabled/unresolved/pending/partial/resolved` 状态及 `userid/name/application_id`，不用于普通前台或业务客户端直连。

## 迁移、备份与验收边界

`006` 在已发布 `005` 后新增 `wecom_applications` 和 `wecom_user_profiles` 两张表，不修改旧迁移、不新增数据库外键。应用 JSON 引用通过逻辑校验、配置协调和事务锁保护；应用版本变更、缓存重置或渠道配置切换后，迟到结果不能写回旧状态。

配置备份包含应用、凭据和渠道 Settings；成员资料属于可重建缓存，仅保留空表结构。Token、负缓存和刷新任务不备份。Agent 整包导出不携带企业应用、凭据或成员资料。导入要求同一 Alembic Revision，保留配置 ID；不要把旧版备份直接标成 `006`。

当新增应用、成员资料或渠道企微扩展设置尚存时，降级 `006 → 005` 会明确拒绝，不能以降级脚本静默清理数据。日常本地继续复用既有 8000、SQLite 和文件目录；迁移往返、备份恢复以及异常分支只能在隔离库验证。

真实验收分开记录：

- 非超管机器人：发布前需确认真实转换、姓名持久化、标题及首轮 User Event 上下文；以实际持久化事件为准，不能仅凭页面姓名判定通过。当前实测结果由发布验收记录补充。
- 超管机器人：真实账号尚未提供，真实验收待补；隔离测试覆盖明文分支不等于真实机器人已通过。
- 外部用户：当前只保证原始 ID 兜底，不承诺获取客户姓名或通讯录。

相关说明：[渠道接入](wecom-channel.md)、[配置备份与初始化](configuration-import.md)。
