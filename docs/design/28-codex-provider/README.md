# GPT 订阅 Provider：本机实验版

> 课题 `C10-CODEX`；2026-09-25 实施。订阅 Provider 总开关 `DEEP_AGENT_ENABLE_SUBSCRIPTION_PROVIDER` 默认关闭（2026-10-12恢复；Alpha订阅连接未通过），目前仅接入 ChatGPT；旧变量 `DEEP_AGENT_ENABLE_CODEX_PROVIDER` 仍兼容，新变量优先。本机账号已由使用者完成授权；远程部署和自然到期刷新仍需另行验收。

## 产品边界

保留 Provider → Model 两层、两张表。Console 新建供应商先选 API／订阅；API 沿用原配置，订阅首期只有 ChatGPT，内部类型 `openai_codex`。订阅的授权链接由 Pydantic AI `OpenAICodexOAuthFlow` 生成；在新标签登录后，把浏览器中的完整 `http://localhost:1455/auth/callback?...` 地址粘回同一弹框，验证成功后才保存供应商。没有本机回调监听，不读取 Codex CLI 登录文件。编辑时可改名称、重新授权或断开；重连保存前旧凭据不变。

模型复用 OpenAI 的 models.dev 目录及手动添加，目录仅供参考，不保证账号支持每个型号。模型用途 `purpose` 旧行与旧请求默认 `chat`；本期只允许对话，图片生成、JEV、思考强度均不开发。Agent、请求级模型选择与原有 SSE／历史／工具链沿用现有路径。

## 数据与兼容

| 表 | 新列 | 说明 |
| --- | --- | --- |
| `llm_providers` | `auth_method` | 非空，旧行默认 `api_key`；订阅为 `oauth` |
| `llm_providers` | `credential_json` | 跨数据库 JSON，版本化保存 access／refresh token 和 account_id；只通过授权流程写入 |
| `llm_providers` | `auth_revision` | 非空默认 0；重连、断开、按需刷新时递增，阻止旧刷新写回 |
| `llm_providers` | `auth_status` | 非空默认 `unconfigured`；订阅使用 `connected`／`disconnected` |
| `llm_models` | `purpose` | 非空默认 `chat`；当前只开放对话 |

当前已发布实现中，OAuth 凭据与现有 API Key 一样**明文存储**，不增加加密密钥或部署变量。列表、详情和日志不得返回凭据；仅返回认证状态和账号末四位。数据库物理备份包含明文凭据，必须按现有数据库凭据管理方式保护。普通配置导出将订阅凭据清空，导入后需重新授权；配置导入仍要求同版本、同结构，不增加旧备份转换。

这五列并入尚未发布的 `007`，不增加 `008`。从 `006` 升级运行 `007`；已经标记旧 `007` 的本机数据库在受控启动时按实际列检查补齐，重复运行不改数据。现有 API Provider、Key、URL、模型引用、会话和附件不迁移；本机升级前保留物理备份。

## 授权与运行

授权临时流程只在 API 进程内存中保存 10 分钟，绑定管理员及目标供应商草稿。完整回调地址必须匹配 SDK 固定回调的 scheme、host、端口、路径与 state；验证成功才临时持有凭据，保存时在单一数据库事务中写入。取消、超时、重启或保存后使流程失效，Token 不进入浏览器存储。普通 Provider PATCH 不接收 OAuth JSON。

运行使用 `OpenAICodexProvider`、`OpenAICodexCredentialSource`、`OpenAICodexModel`。同一进程内同一 Provider 共享 SDK Provider 和刷新锁；SDK 按需刷新，凭据源定向更新 JSON，`auth_revision` CAS 防止断开或重连后的旧写回。实验功能关闭时隐藏新建订阅并拒绝授权与新调用，仍可查看、断开或删除已存在的订阅配置。首版使用本机文件锁限制单运行所有者，不支持多 worker、多副本或 API/Gateway 两进程同时消费同一订阅。

`connected` 只表示本地保存了授权，不证明远端权限、余额或具体模型可用。授权失效、模型拒绝、额度不足明确报错，不自动切换模型。真实授权后再验证模型连接、流式对话、多轮、工具、取消、历史、标题、压缩、轨迹和 Usage；模拟测试不能替代真实联调。

## 外部参考

- [Pydantic AI Codex](https://pydantic.dev/docs/ai/models/openai-codex/)：授权链接、固定回调、凭据源及按需刷新。
- [sub2api Account Schema](https://github.com/Wei-Shaw/sub2api/blob/main/backend/ent/schema/account.go)：平台、认证类型和结构化凭据同表保存的参考；DeepAgent 不引入其账号池与调度。
- [后续方向](../TODO.md)：思考强度与图片生成目前归档为有潜力、暂不实施。

2026-09-26：用户将统一凭据加密列为后续高优先级，见 [31 课题](../31-credential-encryption/README.md)。这是待实施改造，不改变本文对当前明文存储的描述。
