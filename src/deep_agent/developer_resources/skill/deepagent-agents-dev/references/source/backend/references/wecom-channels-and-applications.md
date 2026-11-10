# 渠道、企微应用与成员身份

资料核对：2026-11-06，历史参考基线 Head 013；目标环境须另读 /info 和 OpenAPI。后续以当前源码和目标环境OpenAPI为准。适用于渠道设置、成员名称解析、缓存、身份上下文及渠道诊断；不意味着授权修改线上配置或主动向他人发消息。

## 配置对象与用户路径

“渠道”页标题同行切换“渠道账户｜企微应用”，不是两个一级导航。

| 对象 | 管什么 | 不代表什么 |
| --- | --- | --- |
| 渠道账户 | Bot ID、Bot Secret、连接启停、DeepAgent Agent、平台 `session_user_id` | 不是企业应用，也不为发送者签发平台权限 |
| 企微应用 | 企业ID `corp_id`、企微Agent ID `wecom_agent_id`、App Secret、成员缓存 | 不是DeepAgent Agent；企微Agent ID也不是Bot ID |
| 成员资料 | 同应用下Open User ID与官方User ID映射、姓名、有效期 | 不是平台用户目录、完整企微历史或全通讯录镜像 |

新建企微应用，填写名称、企业ID、企微Agent ID和App Secret；测试连接后，到渠道编辑器开启“查询用户详情”并关联该应用。一个应用可供多个机器人共用。渠道新建默认关闭，启停在列表Switch上维护；资料查询开关与连接启停分开。

Bot Secret用于长连接收发，App Secret用于企业应用HTTP接口。企业ID与企微Agent ID创建后不可改，换身份需新建应用。应用名称与Secret可改；Secret省略、留空或仍为掩码时保留原值，主动清空需 `clear_app_secret:true`，不能同时提交非空新Secret。保存前取消弹框不改变配置。当前凭据按产品约定明文入库，管理响应仅给掩码及 `secret_set`，备份仍必须作为敏感文件保护。

“测试连接”只验证获取Access Token，不能证明成员查询权限、应用可见范围或可信IP正确。可信IP按实际对外请求出口核对，不能拿本机网卡地址代替。

网关状态与机器人状态分开：网关未部署/不可达/运行中描述API到Gateway的连接，机器人已关闭/连接中/已连接/冲突等描述Bot长连接。保存应用和渠道不代表网关已启动；同Bot重复长连接可能互相顶替。API先完成迁移，Gateway不迁移；当前队列与取消注册表不跨实例共享，不能随意扩成多副本。

## 查询开关与引用规则

更新渠道时保留不相关settings及wecom扩展字段：

```json
{"wecom":{"user_details_enabled":true,"application_id":"<企微应用记录ID>"}}
```

- 缺少wecom、wecom为null、缺少开关或false均不查询成员；true必须关联真实存在的应用。
- 关闭查询可以保留引用，重新开启时继续用。关闭开关不等于解除引用，保留的引用仍阻止删除应用。
- 删除应用前先明确清空相关渠道引用；允许删除时连同可重建成员缓存删除，不删除Agent和历史会话。
- 没有“是否超管”“明文/密文”配置，不按ID前缀或姓名猜测用户身份。

## 身份查询与缓存

API进程持有企微客户端、Token、查询任务和缓存服务。Gateway只通过内部Token及渠道账户认证后的接口请求解析，应用由渠道配置确定，调用者不能任选应用。

先匹配已知Open User ID或规范化官方User ID；未知ID先走官方转换。只有转换整体成功且输入明确列在 `invalid_open_userid_list` 时，才尝试以原始值作为明文User ID查询。网络、凭据、权限和可信IP错误不是明文判据。

同应用按规范化User ID去重，确认后补齐Open User ID；不同应用不混用缓存，不按姓名合并，不把明文写入密文字段。未确认身份不创建占位资料。外部微信用户无法解析时保留原始ID，不承诺客户姓名或群名。

成功资料有效7天；过期且有姓名时先用旧值、后台刷新，失败保留旧资料。未知身份失败负缓存5分钟。首次解析最多等5秒，超时继续对话，后台任务最多20秒；外部请求并发上限4，同应用同身份合并查询。控制命令与取消不等待成员查询。Token按有效期提前刷新，明确失效只重试一次。

应用Secret变更、缓存重置和渠道应用切换后，以应用revision与当前渠道引用防止迟到结果覆盖新状态。排查需要检查这些保护，不能仅加前端刷新掩盖竞态。

## 缓存设置

Console显示已缓存用户/待更新数、最早创建、最早成功更新。没有成员明细表；页面仅提供“重置缓存”，不展示批量刷新入口。

重置保留姓名及ID映射，将资料标记过期、清除失败等待，后续消息触发更新；不等于清空数据库，也不等于立即重新拉全量资料。结果回读统计，连接测试与缓存操作分别展示结果。

兼容的后台refresh API仍存在，只刷新已知成员，不能据此重新给页面添加全通讯录同步。刷新任务在进程内，任务丢失/重启时返回结果待确认，先读统计，不能伪称回滚或成功。

## 标题、来源与权限

- 私聊按账户+single+原始发送者复用Session，群聊按账户+group+Chat ID复用。群内不同人仍共享群Session。
- 私聊自动标题为 `User｜姓名(原始 ID)`，无姓名为 `User｜原始 ID`；群聊为 `Group｜Chat ID`。旧 `user/姓名/ID` 写法已过时。
- 只更新当前绑定私聊的自动标题，保护人工标题；不会批量改写历史标题或User Event。
- `channel.message_source` 保留 `channel_type/account_id/peer_type/peer_id/sender_id`。开启查询才增加 `userid/name`，未取得值为null；关闭不增加这两项。
- 私聊仅首条实际持久化用户消息注入来源，`/new`新Session重新计算；群聊逐条注入当前发送者。私聊首轮超时后的后台成功可补标题和缓存，不补写已持久化上下文。
- `sender_id/peer_id`是渠道原始ID；`userid`是企微官方账号；`session_user_id`是平台owner。资料解析、Context Reference和群成员变化不改变平台执行权限或模型工具授权。

## 渠道运行与说明

- 渠道网关按已验证的渠道账户装配当前 Run。来源与入站引用作为用户消息的 `channel.message_source`、`channel.message_quote` 上下文保存，引用字段按平台实际提供。当前渠道、回复去向及处理规则通过 Run 指令注入，快捷命令与媒体限制按需查询。
- `channel_doc()` 和 `channel_message` 自动提供给渠道 Run。用户问渠道怎么用时，模型可直接调用无参数的 `channel_doc()`。工具只返回当前渠道说明，快捷命令沿用 `/help` 文案，并说明群聊与私聊、入站内容、图片和语音等媒体的格式与限制、引用和转发支持。
- 普通回答自动发回当前聊天。主动发图或附件时参照 `channel_message` 的工具说明，已确认发送的内容避免重复发送，结果未知时先核对。渠道 Run 屏蔽 UI 工具。

## API入口

以下路径前缀为 `/api/v1`；应用管理和诊断需管理员，响应no-store。列表返回数组而不是items包装。

| 路径 | 方法/用途 |
| --- | --- |
| `/wecom-applications` | GET列表、POST新建(201) |
| `/wecom-applications/{id}` | GET详情、PUT修改、DELETE删除(被引用409) |
| `/wecom-applications/{id}/test-connection` | POST验证Token，读取success/message而不是只看HTTP200 |
| `/wecom-applications/{id}/cache` | GET统计 |
| `/wecom-applications/{id}/cache/invalidate` | POST重置失效状态并返回统计 |
| `/wecom-applications/{id}/cache/refresh` | POST兼容刷新(202)，仅已知成员 |
| `/wecom-applications/{id}/cache/refresh/{task_id}` | GET任务状态，丢失404/待确认 |
| `/internal/channels/wecom/resolve-user` | POST，仅内部Token+账户认证；有session_id还校验归属，不给普通前端直连 |

006新增两张表 `wecom_applications/wecom_user_profiles`；配置备份复制应用及渠道settings/凭据，成员缓存只建结构。Agent安装ZIP不含这些环境设置。迁移/备份规则见 [数据库迁移](database-migrations-and-backups.md)。

## 渠道统计与平台用户搜索

会话→渠道详情使用 `/diagnostics/channels/accounts`、`/{account_id}/overview`、`/{account_id}/peers`、`/{account_id}/sessions`。它查询DeepAgent已保存的渠道账本和事件，不是企微全量聊天记录。

用户/群聊候选从当前账户与查询时间范围的数据库记录去重，独立于表格当前分页；群聊可额外用sender_id过滤。切换用户/群聊保留右侧用户条件，群过滤仅在群聊显示；活跃用户点击与条件联动。`kind=user/group`是页面筛选值，渠道回调peer_type仍是single/group。用户名来自已有资料缓存，诊断查询不发起企微成员同步。

平台会话列表的 `/diagnostics/sessions/users` 则按时间/Agent扫描原生平台User ID并批量补Username，候选按next_after游标继续，不能只拿已加载50条会话或排行当全集。权限缓存需区分环境及认证身份，401/403时清除页面身份结果。平台会话接口已移除source筛选；日志API的source仍保留，不能一并删除。

## 源码核验与测试

先从实际项目根定位（通常 `<knowledge-base>/deepfos-AI/deep-agent`）：

- `api/v1/wecom_applications.py`：应用/缓存/内部解析路由和Secret清空契约。
- `db/stores/wecom.py`、`services/wecom_profiles.py`、`services/wecom_client.py`：引用、身份合并、有效期、任务与官方请求；路径前加 `src/deep_agent/`。
- `channels/metadata.py`、渠道Store与Gateway：上下文、自动标题、当前绑定及权限边界。
- `console/configuration/wecom-applications.js`、`channels.js`：实际配置入口；`console/diagnostics-channels.js`：统计页。
- `src/deep_agent/diagnostics/channels.py`、`security/diagnostic_users.py`：渠道数据库候选与平台账号补齐两条链。
- `docs/guides/wecom-applications.md`、`wecom-channel.md`、`diagnostic-sessions.md`：产品指南；旧说明与源码冲突时以源码为准，勿复制旧标题格式。

定向测试先确认现有文件名：`test_wecom_store.py`、`test_wecom_profiles.py`、`test_wecom_applications_api.py`、`test_wecom_migration.py`、`test_wecom_identity_events.py`、`test_diagnostic_channels.py`及Console相关测试。按实际改动选择，纯skill说明更新不重启服务或触发真实消息。真机收发由用户触发；只读Token/成员查询、隔离模型事件测试、真实群消息验收分别记录，不能互相冒充。
