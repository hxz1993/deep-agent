# Codex 订阅 Provider

> 课题：`C10-CODEX` · 首次整理：2026-09-24 · 设计更新：2026-09-25。
> 状态：本机实验设计，未实施。用户偏好在现有 Provider 表加字段，少加表。
> 本版替代同日早先的独立授权附表建议；隔离验证见 [TODO](../TODO.md)。

## 决策与范围

保留 Provider → Model 两层、两张表。在 `llm_providers` 增加认证相关字段，不建立 `llm_provider_auth`。现有 `llm_models` 不改。一个 Provider 记录仍表示一套接入配置与账号凭据，多个模型共用该配置。

首期仅支持本机实验，建议 `DEEP_AGENT_ENABLE_CODEX_PROVIDER=false` 默认关闭、本机 Profile 显式开启；不增加全局实验开关。本文为设计，尚未修改代码、配置或数据库。图片生成和 JEV 仅用于检验结构可扩展性，不在本期实施。

## 自动刷新不是用户反复授权

用户在浏览器登录授权，取得用于请求的 access token 和用于换取新凭据的 refresh token。后续正常请求复用有效 access token。当前 Pydantic AI 2.46.0 在请求路径检查临近过期，也会在鉴权失败路径尝试刷新和重试；成功后通过 CredentialSource.save 持久化新凭据。失效、撤销或无法刷新时，才需要用户再次授权。

因此不是每个请求、每个流式片段都刷新，不是后台不停写库，也不能承诺固定几小时或几天刷新一次；以实际凭据和 SDK 行为为准。前版“持续刷新／频繁更新”容易误导，现改为“使用过程中按需自动刷新”。这一事实不构成必须拆表的理由。

refresh token 轮换要求保存最新结果；并发控制无论同表或附表都需要，拆表本身不能解决并发。

## 三个概念分别表达

| 维度 | 位置 | 示例／作用 |
| --- | --- | --- |
| 供应商与接入协议 | 已有 provider_type＋后端类型注册表 | openai、openai_codex、typesafe；选择相应 SDK 适配器和合法地址 |
| 认证方式 | 新 auth_method | api_key、oauth；决定配置表单、凭据读取和校验方式 |
| 模型用途／调用能力 | Model 层，未来需要时扩展 | 对话、专用图片生成等；不能与认证方式混成一个 Provider 类型 |

“订阅”是产品收费或账号来源描述，不是通用认证协议。Console 可以称“Codex 订阅授权”，数据库使用 oauth，不引入 api/subscription/image 混合枚举。provider_type 继续保留 openai_codex，因为其请求协议与普通 OpenAI API 不同；auth_method 与 provider_type 的合法组合由注册表校验，不是允许任意搭配。

当前 Codex 固定 oauth，旧类型默认 api_key。与前版省略 auth_method 相比，本版为用户希望明确区分认证形式而增加此列；不再另外添加 billing_type、is_subscription 或一组含义重叠的字段。

## Provider 主表新增字段

| 字段 | 类型与默认 | 用途 |
| --- | --- | --- |
| auth_method | String，非空，默认 api_key | 旧行及旧接口缺省沿用 API Key；Codex 后端固定 oauth，不向旧客户端新增必填参数 |
| credential_ciphertext | 可空 Text | 加密后的版本化 JSON，含 schema_version、access_token、refresh_token、account_id；禁止通用 PATCH 写入 |
| auth_revision | 整数，非空，默认 0 | OAuth 授权、刷新、断开时递增；旧写回条件校验，不影响普通名称编辑 |
| auth_status | String，非空，默认 unconfigured | OAuth 使用 connected／reauth_required／disconnected 等状态；API Key 类型继续从 api_key 派生对外状态，不双写一个新的 Key 状态源 |

现有 api_key 保留，旧记录不迁移至 JSON；oauth 行必须 api_key=NULL，api_key 行不得同时保留 OAuth 密文。不把订阅 Token 塞进 api_key。凭据格式版本在加密 JSON 内，不另加 schema_version 列。

Codex 使用已有 use_default_base_url=true 和注册表默认地址，base_url 非空约束不变，不开放自定义订阅请求地址。模型、Agent 和会话不增加字段。类型或认证方式的变更受模型引用及授权约束限制，首期跨 API Key／Codex 接入建议新建 Provider，避免已有模型引用改变协议。

密钥由环境外置配置提供且长期稳定，不进库或仓库。当前仓库没有已核验的通用凭据加密模块，实施时补独立凭据服务；只读响应不返回 Token 或密文。connected 仅代表本地已有授权，不保证远端授权、额度或模型访问权。

## 同表如何避免互相覆盖

- 普通配置服务只更新用户提交的名称、地址等业务字段，不整行覆盖，也不接受 credential_ciphertext/auth_revision/auth_status。
- 凭据服务只更新 OAuth 字段，以 Provider ID、认证方式和旧 auth_revision 做条件更新。修改名称不让凭据刷新误判失效；断开、重连递增 revision，拒绝旧刷新结果复活凭据。
- SDK Provider 实例按本机运行所有者复用；当前 SDK 的锁是实例内锁。revision 只能拒绝旧写入，不能阻止两个进程消费同一个 refresh token。
- 本机首期限定并检查同一 Provider 单运行所有者，不宣称支持多 worker、API/Gateway 同时调用同一授权或多副本。未来开放这些场景前补实际刷新过程的跨进程互斥，不需要为互斥改变此表方案。
- 刷新保存失败明确报错；断开清空密文、写状态并递增 revision，淘汰实例。新请求检查授权状态；已经发出的上游请求不保证能撤回。
- 普通 updated_at 可能随授权更新变化；不为此再加时间列，需要时通过明确事件或界面文案区分更新时间。

## sub2api 参考核验

2026-09-25 未在本机 PycharmProjects 常用目录范围找到 sub2api，直接查阅官方 GitHub main 源码：

- `backend/ent/schema/account.go`：accounts 同表保存 platform（平台）、type（api_key/oauth/cookie）、credentials JSONB 和 extra JSONB。认证形式决定 credentials 内容，并未为 OAuth Token 单独拆授权表。
- `backend/internal/service/openai_token_provider.go`：请求先读 Token 缓存；按到期信息决定是否刷新，不是每个模型请求都刷新。
- `backend/internal/service/oauth_refresh_api.go`：锁内重读账号、再次判断、调用平台刷新、写入新凭据及版本。它还有账号池、调度、限流等职责，不整体照搬到 DeepAgent。

参考的是“平台／认证类型／结构化凭据分开表达”和定向更新，不据字段定义推断其凭据已加密；本方案自行要求密文存储。sub2api 使用 PostgreSQL JSONB，我们沿用项目跨数据库能力，密文用 Text，不引入 PostgreSQL 专用类型。

## JEV 与未来图片生成如何兼容

JEV 是 TypeSafe 平台的模型。当前 Pydantic AI 已有 TypeSafeProvider(api_key, base_url) 和 TypeSafeModel，其协议适配与 OpenAI 不同，但认证仍是 API Key。未来新增 provider_type=typesafe 并注册适配器即可，不需要再加一种认证字段或新表；实际依赖和功能限制届时验证。

图片生成也不应另造“图片认证”。例如同一 OpenAI Provider 的 API Key，可供对话模型与 OpenAIImageGenerationModel 使用，SDK 只是模型执行器不同。不同账号、地址或 Key 则新增普通 Provider 记录。

真正需要扩展的是模型用途和运行路由：恢复图片课题后，评估在 llm_models 增加用途字段（旧行默认 chat）或能力声明，区分专用 image_generation 与 chat，确保图片专用模型不进入 Agent 主模型列表。能输出图片的对话模型与专用图片接口也需区分，不能仅凭输出模态判断。图片尺寸、质量、格式、编辑输入和计费记录应在图片调用链设计，不塞进 Provider 凭据 JSON。本期不添加这些模型字段、表单或执行器。

## 运行与管理链路

1. 后端 Provider 类型注册表声明 auth_method、协议和实验门禁；关闭时隐藏新建入口并拒绝创建、授权和新调用，已有记录允许查看、断开、清理。
2. Codex Provider 可先保存为未授权；管理员通过独立接口发起 OAuth、回填完整回调 URL、断开。PKCE/state 限时、一次性并绑定管理员和 Provider，未完成流程重启后重新发起，不建立临时流程表。
3. 显式 CredentialSource 从 Provider 行解密读取并定向保存，构建 OpenAICodexProvider＋OpenAICodexModel；不回退读取 Codex CLI auth.json。
4. 模型可用性改为按认证方式判断，保留旧 API Key 行为；明确授权失效要求重连，普通网络错误不直接判定为撤销。不开启思考强度课题。

## 迁移、备份与验收

实施时追加 Migration，给现有 Provider 表加上述四列；不改已发布 007，不新增授权表，不迁移旧 Key，不变更 Model。当前仅设计，无 DDL 执行。

同表需要列级备份规则：普通配置导出保留 auth_method，但清除 OAuth 密文和运行状态，导入 OAuth 行成为未授权；不能继续无差别复制整行。覆盖导入应与凭据服务协调，递增目标 revision／淘汰旧实例，不能按备份里的 revision 回滚计数或留下旧授权。物理备份与正常重启可保留授权，但需要同环境解密密钥；旧 refresh token 快照可能已失效。

验收范围：旧 API Key 与配置包兼容、服务端实验门禁、独立授权与重启复用、单所有者、名称编辑与刷新不互相覆盖、断开／重连后的旧写回拒绝、保存失败、工具循环、凭据脱敏、列级导入导出及迁移往返。当前隔离验证不等于正式链路已完成。

## 依据

- [sub2api Account Schema](https://github.com/Wei-Shaw/sub2api/blob/main/backend/ent/schema/account.go)
- [sub2api OpenAI Token Provider](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/openai_token_provider.go)
- [sub2api OAuth Refresh](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/service/oauth_refresh_api.go)
- [Pydantic AI Codex](https://pydantic.dev/docs/ai/models/openai-codex/)、[TypeSafe/JEV](https://pydantic.dev/docs/ai/models/typesafe/)、[图片生成](https://pydantic.dev/docs/ai/image-generation/)
- 当前源码：src/deep_agent/db/models.py、services/llm_configuration.py、models/provider_registry.py、db/configuration_transfer.py。
