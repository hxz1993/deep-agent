# 企微渠道 User ID 获取与身份补全

核验日期：2026-09-13；源码 test@4fd0bd7。只读核验，未改渠道配置，未创建自建应用，也未使用真实企业凭证调用企微接口。

> 历史基线说明：下文“当前／尚未实现”指 2026-09-13 的 `4fd0bd7`。后续成员补全和渠道统计已落地，当前使用方式见[企微应用指南](../../guides/wecom-applications.md)和[会话诊断指南](../../guides/diagnostic-sessions.md)。原始调研保留，不作为最新功能状态。

## 先区分是哪一个 User ID

| 标识 | 从哪里来 | 当前用途 |
| --- | --- | --- |
| sender_id | 企微消息 body.from.userid | 本条消息实际发送者；可能明文 userid，也可能密文 open_userid |
| peer_id | 私聊取 sender_id，群聊取 chatid | 决定这条消息属于哪个私聊/群聊会话 |
| session_user_id | 管理员在渠道中配置的 DeepFOS 用户标识 | 平台会话归属；不能作为所有群成员的员工身份 |
| employee_userid（建议补充层） | 已确认明文原值，或自建应用接口验证的转换结果 | 展示员工账号、身份合并及员工统计；当前尚未实现 |

如果是在渠道表单填写“会话用户”，需要的是 **DeepFOS 账号 ID**。当前用户可从同环境 `GET /api/v1/current-user` 的 `user_id` 取得；要使用其他执行账号应取得该账号的实际平台标识。不能填企微 open_userid 或随意猜 UUID。

如果是问“到底哪个企微员工在聊”，消息已经自动带 sender_id。现在可私聊机器人，单独发送纯文本 `/whoami`，它返回：

```text
User ID：<企微原始 sender_id>
会话用户：<渠道配置的 DeepFOS session_user_id>
```

群聊还会显示 Chat ID。当前命令解析不剥离 @ 文本，必须 @ 才能送达的群建议使用私聊命令。此命令只展示已有值，不做解密或通讯录查询。

## 当前已经实现的接收链路

```text
机器人 BotID + 长连接 Secret
  → 建立企微长连接
  → 收到 body.from.userid、msgid、chatid/chattype
  → Adapter 生成 sender_id、peer_type、peer_id
  → 按 account_id + peer_type + peer_id 找 Binding/Session
  → 每条消息保存 sender_id 到 channel_message_runs
  → 经平台 session_user_id 归属的会话调用 Agent
```

群聊多成员共用一个群会话，但每条消息有自己的 sender_id。因此不能把群 chatid 当作聊天员工，也不能用平台 session_user_id 统计不同企微员工。

`channel.message_source` 还会携带发送者和渠道上下文：群聊每条消息注入，私聊每 Session 首个持久化用户消息注入。`peer_metadata` 只保存实际收到的 name/alias/chat_name，兼容这些字段不代表官方协议保证提供姓名和群名。

## 明文还是密文：官方规则

企微官方长连接与接收消息文档都说明：**机器人创建者为企业超级管理员时，from.userid 是明文 userid；否则是企业主体下的加密 userid**。不能仅凭 `wo` 前缀判定。机器人消息接入成功、媒体解密成功，都不意味着身份已经转换。

来源：[长连接](https://developer.work.weixin.qq.com/document/path/101463)、[接收消息](https://developer.work.weixin.qq.com/document/path/100719)。

已知是明文时可以直接把它作为企业员工账号。未知类型时保留原值和未确认状态，不因转换失败就猜它是明文或不存在。

## 需要姓名、部门时的完整补全流程

1. **准备同一企业的自建应用**：取得 CorpID 与该应用 Secret；应用启用，目标成员在可见范围，并有读取成员所需权限。它们不是机器人 BotID/长连接 Secret。
2. **获取应用 access_token**：`GET https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=<CorpID>&corpsecret=<应用Secret>`。后台按企业/应用隔离缓存，以 expires_in 为准，并处理提前失效。不要每条消息都重新取 token。
3. **密文 ID 转换**：`POST https://qyapi.weixin.qq.com/cgi-bin/batch/openuserid_to_userid?access_token=<应用token>`，JSON `{"open_userid_list":["<消息里的原始ID>"]}`，每次最多 1000 个。
4. **读取转换结果**：成功映射在 `userid_list:[{open_userid,userid}]`，失败在 `invalid_open_userid_list`。官方明确：已经是明文 userid 的输入也会进入 invalid 列表。因此 invalid 既不能证明员工不存在，也不能单独证明输入是明文。
5. **读取员工资料**：对已确认明文 ID 调用 `GET https://qyapi.weixin.qq.com/cgi-bin/user/get?access_token=<应用token>&userid=<明文userid>`，获取权限范围内的姓名和部门信息。可见范围外的成员不能保证转换/查询成功。
6. **合并到身份补充层**：保留原始 sender_id，缓存经验证的员工 userid、可见姓名、department IDs、来源和更新时间；失败保留原值，不阻塞聊天。

官方接口：[获取 token](https://developer.work.weixin.qq.com/document/path/91039)、[机器人与自建应用身份转换](https://developer.work.weixin.qq.com/document/path/101521)、[读取成员](https://developer.work.weixin.qq.com/document/path/90196)。

部门返回的是 **部门 ID 数组，不是部门名称**；如果需要名称还需匹配相应可见部门目录。姓名/部门字段会受应用类型和权限限制，不能保证全部返回。新建自建应用通过普通读取成员接口不能直接取得手机、邮箱、头像等受限信息，涉及另行 OAuth 授权；当前“知道谁在聊”的目标优先只做员工账号、姓名和部门。

## 三类凭据不要混用

| 凭据 | 用途 | 不能替代什么 |
| --- | --- | --- |
| DeepFOS 测试环境登录 token/cookie | 平台接口鉴权、本地联调 | 不是企微 access_token，不能拿来调用身份转换 |
| 企微 BotID + 长连接 Secret | 机器人订阅长连接、收发消息 | 不是自建应用 CorpSecret |
| 企微 CorpID + 自建应用 Secret → access_token | 身份转换、权限内通讯录查询 | 不替代平台执行账号及平台权限 |

HTTP 回调 Token/EncodingAESKey 与当前长连接身份转换不相干，本次不引入回调配置。

## 建议落地范围

先做“按原始 sender_id 的归属记录/统计展示”，再加可选员工身份补全。原始 ID 已足以在限定渠道范围区分用户，不能把整个能力阻塞在通讯录配置上。

- 统计按账户/企业范围隔离，消息按外部 msgid 去重，并明确是否计入命令、失败消息与重试。当前 channel_message_runs 是消息处理记录基础，不等于已经有完整统计报表。
- 转换后将同一员工的原始 ID 与明文 ID 归到同一个身份，避免人数重复；不要仅按姓名合并。
- **保留既有 sender_id/peer_id 路由键**。直接将明文 ID 替换 peer_id 会改变 Binding，可能让已有私聊另开新会话。
- 姓名只是显示，员工身份不自动赋予 DeepFOS 权限。若未来要“每个员工使用自己平台权限”，需要独立的平台账号绑定和授权方案。
- 缓存与查询失败显示“尚未补全”，记录必要错误类别；不将应用 Secret、access_token 放进消息、模型上下文或日志。

实施前需要明确：机器人创建者是否超级管理员；只做统计展示还是要平台账号绑定；是否已有启用且对目标成员可见的自建应用。仅调研流程不需要用户现在提供 Secret。

## 精确源码入口

以下相对 deep-agent 根目录：

- `src/deep_agent/channels/adapters/wecom/__init__.py:22`：BotID/Secret。
- `src/deep_agent/channels/adapters/wecom/messages.py:40`：原始 sender_id；`:56` 私聊/群聊 peer_id；`:64` 元数据。
- `src/deep_agent/channels/service.py:67`：入站路由；`:97` claim_message；`:203` 发送者上下文；`:366` whoami。
- `src/deep_agent/db/stores/channel.py:201`：Binding；`:249` 平台 session_user_id；`:393` 消息发送者持久化。
- `src/deep_agent/channels/commands.py:13`：纯文本命令解析。
- `src/deep_agent/api/v1/channel_auth.py:44`：渠道内部鉴权与平台会话用户。
- `src/deep_agent/schemas/channel.py:22`：当前渠道配置，无自建应用身份补全字段。
- `src/deep_agent/api/v1/current_user.py:19`：平台 current-user 响应中的 user_id。

截至此版本，定向搜索未发现 openuserid_to_userid 或通讯录 user/get 补全链路。代码图谱存在旧根路径，本报告以当前 src/ 文件为准。
