# AGENTS.md — 后端（deep_agent）

> 父索引：[`../AGENTS.md`](../AGENTS.md)

后端 Python 包：FastAPI 传输层、Pydantic AI 运行时封装、能力层、会话存储、Agent/MCP 配置。

---

## 快速定位

| 任务 | 先看文件 |
| --- | --- |
| 启动与全局状态 | `main.py` → `core/context.py` |
| 对话入口 | `api/v1/chat.py` |
| Agent 执行 | `agent/runner.py` |
| 能力注册 | `capabilities/__init__.py` |
| 配置解析 | `options.py`（`OPTION` + accessor）；DB URL 见 `db/url.py` |
| MCP 加载 | `capabilities/mcp/resolver.py`、`db_loader.py`、`builtin.py` |

---

## 架构与数据流

```
HTTP (REST/SSE)
    → api/v1/chat.py
    → AgentRunner（agent/runner.py，与 FastAPI 解耦）
    → pydantic_ai.Agent
    → ToolPackageRegistry.toolsets()
        ├─ builtin:general  UTC 时间 FunctionToolset
        ├─ builtin:sandbox  无状态 Python FunctionToolset
        ├─ builtin:attachment  TXT/DOCX/XLSX 读取
        ├─ mcp          请求时 `capabilities/mcp/resolver.py`（DB + mcps/ 兜底）
        └─ ui           代码内置或数据库工具包 → ExternalToolset
    → Agent.skills → Harness Skills（SKILL.md 渐进披露）
    → SessionStore（`db/conversation.py` 协调器；表 Store 在 `db/stores/`）
```

### 启动生命周期（`main.py` lifespan → `AppContext.bootstrap()`）

1. `configure_logging()` → `AppContext.bootstrap()`（见 `core/context.py`）
2. `upgrade_database()` → `bootstrap_database()` → 表 Store + `SqlConversationCoordinator`
3. `build_registry(stores=)` — 通用、Sandbox 与附件工具包；Sandbox Backend 可用时注册独立 `builtin:sandbox`
4. `init_context(ctx)` — 路由通过 `Depends(AppContextDep)` 注入
5. 关闭：`ctx.aclose()` → `reset_context()`

未配置 `DEEP_AGENT_DBMS_*` 时默认使用 SQLite 文件 `data/deep-agent.db`（可用 `DEEP_AGENT_SQLITE_PATH` 覆盖）。配置 `DEEP_AGENT_DBMS_*` 则使用 PostgreSQL / MySQL。

MCP 在请求时由 `resolve_mcp_toolsets` 解析（DB 行 + `mcps/` builtin 兜底）。

### 对话请求流（`POST /api/v1/chat/stream`）

1. 解析 `StreamChatRequest`；`session_id` 缺省则生成 UUID
2. **无 `agent_id`**：`build_runner(model?, capabilities?)` → `OPTION` 默认 `AgentSpec`
3. **有 `agent_id`**：`build_runner_for_agent()` → `agents` 表 → `AgentSpec.from_row()` → 存储 `(agent_id, session_id)`
4. `AgentRunner.run_stream()`：session 锁 → raw history / Checkpoint 投影 → Pydantic AI `AbstractCapability` 预算与压缩 → SSE 事件 → `append(new_messages)`
5. `capabilities: null` → 仅 `default_enabled=True` 的工具包；显式列表按工具包编码启用

### 正式取消（v0.3.10）

停止按钮调用 `POST /api/v1/sessions/{session_id}/cancel`，由进程级 `ActiveRunRegistry` 找到
Pydantic AI `CancellationToken`。Runner 捕获 `RunCancelled` 后只持久化
`RunCancelled.new_messages()` 并返回真实 `cancelled` SSE（含 `usage`）；不再探测其他 result、
拼接流式文本、手工补 `ToolDenied` 或生成前端假事件。若 Provider 返回的最后一个非空部分响应
仍标为 `complete`，持久化前统一规范化为 `state=interrupted`，History API 映射为
`assistant.status=interrupted`；下一轮的悬空 Tool Call 由 Pydantic AI 自动补
`outcome=interrupted`。已实时发布但未进入官方中断历史的工具调用先发送
`tool_call_removed(reason=cancelled)`，避免刷新前后工具卡不一致。

普通工具在参数校验后发布 `tool_call`；若业务执行返回 Pydantic AI `ModelRetry`，对应官方
`RetryPromptPart` 必须映射为 `tool_call_removed(reason=model_retry)`。客户端按
`tool_call_id` 删除临时卡片，修正后的新调用再按新的 ID 正常展示。

工具错误需按可纠正性分类：参数、对象选择或工具选择错误使用 `ModelRetry`；同样参数重试也
不会成功的下载、文件损坏、解析、大小上限或明确上游失败使用 `ToolFailed`。两者都由
Pydantic AI 形成合法 Tool Result，但只有 `ModelRetry` 消耗工具重试预算并要求模型纠正。

流式接口必须先把 Active Run 注册到 Registry，再发布首条 `session` SSE，并将同一个
`CancellationToken` 传入 Runner。否则客户端刚拿到 `session_id` 就停止时，会在用户消息尚未
持久化的窗口内误报 `404 Session not found`。

页面关闭或网络断开只触发 Token 清理无人消费的 Run，不是正常停止入口。UI Deferred 已经
Pending 时没有活动 Run，其表单取消仍通过 `DeferredToolResults` 处理。当前注册表只在单进程
内有效。v0.3.14 起每个 Pod 固定一个异步 Gunicorn Worker；多 Pod 发布仍必须使用会话粘性，
或后续增加共享取消协调器。

### MySQL 空闲连接（v0.3.11）

- MySQL 引擎使用 `pool_pre_ping=True` 和 `pool_recycle=90`，在 Test 默认 120 秒空闲超时前主动换新连接。
- 仅 pre-ping 阶段的 asyncmy `TCPTransport closed=True` / `handler is closed` RuntimeError 会被标记为断链并交给 SQLAlchemy 重连。
- 不对已经开始执行的业务 SQL 做通用重试，避免写请求被重复执行；SQLite 和 PostgreSQL 不使用 MySQL 的 90 秒回收策略。
- 排障、环境取值和未来配置项约定见 [`../docs/guides/mysql-idle-connection-timeout.md`](../docs/guides/mysql-idle-connection-timeout.md)。

### 能力装配与模型输入

DeepAgent 自研 `capabilities.registry.ToolPackage` 是产品配置单位，包含分类、展示元数据、工具目录和恰好一个 Toolset；Pydantic AI 的 `AbstractCapability` 才是可介入模型请求、消息历史、工具准备和事件生命周期的原生扩展点。当前上下文压缩使用这一层；DeepSeek V4 已交由 Pydantic AI 官方 Provider/Profile，不再注册项目自定义重试能力。

模型每轮直接看到的是：Agent/能力 Instructions、`message_history`、当前用户输入/附件和本轮 Tool Definitions。Toolset 负责列出、准备和调用工具；MCP Prompt 不会自动进入模型工具列表。MCP Resource 由宿主侧动态代理工具按需读取。

URL 与 Builtin MCP 的运行态不同：URL MCP 在管理员检测时保存 Tool Definitions 和 Server Instructions，Run 只读快照，首次真实调用时才创建官方 `MCPToolset`；Builtin MCP 仍由 Run-scoped Client 按需启动。两者最终工具名都由 `PrefixedToolset` 统一加 Server 前缀。缺少或配置失效的 URL 快照不披露工具，也不会因远端不可达阻塞普通聊天。

新增横切能力时遵循：

1. 确定性历史整理优先复用 Harness 的 Clamp、Deduplicate 和 Clear tiers。DeepAgent 的动态窗口预算、主动压缩 SSE、Checkpoint Hash/CAS 与目标复检属于产品语义，不能直接用通用 `ProcessHistory` 或 Harness `SummarizingCompaction` 替代。
2. 按 App、Space、权限或模态过滤工具优先使用 `PrepareTools`。
3. v0.3.12 起，已授权 MCP 候选超过 3 个时统一进入 `DeferredLoadingToolset`，由本地词法检索预选 Top 3、模型 `search_tools` 返回 Top 5；小型 MCP 全量直接披露。首版不使用 Embedding，不持久化索引。
4. 生命周期观测优先使用 Hooks/Instrumentation，并先完成 Token、Cookie、文件和工具结果脱敏。
5. 通用大工具结果治理优先评估 Harness `OverflowingToolOutput`；多实例部署不得直接依赖默认本地文件 Store。
6. Harness Memory 只保存明确的跨会话笔记，不替代聊天历史；namespace 必须由认证用户、Agent、App 和 Space 生成。
7. Harness `StepPersistence` 不替代现有 SQL 会话、SSE 和 Deferred/Pending；只有副作用账本、子 Agent 或崩溃恢复成为真实需求时再接入。
8. Harness 仍为 0.x。引入时必须将 `pydantic-ai-slim` 与 `pydantic-ai-harness` 固定为经过联合回归的准确版本，禁止继续依赖无上限 `>=` 漂移。
9. 不要通过修改 `AgentRunner` 主循环重复实现 Pydantic AI 已提供的机制。
10. Harness 0.18 `Skills` 只负责延迟披露 `SKILL.md` 正文，不读取或执行 references、assets、scripts；第三方 Skill 的安装、版本、权限、素材读取和脚本隔离仍由 DeepAgent 产品层负责。
11. `IncludeToolReturnSchemas` 发生在模型请求前的 Tool Definition 准备阶段，不是工具执行后给真实结果追加 Schema。首版只允许按最终稳定协议名的明确白名单启用，并要求存在可信、稳定的返回 Schema；不得通过名称包含 `query` 等模糊规则自动扩大范围。延迟 MCP 工具只有被 Tool Search 披露时才携带其返回 Schema，并必须观测新增 Schema Token。真实结果过大或空字段过多继续由分页、投影或 `ToolOutputLimits`/Artifact 治理。
12. Prompt Cache 不是跨 Provider 的统一 DeepAgent 缓存。DeepSeek 等 Provider 的自动缓存只读取其 Usage 命中/未命中数据，不改写请求；显式 `CachePoint`、cache key 和 TTL 只能在模型目录确认具体 Provider 支持后启用。统一观测可以归一化指标，不能伪造统一缓存协议。
13. v0.3.17 使用 Pydantic AI 官方 `conversation_id/run_id` 贯通运行：`conversation_id=deepagent:{agent_id}:{session_id}` 在同一 Session 内稳定，每次发送、Deferred 恢复、重试或主动压缩使用新的 `run_id`。第一阶段只输出脱敏结构化日志和当前页面 Run 指标，不把高频运行数据写入业务 MySQL；后续 OTLP/Instrumentation 不得建立第二套 Agent 生命周期。

框架能力是否应采用、部分采用或明确关闭，统一先查
[`../docs/design/14-framework-capabilities/framework-capabilities-baseline.md`](../docs/design/14-framework-capabilities/framework-capabilities-baseline.md)。
升级 Pydantic AI/Harness 时只做相对该基线的增量审计，不从历史 TODO 重新发散；没有真实触发条件的
框架能力不得因为“官方已经提供”就进入开发计划。

当前上下文压缩默认自动启用：模型配置有效 `context_window_tokens` 即启用，否则停用。
摘要始终复用当前 Agent 主模型，并与正式回答共享 `RunUsage` 和全局 `UsageLimits`；
`chat_messages` 保持完整事实源，只有摘要成功且复检达到目标线时才覆盖 Checkpoint。
v0.3.4 同时提供独立的 session 主动压缩 SSE 接口；它跳过自动触发线，也不因历史较短、
已经生成 Checkpoint 或没有新增消息而拒绝用户操作。后端仍要求安全前缀、目标复检、历史 Hash
与 Checkpoint CAS 全部通过；无有效收益返回 `noop`，失败时不得用 Sliding Window 冒充成功。

v0.3.6 的摘要链路只允许一次语义摘要。压缩器同时校验最终投影目标和摘要请求窗口，保留最近
历史最多的安全切分点；不再使用 192K 固定封顶、滚动分块或第二次更激进摘要。429、502、
503、504、连接重置和超时可用同一 Prompt 重试一次，自动链路还必须为正式回答保留一次请求。

v0.3.13 起，自动压缩的内部摘要复用正式 Run 的 Pydantic AI `CancellationToken`；用户停止时
摘要调用与外层 Run 一起取消，`RunCancelled` 不得转成 Sliding Window 继续回答。工具边界和
历史待处理判断使用 2.26 的 `BaseToolCallPart` / `BaseToolReturnPart`，同时覆盖 Local Tool、
Tool Search 和 Provider Native Tool。Provider 原生 OpenAI/Anthropic Compaction 暂不与
DeepAgent Checkpoint 混用；只有不透明 `provider_details` 的 `CompactionPart` 继续拒绝落盘。

v0.3.16 起，自动与主动压缩必须调用同一个“确定性整理 → 单次摘要 → 目标复检”内部投影流程。
摘要输出固定为任务目标与约束、关键决策及理由、事实/文件/标识和工具结论、当前状态、下一步、未决问题
与阻塞六段；旧摘要只作为一次锚点更新。运行压缩相关改动时执行
`python3.12 tests/check_context_compaction_coverage.py`，核心行覆盖率不得低于 95%，分支覆盖率
不得低于 85%。

v0.3.14 起部署入口固定每个 Pod 一个异步 Gunicorn Worker，使 session 取消请求与进程内
`ActiveRunRegistry` 一致命中。主动压缩的 `context_compaction_result` 不再等待二次
`context-status` 查询，客户端收到终态后分别刷新 history 与 context-status。多个 Pod 的正式
取消仍需要会话粘性或共享取消协调器，不能依赖随机网关路由。

v0.3.5 的普通与流式聊天支持结构化 `context_refs`。公共外壳在 Schema 层严格校验；原始
`payload/metadata` 随消息保存，模型、Token 估算、自动/主动压缩只使用排除 `metadata` 的
XML + JSON 投影。业务字段语义仍由具体 Agent Prompt 负责。

研究依据见
[`../docs/design/03-context-compaction/context-compaction-research.md`](../docs/design/03-context-compaction/context-compaction-research.md)；
当前数据结构和运行方案见
[`../docs/design/03-context-compaction/context-compaction-design.md`](../docs/design/03-context-compaction/context-compaction-design.md)。

---

## 模块索引

### `agent/`

| 文件 | 职责 |
| --- | --- |
| `spec.py` | `AgentSpec`：`from_row()`、model、instructions、capabilities、skills |
| `history.py` | `ModelMessage[]` → 前端时间线 |
| `factory.py` | `build_agent()` — model 字符串或 OpenAI 兼容 endpoint |
| `models/` | models.dev 管理员刷新、目录标准化和 Provider/模型适配 |
| `attachment_extractors.py` | 文本、DOCX、XLSX 按需解析、分页与文件安全限制 |
| `attachments.py` | 会话附件引用、原生模态校验和平台下载 |
| `runner.py` | `run()` / `run_stream()` — 会话锁、history、事件映射 |
| `deps.py` | `AgentDeps`：session_id、user、agent_id、request_headers |
| `headers.py` | HTTP → MCP 的 header 白名单提取 |
| `events.py` | Pydantic AI 事件 → SSE 字典（使用 `singledispatch` 分层映射） |

### `capabilities/`

| 路径 | 职责 |
| --- | --- |
| `registry.py` | `ToolInfo` / `ToolPackage` / `ToolPackageRegistry` |
| `native/builtin.py` | 默认启用的内置“通用工具”，当前提供 `get_current_time` |
| `mcp/db_loader.py` | 从 `mcp_servers` 构建 MCP ToolPackage 与快照 Toolset |
| `mcp/builtin.py` | 发现 `mcps/<name>/server.py` |
| `mcp/toolset_builder.py` | url / stdio / builtin + header 注入 |
| `mcp/inspection.py` | URL MCP initialize、Ping、能力清单检测与快照 |
| `mcp/snapshot_toolset.py` | URL MCP 纯内存工具目录、首次调用建连与 Run 内连接复用 |
| `mcp/resources.py` | 按检测快照开放、运行时实时读取的 Resource 代理工具 |
| `mcp/run_scoped.py` | builtin MCP：按 agent run 租约 HTTP client |
| `mcp/lease_manager.py` | worker 级 MCP lease + shared registry touch |
| `ui/registry.py` | builtin/manual UI 工具按包筛选、合并和 `prepare_ui_tool_packages_for_spec` |
| `attachments/` | 默认关闭的 `builtin:attachment` 包；会话内 TXT/DOCX/XLSX 分页读取 |
| `../sandbox/toolset.py` | 默认关闭的 `builtin:sandbox`；代理 PythonExecutionBackend |

### `skills/`

| 文件 | 职责 |
| --- | --- |
| `catalog.py` | 合并代码内置与数据库上传 Skill 目录 |
| `package.py` | ZIP、Frontmatter、Manifest 与资源索引安全校验 |
| `runtime.py` | 平台 ZIP 下载、Hash 校验、临时物化和 Harness `Skills` 装配 |
| `tools.py` | 已授权 Reference/Asset 的只读 List/Read 工具 |

### `api/v1/`

| 文件 | 路由 |
| --- | --- |
| `chat.py` | `POST /chat`, `POST /chat/stream` |
| `agents.py` | CRUD `/agents` |
| `mcp.py` | CRUD `/mcp`、统一检测 `/mcp/{name}/inspect` |
| `ui_tools.py` | CRUD `/ui-tools`；工具必须归属手工 UI 工具包 |
| `ui_tool_packages.py` | CRUD `/ui-tool-packages`；代码内置包只读 |
| `skills.py` | Skill 目录、安装、替换、启停、资源和原包下载 |
| `sessions.py` | 会话列表 / 历史 / 删除 / 正式取消当前 Run |
| `auxiliary_models.py` | CRUD `/auxiliary-models` |
| `tools.py` | `GET /tools` |
| `deps.py` | `build_runner` / `build_runner_for_agent`，统一承接多入口的运行器组装 |

### `db/`

| 路径 | 职责 |
| --- | --- |
| `deps.py` | `get_*_store(ctx)` Depends |
| `conversation.py` | `SessionStore` 协议 + SQL/内存协调器（跨表写入、进程锁） |
| `stores/` | 表 Store（含 `model_catalog/`、`skill_package/`、`ui_tool_package/`、`context_checkpoint/`）+ `bundle.DataStores` |
| `factory.py` / `dialect.py` | `create_engine` / `close_db`、`bootstrap_database()`、方言解析 |
| `session_scope.py` / `session_list.py` | 会话作用域解析、列表过滤 DTO |

| 表 | 用途 |
| --- | --- |
| `agents` | DB Agent（UUID PK，name 唯一） |
| `llm_models` | LLM 运行时唯一模型事实：连接、模态、上下文、最大输出与 tags |
| `model_catalog` | models.dev 的 Lab/Model/Provider 数据库快照，只用于浏览和预填 |
| `auxiliary_model_configs` | 辅助模型场景绑定；当前只用于会话标题生成 |
| `mcp_servers` | MCP transport、config JSON、最近一次 inspection 检测快照 |
| `skill_packages` | 上传 Skill 当前安装版本的元数据、平台 File ID、Manifest 与资源索引 |
| `ui_tool_packages` | 手工 UI 工具包；代码内置 `ui:builtin` 不写表 |
| `ui_tool_definitions` | 手工 UI 工具定义及所属包；builtin 由代码生成，不写表 |
| `chat_sessions` | 会话元数据 |
| `chat_messages` | `ModelMessage` JSONB 历史 |
| `chat_context_checkpoints` | 脱敏后的模型输入派生投影；不替代原始历史 |

## 扩展指南

### 内置工具包

`builtin:general` 只提供当前时间且默认启用；`builtin:sandbox` 只提供 Python 执行，
`builtin:attachment` 提供附件读取，两者默认关闭。每个包严格包装一个 Toolset。内置包由代码随版本发布，
不在数据库注册；配置中心只读展示，Agent 仍按整包选择。

### Skill

- Skill 不属于 ToolPackage，Agent 通过 `agents.skills` 独立选择。
- 代码内置只读包位于 `builtin_skills/<code>/SKILL.md`。
- 上传包由 `/api/v1/skills` 校验后保存到平台文件服务，`skill_packages` 只存 File ID 和索引。
- 当前 Pod 只保留 `runtime-skills/<code>/` 一份上传 Skill 运行副本；启动、安装、替换、启用和
  `POST /api/v1/skills/{code}/reload` 负责同步，普通 Run 不访问平台文件服务。Hash 只用于完整性
  和过期判断，不代表产品版本，也不保留历史目录。
- Harness 0.18 `Skills` 负责 `load_capability` 渐进披露；DeepAgent 只补授权资源 List/Read。
- `scripts/`、`tools/` 可以随包保存，但当前禁止模型读取和执行。
- 单个上传 Skill 文件缺失或同步失败时，本轮跳过该 Skill 并继续运行；不得让它阻塞其他 Skill、
  MCP 或普通回答。诊断进入 `/skills` 的派生状态与 `final.context.skills`，不写数据库状态字段。
- 完整边界见 [`../docs/design/12-agent-skills/skill-system-design.md`](../docs/design/12-agent-skills/skill-system-design.md)。

### MCP Server

开发内置 server → 见 [`../mcps/AGENTS.md`](../mcps/AGENTS.md)。

生产配置 → `POST /api/v1/mcp` 或 `/console/#/configuration`。用户新建只支持 URL MCP；Builtin 由仓库内 `mcps/` 和程序配置管理，可查看、更新允许运行时调整的配置，但不能通过 Console 或删除 API 移除。历史 stdio 行只允许读取或删除，不能新建、修改或运行，需迁移为 URL MCP。

### DB Agent

`POST /api/v1/agents` 或 Alembic 种子；流式请求带 `agent_id`（UUID）。

### UI 工具

代码内置 `builtin__confirm`、`builtin__choice` 同属只读包 `ui:builtin`。手工包保存于
`ui_tool_packages`，工具通过 `package_code` 归属；Agent 的 `capabilities` 只保存包编码，
选中后包内所有启用且 Schema 有效的工具进入同一个 ExternalToolset。内置包和工具不能修改
或删除；手工包包含工具或被 Agent 引用时也不能删除。

`builtin__choice` 始终支持候选外自由输入，结果固定为 `{selected: string[], custom_input: string | null}`；单选、多选和自由输入共用这一结构。

### 附件工具

`builtin:attachment` 是默认关闭的 Agent 工具包，只在 `capabilities` 显式包含该编码时加载
`read_text_attachment`、`read_docx`、`read_xlsx`。工具首次通常只传 `file_id`，随后根据
`has_more/next_offset` 渐进读取。合法 ID 范围必须从当前消息和当前会话完整
`chat_messages` 建立，不能从可能被裁剪的 Checkpoint 建立，也不能仅依赖平台下载权限。

图片、音频、视频、PDF 是原生模态，运行时只认 `llm_models.input_modalities`。PDF 当前无
本地解析/OCR。新上传不抽文本，历史 `extracted_text` 仅为回放兼容。完整设计见
[`../docs/design/06-attachment-parsing/`](../docs/design/06-attachment-parsing/)。

### Python Sandbox

`execute_python` 属于默认关闭的 `builtin:sandbox`，与时间工具分开整包选择。工具协议
与执行 Backend 解耦：本地联调使用 `subprocess-dev`，生产硬隔离
仍按 [`../docs/design/07-python-sandbox/`](../docs/design/07-python-sandbox/) 推进。它不是 MCP，
也不等同于 Provider 托管的 `CodeExecutionTool`。

---

## 配置

`.env` 在仓库根，经 `options.py` → `deepfos_platform.conf.option` 加载。

### 本地 Test 调试入口

生产环境继续启动 `deep_agent.main:app`，只使用平台网关注入的身份。需要从本地浏览器调试 Test 数据和服务时，使用独立入口：

```bash
python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

配置文件必须位于仓库外，且在 macOS/Linux 上执行 `chmod 600`。这个文件作为完整的本地运行 Profile，可同时包含 `DEEP_AGENT_DBMS_*`、`EXPORT_*` 和以下身份项：

```text
DEEP_AGENT_LOCAL_USER=<平台网关注入的用户 ID>
DEEP_AGENT_LOCAL_TOKEN=<当前环境的有效 Token>
DEEP_AGENT_LOCAL_TOKEN_COOKIE=<可选，仅在目标环境要求 Cookie 时填写 Cookie 名>
```

本地入口只监听 `127.0.0.1`。它会覆盖浏览器提交的 `user / authorization / x-user-token / cookie`，保留控制台提交的调试 `app`。后端根据标准平台编码规则从 App 去掉末尾三位得到 Space，并在未显式提供语言时使用 `zh-cn`。身份和 Token 不得进入源码、仓库、浏览器存储、日志或测试夹具。

注意：应用启动会自动执行 Alembic `upgrade head`。本地 Profile 如果连接共享 Test 数据库，新增迁移会随本地服务启动直接应用；涉及删列等不兼容 DDL 时，必须先协调 Test 服务维护窗口，不能直接启动新代码。

### Agent（`DEEP_AGENT_` 前缀）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODEL` | `openai:gpt-4o` | `<provider>:<model>` |
| `MODEL_BASE_URL` / `MODEL_API_KEY` | — | OpenAI 兼容 API |
| `SYSTEM_PROMPT` | 内置 | 系统指令 |
| `MAX_REQUESTS` | `30` | 所有 Agent 共用的单轮模型请求上限 |
| `MAX_TOTAL_TOKENS` | 无限制 | Token 上限 |
| `SKILLS_DIR` | `skills` | 相对仓库根 |
| `MCP_FORWARD_REQUEST_HEADERS` | `authorization,x-tenant-id,x-user-token` | 转发到 MCP |
| `MCP_STDIO_SESSION_SCOPE` | `true` | stdio MCP 按 session 保活 |

### 数据库（`DEEP_AGENT_DBMS_` 前缀）

| 变量 | 说明 |
| --- | --- |
| `NAME` | `postgres` \| `mysql`（kingbase/dameng 需额外驱动） |
| `HOSTNAME` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | 连接信息 |
| 全部未设置 | 文件 SQLite，默认 `data/deep-agent.db`；可用 `DEEP_AGENT_SQLITE_PATH` 覆盖 |

Provider Key（`OPENAI_API_KEY` 等）由 Pydantic AI 直接读取。

### 迁移

```bash
# 仓库根
alembic upgrade head
```

迁移：v0.3.0 起只保留 `alembic/versions/001_initial_schema.py` 作为完整基线；旧 `001` … `023` 历史不再兼容。已有旧库必须先用 `maintenance.rebaseline_database` 校验最终物理结构，再执行 `stamp --purge 001`，禁止仅手工修改版本号。涉及共享数据库时必须按维护窗口发布。本地 Postgres：`docker compose up -d`。

---

## API 速查

| 方法 | 路径 | 需 DB |
| --- | --- | --- |
| GET | `/health`, `/info`, `/docs` | 否 |
| POST | `/api/v1/chat`, `/api/v1/chat/stream` | 否（持久化需 DB） |
| CRUD | `/api/v1/agents` | 是 |
| CRUD | `/api/v1/mcp` | 是 |
| CRUD | `/api/v1/ui-tools` | 是 |
| GET | `/api/v1/tools` | 否（无 DB 时跳过动态 UI 工具） |
| GET | `/api/v1/global-settings` | 否；无需额外身份依赖，返回安全白名单，只读 |
| GET/POST | `/api/v1/llm-models/catalog`、`/api/v1/llm-models/catalog/refresh` | 是；目录只读查询、管理员手工全量刷新 |
| GET/POST | `/api/v1/context-test-scenarios` | 是；按当前模型预算创建普通测试会话 |
| GET/POST | `/api/v1/uploads/capabilities`、`/api/v1/uploads` | 否/否；后端仍执行用户与文件限制校验 |
| GET | `/api/v1/request-context` | 否；返回当前请求上下文最终值 |
| GET | `/api/v1/sessions/{id}/context-status` | 是；沿用会话所有权校验 |
| | `/api/v1/sessions/*` | 是 |
| | `/console/#/configuration` | 是 |

SSE 协议：[`../docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。前端对接：[`../frontend/AGENTS.md`](../frontend/AGENTS.md)。

---

## 约定与陷阱

0. **重构风格**：事件多态映射集中在 `agent/events.py`；多入口共享组装集中在 `api/v1/deps.py`，变化量留在调用入口
   - 事件多态映射 → `agent/events.py`（`singledispatch` 分层）
   - 多入口共享组装 → `api/v1/deps.py`（变化量留在入口，上下文在 `_build_*` 内统一解包）
1. 包名 **`deep_agent`**，启动 `deep_agent.main:app`（非 `app.main`）
2. 扩展优先走路径注册，避免改 `runner.py` 核心
3. 工具包命名：`builtin:general` \| `builtin:sandbox` \| `builtin:attachment` \| `mcp:<name>` \| `ui:<package>`；一个包严格包装一个 Toolset，Agent 按包选择，工具协议名不随包编码变化。Skill 使用独立 `agents.skills`，不得重新塞回 ToolPackage
4. DB Agent 会话：复合键 `(agent_id, session_id)`；无 agent 时 `agent_id=''`
5. 同 session 用 `SessionStore.lock()` 串行；多实例 DB 级锁未实现
6. 配置保存先完成 Schema/引用校验，再进入写事务；一次 API 业务变更不要串联多个各自提交的 Store `_write()`。MCP 检测等外部 I/O 先基于候选配置执行，再把配置与检测快照一次性提交。模型目录联网、解析和完整校验必须在事务前完成，随后单事务全量替换；模型配置保存不得隐式联网。
7. `model_catalog` 不是运行时事实源，目录刷新不得修改已有 `llm_models`。模型名称和 Base URL 不再用于猜测模态；上下文缺失时当前自动压缩保持关闭。
8. 配置中心必填文本会去除首尾空白并拒绝空值，显式把必填更新字段传为 `null` 返回 422；重复名称返回 409。删除仍被 Agent 引用的 MCP、UI 工具包或包内工具返回 409，并在 `detail.referenced_by` 列出引用方。
9. 请求上下文以 App 为主键：标准 App 必须以三位数字结尾；Space 自动取 `app[:-3]`。App 与显式 Space 不一致时返回 422，Language 缺失时默认 `zh-cn`。
10. 上下文压缩在模型配置有效上下文窗口时自动启用；环境变量只调节全局比例、工具保留量和摘要超时，
   不提供启用开关。只有主模型实际生成/更新 Summary 且复检达标时才写 Checkpoint；
   确定性压缩、Sliding Window、Pending、取消、失败都不更新。Checkpoint 写入失败不能让已完成对话失败。
11. Console 压缩测试会话是管理员控制台的常驻功能；后端必须按当前模型实时预算构造可重新
    反序列化的普通对话历史，不得直接写 Checkpoint，也不得新增测试专用表。

| 现象 | 原因 |
| --- | --- |
| MCP 工具为空 | MCP 未 enabled，或 Agent 未选择对应 `mcp:<name>` |
| 历史不在预期数据库 | 未配置 DBMS 时实际使用默认文件 SQLite；检查 `DEEP_AGENT_SQLITE_PATH` |
