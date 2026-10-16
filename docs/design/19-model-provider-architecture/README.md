# Provider 与模型配置分层

## 专项研究

- [决策 API 与轻量路由研究](decision-api-research.md)：2026-10-14 核验快照，包含 OpenAI Decisions、Jev、Strom 的格式、上下文、价格、评价与本地连通性；尚未接入产品，后续采用前须重新核验。

## 结论

DeepAgent 自 v0.5.6 起完成模型接入信息与模型事实的归一化分层，统一采用以下层级：

```text
模型目录 Catalog（候选数据）
  -> Provider（地址、密钥、Pydantic AI 适配类型）
    -> Model（模型编码、模态、上下文与能力）
      -> Agent / 请求级模型覆盖（继续引用 model_id）
```

Provider 是管理员创建的本地运行实例，不等同于 models.dev 的 Provider 编码。同一种类型可以创建多个实例，例如“公司 Kimi”和“测试 Kimi”。Agent、Chat、Session、History、SSE 继续使用既有 `model_id` 协议，不增加自动路由、故障转移或新的凭据服务。

## 首批 Provider 类型

类型顺序由后端注册表唯一维护，Console 只消费接口结果：

| 顺序 | 编码 | 显示名称 | Pydantic AI Model | 默认地址能力 |
| --- | --- | --- | --- | --- |
| 1 | `deepseek` | DeepSeek | `OpenAIChatModel` | 支持默认和自定义 |
| 2 | `moonshotai` | MoonshotAI（Kimi） | `OpenAIChatModel` | 支持默认和自定义 |
| 3 | `alibaba` | Alibaba（Qwen / DashScope） | `OpenAIChatModel` | 支持默认和自定义 |
| 4 | `zai` | Z.AI（智谱 GLM） | `OpenAIChatModel` | 支持默认和自定义 |
| 5 | `openrouter` | OpenRouter | `OpenAIChatModel` | 支持默认和自定义 |
| 6 | `openai` | OpenAI | `OpenAIChatModel` | 支持默认和自定义 |
| 7 | `anthropic` | Anthropic | `AnthropicModel` | 支持默认和自定义 |
| 8 | `google` | Google Gemini | `GoogleModel` | 支持默认和自定义 |
| 9 | `openai_compatible` | OpenAI Compatible | `OpenAIChatModel` | 仅自定义地址 |

`provider_type`选择框架适配器，不代表实例名。DeepSeek、MoonshotAI、Z.AI 和 OpenRouter 使用自定义地址时，改用 Pydantic AI 原生 `OpenAIProvider(base_url=...)`；Alibaba、OpenAI、Anthropic 和 Google 使用各自原生 Provider 的地址覆盖能力。新运行不再通过模型前缀或 URL 猜协议。

## 数据结构

Alembic `007`新增 `llm_providers`：

```text
id                    主键
name                  唯一实例名称
provider_type         注册表类型编码
use_default_base_url  是否使用框架默认地址
base_url              明确持久化并回显的当前地址
api_key               沿用现有数据库密钥存储方式
created_at / updated_at
```

`llm_models`新增带索引和`RESTRICT`外键的 `provider_id`，并删除原有
`base_url`、`api_key`。Provider被模型引用时不能删除，类型也不能直接修改。模型名称只要求在同一
Provider内唯一，因此两个Provider可以配置同名模型。模型是否进入Run只由Agent或请求级`model_id`
选择，并要求模型自身处于启用状态；Provider是否可用由密钥和地址等真实连接配置决定，连接事实只
存在Provider一处。Alembic 010删除了Provider和模型的旧启停列，Alembic 011仅恢复模型级
`enabled`，Provider仍无启停层。

普通`GET /llm-models`只返回`selectable=true`的当前可用模型；管理Console必须显式传
`selectable_only=false`读取全部模型。`selectable`同时要求模型启用和Provider配置有效，失败原因
区分`model_disabled`、`provider_unconfigured`和`provider_invalid`。停用模型不会删除已有Agent
引用或触发自动换模，但新选择、请求覆盖和实际运行均失败关闭。

### 默认地址语义

不使用 `NULL`表达默认地址：

- `use_default_base_url=true`：`base_url`仍保存当前固定版本的官方地址并供 Console 回显；运行时不把它作为覆盖参数传给 Pydantic AI。
- `use_default_base_url=false`：`base_url`必须是明确的 HTTP/HTTPS 地址，并作为原生 Provider 的地址覆盖。
- `openai_compatible`：不支持默认地址，地址始终必填。

## 迁移与回退

006 升级 007 时，迁移只在历史数据迁移阶段根据模型前缀、规范化地址和实际连接信息推断 Provider。相同类型、地址和密钥的模型共享一个 Provider；任一项不同都不会合并。官方地址标记为默认地址，无法可靠识别的 OpenAI 风格端点归入 `openai_compatible`。迁移完成前必须确认每个模型都有有效 `provider_id`，否则事务失败。

007 降级 006 时，先恢复模型的 `base_url/api_key`列，再把Provider当前地址和密钥回填到每个模型，
最后删除外键、索引、`provider_id/enabled`和Provider表。因此最新连接配置可供旧版本继续运行。
由于006要求模型名称全局唯一，若007数据已经在不同Provider下使用相同名称，降级会明确失败，避免
静默改名或丢失配置。

未发布迁移只在 Test 配置库的隔离 SQLite 副本执行。共享 Test 数据库不得提前升级；备份、SQLite、`data/`和 `TMP_to_delete`文件均不提交。

## API

Provider 类型目录：

```http
GET /api/v1/llm-provider-types
```

Provider 管理：

```http
GET    /api/v1/llm-providers
POST   /api/v1/llm-providers
GET    /api/v1/llm-providers/{provider_id}
PATCH  /api/v1/llm-providers/{provider_id}
DELETE /api/v1/llm-providers/{provider_id}
```

响应只返回 `api_key_set`，永不返回原始密钥。PATCH省略 `api_key`表示保留；替换使用新值；清除
必须显式发送 `clear_api_key=true`。清除密钥不会修改子模型；模型配置保持原状并进入
`provider_unconfigured`不可用状态，补回密钥后自动恢复。

模型创建和更新接收 `provider_id`，管理接口不再通过模型修改地址或密钥。模型响应增加Provider摘要，
管理响应保留从Provider派生的 `base_url`和 `api_key_set`兼容字段；`selectable`表示当前有效可用性，
`unavailable_reason`只返回模型停用、Provider未配置或配置无效等脱敏原因。
`GET /llm-models`支持可选 `provider_id`过滤，默认只读取可选模型；管理界面显式传
`selectable_only=false`读取包括停用模型在内的完整目录。
原有核心字段与 `model_id`语义不变。

2026-10-17业务权限拆分：默认模型列表和单模型详情允许可信登录用户读取，不要求管理员；
业务投影不返回`base_url`、Agent引用数及创建更新时间。`selectable_only=false`完整管理列表，
模型增删改、连接测试、目录和Provider管理继续校验管理员。指定Agent读取同样显式要求可信身份。
详见[权限指南](../../guides/console-admin-access.md)。

## 运行时

运行时链路为：

```text
LlmModel + LlmProvider
  -> 校验外键、密钥和地址
  -> 按 provider_type 选择 Pydantic AI Provider
  -> 构造 Pydantic AI Model
  -> 交给现有 Agent Factory
```

Pydantic AI继续负责 SDK客户端、认证头、默认地址、Provider Profile、流式响应、取消和错误转换。
DeepAgent只负责持久化、授权、引用校验、显式工厂选择、脱敏和观测。Provider缺失、缺Key或地址
无效时在请求前失败，不自动切换或重新猜测。

普通Agent、模型覆盖、Prompt估算、上下文测试、标题生成均使用同一解析器。首轮标题请求使用该轮实际生效模型；压缩摘要固定复用当前Agent已解析的主模型。二者都不提供独立模型配置。Catalog只提供预填数据，不参与运行时关联。

## Console

配置中心Provider页面采用轻风格左右分栏：左侧展示品牌标识、实例、类型、密钥状态和模型数量；
右侧管理默认地址、明确地址回显、密钥和关联模型。新建Provider不自动创建模型。

Provider详情中的模型采用固定父子关系：新建和编辑时不允许切换Provider，名称兼容字段由运行模型
编码同步生成；关联列表直接展示运行模型编码。模型选项只有在Provider连接配置完整时才可新选。
模型也可由目录预填；目录不会修改Provider凭据。Agent、Console Chat等
模型选择器按Provider实例分组，option值和请求参数始终仍是 `llm_model.id` / `model_id`。

DeepCube和DeepTable客户端读取的模型核心字段和发送的`model_id`保持兼容，不要求改造；普通列表请求
天然只返回当前可用模型。只有管理界面应显式读取全量列表并识别`selectable=false`，不能把Provider ID
当作聊天请求的`model_id`。

## 安全与非目标

- Token、Cookie、Provider Key及其摘要不得进入响应、日志、Console DOM、错误或轨迹导出。
- Provider 管理接口继续使用 Console 管理员鉴权；普通聊天接口不受额外保护。
- 本轮不增加自动路由、负载均衡、故障转移、Key轮换、Credential Service、插件体系或新的密钥加密策略。
- 本轮不修改 Chat、SSE、History、Planning、MCP、Deferred、附件和数据库会话结构。
