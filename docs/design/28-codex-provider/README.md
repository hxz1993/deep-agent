# Codex 订阅 Provider

> 课题：`C10-CODEX` · 首次整理：2026-09-24 · 状态：设计，暂缓实施。
>
> 本文记录拟采用的结构，不表示 DeepAgent 已有线上 Codex 订阅授权功能。已完成的隔离调用与持久化验证见 [`TODO.md`](../TODO.md)。

## 结论与边界

复用现有 `llm_providers -> llm_models -> Agent / 请求级 model_id` 链路，不建立第二套订阅模型表，也不把令牌伪装成 `api_key`。每个部署环境使用自己的数据库和加密密钥；当前一个 Codex Provider 实例连接一个 ChatGPT/Codex 账号。同一环境以后需要不同账号时，可建立不同的 Provider 实例；多账号自动调度不在本课题范围。

`provider_type` 已经存在，用新值 `openai_codex` 区分现有使用 API Key 的 `openai`；另设 `auth_method` 表示凭据形式，使类型与认证方式各司其职。模型仍指向 Provider ID；运行时由该类型创建 Pydantic AI 的 `OpenAICodexProvider` + `OpenAICodexModel`，不经过现有 `OpenAIChatModel` 的 API Key 路径。现有对 `api_key` 的强制校验、可选模型判定、连接测试和 Console 表单均需按认证方式调整。

参考 sub2api：它在 `accounts` 行中分开存 `platform`、`type` 和 `credentials JSONB`；OpenAI/Codex 默认使用 `localhost:1455` 授权回调，管理页面允许粘贴完整回调 URL，由后台校验 `state` 并兑换令牌。它的数据库凭据持久化、刷新前重读与跨实例锁有参考价值；这套登录界面仍包含人工回填步骤，并非已经解决远端网页自动捕获本机回调。

## 数据设计

| 位置 | 字段 | 语义 |
| --- | --- | --- |
| 现有 `llm_providers` | `provider_type='openai_codex'` | 选择 Codex SDK 模型协议；`openai` 继续表示原 API Key Provider |
| 现有 `llm_providers` 新列 | `auth_method`：`api_key` / `oauth`，默认 `api_key` | 兼容已有记录，也可供其他订阅型 Provider 复用；`openai_codex` 首期只允许 `oauth` |
| 新表 `llm_provider_auth` | `provider_id` 主键并关联 Provider | 一个 Provider 一份当前授权；删除 Provider 时在同一事务删除关联凭据 |
| 新表 `llm_provider_auth` | `credential_json`，使用项目已有跨数据库 JSON 类型 | 含 `schema_version`、账号标识、加密后的 access/refresh token、必要的供应商专属字段；不同供应商可采用不同受校验的 JSON 结构 |
| 新表 `llm_provider_auth` | `revision`、`updated_at`，可选 `expires_at` | 刷新结果版本比较与状态展示；不能仅用过期时间推断上游授权仍有效 |

JSON 的灵活性只用于**凭据内部结构**。每种 `provider_type + auth_method + schema_version` 必须有明确校验和升级规则；不接受任意客户端 JSON 直接入库。Token 在应用层加密后才写入 JSON，解密密钥来自该环境稳定的部署 Secret，不写进数据库、镜像或仓库。一般 Provider 列表只返回授权状态和脱敏账号信息，不返回 `credential_json`；普通配置备份也不得携带明文令牌。

独立的一对一凭据表比直接给 Provider 主表加 JSON 更合适：刷新会频繁更换一次性 refresh token，能与管理员编辑名称、地址及模型关联分开；普通 Provider 列表、变更接口和通用备份也更容易排除秘密。`llm_models`、Agent 与会话表不需要新增列。

## 授权、调用与失效

1. 管理员在现有 Provider 页面新建“OpenAI Codex（订阅）”，授权区域显示“未连接／已连接／需重新连接”；模型仍在该 Provider 下新增与选择。连接和断开操作使用管理员权限的专用接口，不把令牌放进普通 Provider PATCH。
2. 首期可采用已完成隔离验证的授权链接＋完整回调 URL 人工回填：服务端暂存 PKCE verifier 和 `state`，核对回调来源与 `state` 后兑换令牌并加密入库。sub2api 采用类似方式。若后续追求全自动远端体验，可单独验证官方设备码授权（Beta，需账号或工作区启用）；当前未在 DeepAgent 实现或端到端验证。
3. Pydantic AI 的 `OpenAICodexCredentialSource.load/save` 对接该表。刷新前跨进程协调并重读最新凭据；成功后以 `revision` 比较写回新令牌，持久化失败时停止使用该轮刷新结果，避免单次使用的旧 refresh token 被重复消耗。运行进程须能观察到其他进程完成的授权更新。
4. 断开时清除该 Provider 的授权记录并使运行进程缓存失效；未连接、令牌失效或上游撤销时，该 Provider 下的模型不可用于新请求，并提供重新连接入口。不自动切到别的 Provider，也不删除已有模型、Agent 或历史。
5. 重新发布代码或重建容器后，只要该环境的持久数据库和解密密钥仍在，就复用授权。数据库记录丢失、密钥更换或上游撤销时需重新登录。浏览器 Cookie 不承担服务运行时的凭据保存。

订阅余额、余量与价格展示不从 OAuth 令牌存在与否推断，也不作为首期“已连接”的前提；有稳定上游接口和真实账号验收后再另列功能。

## 迁移和验收

需要一次数据库迁移：扩展 `llm_providers.auth_method` 并创建一对一凭据表，旧 Provider 回填 `api_key`。当前仓库的 Alembic Head 为 `007`；若某环境已执行 `007`，必须追加新 Revision，不能改写已执行的迁移。若确认发布链路尚未执行该 Revision，是否合并到发行中的 V7 由当时的部署状态决定；不在设计阶段改迁移文件。

验收至少覆盖：现有 API Key Provider 回归、独立授权与重启复用、刷新令牌轮换与多进程竞争、断开后不复活、模型选择与工具循环、失效/额度不足/模型不可用、Console 和 API 脱敏、数据库备份与恢复。现有本机验证尚未覆盖真实远端网络、刷新竞争与正式部署。

## 依据

- [Pydantic AI OpenAI Codex 文档](https://pydantic.dev/docs/ai/models/openai-codex/)：OAuth 登录、固定本机回调、凭据源 `load/save` 和刷新令牌单次使用。
- [sub2api 账号表](https://github.com/Wei-Shaw/sub2api/blob/a3eb7ef302961cba716dc78b39b93b60c467db0e/backend/migrations/001_init.sql#L60-L74)、[OpenAI 授权服务](https://github.com/Wei-Shaw/sub2api/blob/a3eb7ef302961cba716dc78b39b93b60c467db0e/backend/internal/service/openai_oauth_service.go#L44-L169)、[统一刷新入口](https://github.com/Wei-Shaw/sub2api/blob/a3eb7ef302961cba716dc78b39b93b60c467db0e/backend/internal/service/oauth_refresh_api.go#L159-L168)。
- [OpenAI 官方无界面登录说明](https://learn.chatgpt.com/docs/auth#login-on-headless-devices)：设备码授权目前为 Beta，需先开启相应权限。
- DeepAgent 现有结构：[Provider 与模型分层](../19-model-provider-architecture/README.md)、`src/deep_agent/db/models.py`、`src/deep_agent/models/provider_registry.py`。
