# 企微应用 Webhook 渠道

“企微应用”是独立的 `wecom_app` / `webhook` 渠道。原有 `wecom` / `websocket` 仍表示企微智能机器人。两者接收协议、凭据与发送方式不同，不能共用机器人消息解析器。

## 配置顺序

1. 在「渠道 → 企微应用」创建企业应用，填写 CorpID、企微 AgentID 和 App Secret。
2. 在「渠道账户」新建「企微应用」，选择已有应用、关联 Agent 和会话归属的平台用户。
3. 填写企微官方定义的 **Token**、**EncodingAESKey**。Token 为 3–32 位字母或数字，EncodingAESKey 为 43 位 Base64 字符并解码为 32 字节。
4. 保存成功后弹框保留，显示完整 Channel ID 和回调地址；首次保存之前不预分配 ID。再次编辑保持同一地址。
5. 将回调地址复制到企微后台「接收消息」配置。GET 验证成功后再启用业务消息处理。

已关联其他渠道的应用会显示提示，仍允许重复关联。删除应用前必须解除渠道引用。应用关联同时确定发送身份与持久化成员资料来源；复用该应用的成员缓存，按明文 `FromUserName` 查询资料，显示和统计共用这一来源。

## 地址与网关转发

Console 复用 `serviceUrl()`，从当前 Console 的组件根路径拼接 `api/v1/webhooks/channels/{channel_id}`，保留网关组件前缀，无需环境变量。

Alpha 展示 `https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/api/v1/webhooks/channels/{channel_id}`；本地展示 `http://127.0.0.1:8000/api/v1/webhooks/channels/{channel_id}`。直接使用组件回调入口，不依赖额外短路由映射。

GET 成功返回解密后的 `echostr` 原文。POST 正常接收入队后返回 `success`；这只表示接收成功，不表示模型或应用回复已经完成。其他渠道类型不使用这个入口，返回 `webhook_not_supported`。渠道不存在、签名失败、入队失败分别返回明确错误，不能为了联调统一返回 200。

## 配置与升级

数据库迁移 **011** 在 `channel_accounts` 新增可空 `webhook_token`、`webhook_aes_key` 两个 TEXT 字段，并将 `bot_id`、`secret` 改为可空。仅新应用渠道不使用 Bot 凭据；旧渠道仍要求完整 Bot ID/Secret。不新增表、索引、外键和 Runs 字段，不重写历史来源。

应用引用保存于 `settings.wecom_app.application_id`；CorpID、企微 AgentID、App Secret 仍读取 `wecom_applications`。普通管理响应只返回 Webhook 密钥的配置状态和掩码。编辑不提交或提交空字符串表示保留；实际新值才替换；不允许将掩码保存为密钥，也不允许清除必填值。

配置备份包含凭据，按敏感备份保护。备份导入需匹配数据库版本和物理结构，并检查应用逻辑引用。迁移可能涉及目标数据库改列锁或 SQLite 重建，生产耗时必须按目标数据库及表规模评估。011 不提供自动无损降级；回退需匹配的迁移前备份和代码。

## 运行与统计

消息通过独立适配器转成既有渠道入站消息，复用 Binding、Runs、Agent 执行及附件处理。图片、语音和文件能力依赖对应消息是否由企微应用实际投递以及现有模型/附件能力；附件下载成功不代表所有文档内容均可解析。媒体不会通过任意外部 URL 下载。

新会话来源固定为 `channel:wecom_app`，来源 ID 为渠道账户 ID。提问和用量以实际 Events 为准，Runs 提供真实发送者和运行/投递状态。GET 验证、重复投递、控制命令不增加提问；Console 续问计入会话总量，但单列为无渠道发送者，不归到原用户名下。全局平台用户及模型用量口径不改变。

沿用内存队列；不新增持久化消息正文和重启恢复。应用回复不模拟机器人 WebSocket 流式卡片。发送失败不重新执行模型，结果不确定时不盲目重发。

## 联调排障

先在 API 日志查请求到达，再按 request ID、渠道 Run ID、Session ID、Agent Run ID 追踪验签、解密、接收、媒体、模型与发送阶段。若没有入口日志，检查公网网关访问日志及上游状态。

联调阶段保留 INFO 级阶段日志。日志禁止记录 Token、EncodingAESKey、App Secret、access_token、完整 Query、原始 XML、消息正文和文件内容。只看 HTTP 200 不足以证明企微回复成功，还需要核对发送接口业务结果和 Run 状态。

API 的 Uvicorn 访问日志会去掉 Webhook Query。公网网关仍需单独配置：记录方法、路径（Nginx `$uri`）、状态、耗时和关联 ID，不记录包含查询参数的 `$request` / `$request_uri`、请求体或鉴权凭据。网关转发时必须保留这些参数，日志脱敏不能改变转发内容。

当前入口单进程最多并行处理 32 个请求，单次入口处理超时 4 秒，请求体上限 256 KiB，签名时间窗口为前后 10 分钟。公网网关应另设总体流量限制；这些应用内限制不代替边缘防护。尚未验证的匿名请求日志有速率预算，超出后记录抑制数量，避免日志洪泛。

语音复用附件链路：AMR 和可识别的 Ogg/Speex 通过 FFmpeg 转为 WAV；无法识别的裸 Speex 返回明确提示。部署须提供 FFmpeg（现有 API/Gateway 镜像已安装）。图片与文件使用回调 MediaId 下载，受既有附件大小、格式及模型能力限制。文件消息解析和入站处理已实现，但企微应用是否实际投递该类型仍需现场验证，不能把人工构造的回调测试当成平台支持承诺。

本期向企微应用发送最终文本回复，按 UTF-8 字节分段；不提供机器人式流式卡片，也不新增图片、语音或文件的出站发送功能。
