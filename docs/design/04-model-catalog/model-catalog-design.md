# DeepAgent 模型目录与运行属性设计

> 状态：已实现
>
> 更新日期：2026-07-30
>
> 数据库迁移：v0.3.0 完整基线 Alembic `001`
>
> 关联设计：[`上下文压缩`](../03-context-compaction/context-compaction-design.md)

## 1. 结论与边界

0.2.2 把“模型选型目录”和“Agent 实际运行配置”明确拆成两层：

```text
models.dev
  └─ 管理员手工更新
       └─ model_catalog
            └─ 选择 Lab / Model / Provider 并预填
                 └─ llm_models
                      └─ Agent 运行时唯一事实源
```

- `model_catalog` 只用于浏览与预填，不是运行时依赖。
- `llm_models` 保存管理员最终确认的调用编码、地址、密钥、输入模态、上下文、最大输出和能力标签。
- 两张表没有外键，目录刷新不会修改已有 `llm_models`。
- 客户私有模型可以完全绕过目录手工创建。
- 不保留生产运行使用的目录 JSON，不做启动同步、定时同步或 Agent 请求时联网。
- 首版只接 models.dev，不接 OpenRouter。
- 不增加目录状态表、版本表、刷新历史、`catalog_key` 或恢复命令。

## 2. models.dev 三维数据

models.dev 的数据不是简单的 Provider × Model 平铺：

- Lab：模型研发方，例如 OpenAI、Anthropic、DeepSeek。
- Model：与接入渠道无关的标准模型，例如 `anthropic/claude-*`。
- Provider：实际提供调用服务的渠道，例如 Anthropic 官方。

刷新读取 models.dev 官方组合目录，并可选补充 Lab 页面信息：

| 来源 | 用途 | 是否阻断刷新 |
| --- | --- | --- |
| `https://models.dev/catalog.json` | 同时提供标准 Model 与 Provider，包含 Lab 归属、Provider 实际模型编码、能力和覆盖值 | 是 |
| `https://models.dev/labs` | Lab 名称、描述和公开链接补充 | 否 |

Lab 页面不可用时，可从标准模型 ID 前缀推导 `lab_id/lab_label`；标准模型目录或 Provider
对象缺失或非法时，整次刷新失败。不再额外请求 `api.json`，避免同一轮刷新混用不同生成
时点的两份 Provider 数据。

标准化顺序为：

```text
获取完整 catalog
→ 建立标准 Model 索引
→ 过滤 Provider 白名单
→ 解析 Lab / Model / Provider 关系
→ 排除废弃、非文本聊天模型
→ 合并 Provider 覆盖值
→ 校验 Token、模态、能力和主键
→ 生成数据库行
```

页面与接口按 `release_date` 倒序展示；日期缺失的模型排在有明确发布日期的模型之后。

## 3. Provider 白名单

上游目录获取完整后，后端只保留代码白名单内且 DeepAgent 已具备运行适配的常见渠道：

```text
OpenAI
Anthropic
Google
DeepSeek
Alibaba
Moonshot AI
Zhipu AI
MiniMax
xAI
```

第一版不展示 OpenRouter、Azure、Bedrock、Vertex、各类 Gateway、Coding Plan、Token Plan
和长尾聚合渠道。白名单是代码常量，不进入数据库或环境变量。

运行适配类型保存在目录的 Provider 扩展元数据中：

```text
openai
anthropic
google
deepseek
openai_compatible
anthropic_compatible
```

目录选择最终生成 Pydantic AI 可使用的 `runtime_model` 和默认 `base_url`；管理员保存时仍可修改。

## 4. 数据库设计

### 4.1 `model_catalog`

只建一张表。一行代表“某个标准模型通过某个 Provider 提供”，复合主键为：

```text
provider_id + provider_model_id
```

主要字段：

| 分组 | 字段 |
| --- | --- |
| Provider | `provider_id`、`provider_label`、`provider_model_id`、`provider_npm`、`provider_api_url`、`provider_doc_url` |
| Lab | `lab_id`、`lab_label`、`lab_description` |
| Model | `canonical_model_id`、`model_label`、`model_description`、`family`、`release_date` |
| 标准能力 | `canonical_context_window_tokens`、`canonical_max_output_tokens` |
| 渠道生效能力 | `context_window_tokens`、`max_output_tokens`、`input_modalities`、`output_modalities`、`capabilities`、`status` |
| 可扩展元数据 | `lab_metadata`、`model_metadata`、`provider_metadata` |
| 来源与时间 | `source`、`refreshed_at`、`created_at`、`updated_at` |

`release_date` 单独保存用于稳定倒序分页；三份扩展 JSON 继续兼容 models.dev 后续增加链接、权重、实验状态、推理选项、
基准信息等属性，避免每个非核心展示字段都升级表结构。

明确不保存：

- `cost` 及其任意层级的价格数据。
- API Key、Token、Cookie。
- 本地路径或运行时连接状态。
- 目录同步历史和版本。

### 4.2 `llm_models`

完整基线 `001` 包含：

```text
context_window_tokens  BIGINT NULL
max_output_tokens      BIGINT NULL
tags                   JSON/TEXT NULL
```

字段语义：

| 字段 | 语义 |
| --- | --- |
| `context_window_tokens` | 模型输入和输出共享的总窗口 |
| `max_output_tokens` | Provider 声明的单次最大输出能力上限 |
| `tags` | 推理、工具调用、结构化输出等能力标签 |

`input_modalities` 与 `tags` 必须分开：

- 模态：`text/image/audio/video`，描述模型原生输入类型。
- 能力标签：`reasoning/tool_call/structured_output/...`，描述协议或推理能力。

新建与前端编辑要求上下文、最大输出和至少一种输入模态。迁移列保持 nullable，避免升级时为历史
模型伪造值；历史模型未补齐上下文时继续运行，但当前自动上下文压缩保持关闭。

v0.3.15 起，运行时将 `max_output_tokens` 映射为 Pydantic AI
`ModelSettings.max_tokens`，普通和流式请求使用同一设置；未配置时不发送该设置，保留 Provider
默认行为。上下文摘要不复用普通回答上限，而是继续根据摘要输入和窗口预算计算独立的动态
`max_tokens`，最高仍受现有摘要保护限制。

不增加：

- `max_input_tokens`：它不是稳定模型属性，不能简单用总窗口减最大输出。
- 固定输出预留：每轮应根据任务和实际 usage 动态预测。
- `context_budget_config`：Tokenizer 与估算系数属于算法，不要求用户持久化配置。

## 5. 全量刷新

接口：

```http
POST /api/v1/llm-models/catalog/refresh
```

事务边界：

```text
联网获取全部来源
→ Provider 白名单过滤
→ Lab / Model / Provider 标准化与合并
→ 校验所有候选行
→ 开启一个数据库事务
→ DELETE model_catalog
→ 批量 INSERT 新目录
→ COMMIT
```

完整校验至少包含：

- Provider 数据必须是对象，Provider 的 `models` 必须是对象。
- 复合主键不能重复。
- 每行必须能解析 Lab、标准模型和 Provider 模型 ID。
- 输入和输出必须包含文本。
- 上下文与最大输出必须为正整数，最大输出不得超过上下文。
- Provider 必须存在明确运行适配。
- 白名单过滤后不能得到空目录。
- 扩展 JSON 中递归删除所有 `cost` 字段。

任何联网、解析、校验或数据库写入失败都不提交，旧目录完整保留。进程内同一时间只允许一个刷新，
并发刷新返回 `409`。

不做：

- 服务启动自动刷新。
- 定时任务。
- 保存 `llm_models` 时联网刷新。
- Agent 运行时联网。
- 逐条增量更新或失败后混用新旧目录。

## 6. 查询接口

```http
GET /api/v1/llm-models/catalog
```

只查询数据库。支持：

```text
view=model|provider
q
lab_id
provider_id
modality
offset
limit
```

旧的 `source` 与 `refresh` 不再属于接口契约。FastAPI 默认可能忽略未知查询参数，但它们不会改变
数据源或触发网络请求。

### 6.1 按模型

按 `lab_id + canonical_model_id` 聚合：

```text
Lab
  └─ Model
       └─ Providers[]
```

模型项展示标准上下文、最大输出、输入模态、能力标签和可用渠道数；展开后展示每个 Provider 的
实际模型编码、运行适配、API 地址、Provider 覆盖后的 Token 上限和是否已有配置。

### 6.2 按接入渠道

按 `provider_id` 聚合，展示渠道协议、API、可用模型数量和模型列表，用于管理员检查接入范围。

## 7. Console

模型目录页提供：

- “按模型”默认视图：Lab 左侧分类，模型列表，展开 Provider。
- “按接入渠道”视图。
- 搜索、Provider 与输入模态过滤。
- “在线更新”调用手工刷新接口。
- “导入”接受models.dev官方`catalog.json`，供不能访问外网的部署环境离线更新。
- “导出”输出当前数据库中的已接受目录，格式可直接再导入。
- 初始空状态、刷新进度、错误回显和失败保留旧目录提示。

联网刷新和JSON导入共用一套解析、Provider白名单、标准化、完整校验和
单事务替换。导入上限为16 MiB，不接受自定义精简Schema；校验或写入失败不清空
已有目录。目录更新始终不修改`llm_models`运行配置。

点击“使用此模型”后，模型配置弹窗预填：

- 名称与说明。
- 运行时模型编码。
- Base URL。
- 上下文窗口。
- 最大输出 Token。
- 输入模态。
- 能力标签。

API Key 永不来自目录，管理员自行填写。保存只写 `llm_models`，不会再次访问 models.dev。

模型配置列表新增上下文、最大输出、输入模态和能力标签列；表单中两种标签分别展示。
编辑已有模型时可以点击“从模型目录更新”，按运行时模型编码或 Provider 模型编码匹配当前
数据库目录，并覆盖上下文窗口、最大输出、输入模态和能力标签。该操作不会修改名称、运行时
模型编码、Base URL 或 API Key；管理员仍需点击“保存”后才写入 `llm_models`。

## 8. 运行时行为

运行时只读 `llm_models`：

- `input_modalities`描述目录和管理员预期能力，不作为DeepAgent本地媒体拒绝开关；当前轮图片附件
  统一交给Provider，所选模型不接受时由Provider正常返回错误。
- 不再根据模型名称或 Base URL 猜测模态。
- 不再读取 `modality_data.json`、`models_dev_snapshot.json`。
- `AgentSpec/AgentRunner` 接收上下文、最大输出和 tags，为 Token 预算与上下文压缩提供事实来源。
- 上下文压缩算法只使用 `llm_models`，不在运行时反查目录。

Provider 工厂使用模型配置保存的 API Key 与 Base URL 构造 OpenAI、Anthropic、Google、
DeepSeek 或兼容 Provider；目录不参与运行时 Provider 解析。

## 9. Alembic 与发布安全

v0.6.6 已将Revision 001～012的稳定结构再次压缩为完整基线 `001_initial_schema.py`。该基线
一次性创建`model_catalog`、`llm_models`运行属性及全部当前表、约束、索引和默认数据。新数据库
从空库升级；旧Revision 012数据库必须在完整备份、停止旧实例并通过物理结构与全表内容哈希校验后，
使用`maintenance.rebaseline_database`执行无DDL的`stamp --purge 001`，不能重放或手工伪造迁移
状态。共享环境的具体安全门和校验命令见`src/maintenance/README.md`。

## 10. 配置备份

`src/maintenance/config_backup.py`按源Alembic Revision自动复制全部业务表，只将
`chat_sessions`和`chat_session_events`保留为空结构；新增配置表不再维护手工白名单。

备份完整保存：

- 目录的 Lab、Model、Provider 组合。
- 输入/输出模态、上下文、能力与三类扩展 JSON。
- `llm_models` 的上下文、最大输出和 tags；不保留无运行价值的目录来源字段。
- 原有 API Key、MCP Header 等配置原值。

`chat_sessions/chat_session_events`仍只建结构、不复制数据。备份工具仍只负责导出，不提供恢复命令。
备份完成前，使用新版 ORM同时读取 `LlmModel` 与 `ModelCatalog` 验证兼容性。

## 11. 验收

后端：

- 目录标准化正确处理 Lab、Model、Provider 及 Provider 覆盖值。
- 白名单与运行适配一致，价格字段被递归排除。
- GET 只查数据库，支持两种视图、过滤、分页、发布时间倒序与已配置标记。
- 刷新成功全量替换，联网/校验/写库失败保留旧目录。
- 模型 CRUD 强制新运行属性，旧 Meta 刷新接口删除。
- 运行时模态只认数据库。

数据库与运维：

- 临时 SQLite 完成 `001 → base → 001`，并验证单一 base/head。
- 配置备份包含目录表及全部新增字段和 JSON。
- 会话数据不进入备份。

前端：

- 两种目录视图、Lab 分类、Provider 展开与筛选正确。
- 空状态、刷新错误和重新加载可用。
- 目录预填与纯手工私有模型配置均可保存。
- 模态与能力标签分开展示。
- 保存模型不触发外网请求。

变更继续要求运行目标 Python 测试、完整 Python 测试、Console Node 测试、JS 语法检查、
Alembic 往返、配置备份测试、`git diff --check` 与工作区状态检查。后续事项见
[`../TODO.md`](../TODO.md)。
