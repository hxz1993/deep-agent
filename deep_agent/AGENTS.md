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
    → CapabilityRegistry.toolsets()
        ├─ native       capabilities/native/builtin.py
        ├─ skill        skills/<name>/（仓库根，见 DEEP_AGENT_SKILLS_DIR）
        ├─ cursor-skill .cursor/skills/<name>/SKILL.md
        └─ mcp          请求时 `capabilities/mcp/resolver.py`（DB + mcps/ 兜底）
    → SessionStore（`db/conversation.py` 协调器；表 Store 在 `db/stores/`）
```

### 启动生命周期（`main.py` lifespan → `AppContext.bootstrap()`）

1. `configure_logging()` → `AppContext.bootstrap()`（见 `core/context.py`）
2. `upgrade_database()` → `bootstrap_database()` → 6 个 Store（含 `SqlConversationCoordinator`）
3. `build_registry(stores=)` — native + skill + cursor-skill（静态能力）；`stores` 预留未来启动期只读配置
4. `init_context(ctx)` — 路由通过 `Depends(AppContextDep)` 注入
5. 关闭：`ctx.aclose()` → `reset_context()`

未配置 `DEEP_AGENT_DBMS_*` 时默认使用 SQLite 文件 `data/deep-agent.db`（可用 `DEEP_AGENT_SQLITE_PATH` 覆盖）。配置 `DEEP_AGENT_DBMS_*` 则使用 PostgreSQL / MySQL。

MCP 在请求时由 `resolve_mcp_toolsets` 解析（DB 行 + `mcps/` builtin 兜底）。

### 对话请求流（`POST /api/v1/chat/stream`）

1. 解析 `StreamChatRequest`；`session_id` 缺省则生成 UUID
2. **无 `agent_id`**：`build_runner(model?, capabilities?)` → `OPTION` 默认 `AgentSpec`
3. **有 `agent_id`**：`build_runner_for_agent()` → `agents` 表 → `AgentSpec.from_row()` → 存储 `(agent_id, session_id)`
4. `AgentRunner.run_stream()`：session 锁 → history → `agent.iter()` → SSE 事件 → `append(new_messages)`
5. `capabilities: null` → 仅 `default_enabled=True`；显式列表按名启用

### 能力装配与模型输入

DeepAgent 自研 `capabilities.registry.Capability` 是配置包装对象，负责把一组 Instructions/Toolset 暴露给 Agent；Pydantic AI 的 `AbstractCapability` 才是可介入模型请求、消息历史、工具准备和事件生命周期的原生扩展点。当前只有 `DeepSeekThinkingRetry` 使用了原生 Capability。

模型每轮直接看到的是：Agent/能力 Instructions、`message_history`、当前用户输入/附件和本轮 Tool Definitions。Toolset 负责列出、准备和调用工具；MCP Resource 与 Prompt 不会自动进入模型工具列表。

运行态 URL 与 Builtin MCP 统一启用 Pydantic AI `MCPToolset(include_instructions=True)`，因此已关联 MCP 在 `initialize` 中返回的 Server Instructions 会随 Toolset 加入 Agent Instructions；未关联 MCP 不会注入。UI 工具发现专用连接不参与对话，不需要启用该选项。

新增横切能力时遵循：

1. 历史裁剪、摘要和上下文预算优先评估 Harness `TieredCompaction`；若采用 Harness，不要再并行维护一套自研 `ProcessHistory` 压缩器。SQL 原始历史继续作为产品事实源，压缩结果应作为派生上下文保存。
2. 按 App、Space、权限或模态过滤工具优先使用 `PrepareTools`。
3. 工具达到数十个、Schema 明显占用上下文后再评估 `ToolSearch`。
4. 生命周期观测优先使用 Hooks/Instrumentation，并先完成 Token、Cookie、文件和工具结果脱敏。
5. 通用大工具结果治理优先评估 Harness `OverflowingToolOutput`；多实例部署不得直接依赖默认本地文件 Store。
6. Harness Memory 只保存明确的跨会话笔记，不替代聊天历史；namespace 必须由认证用户、Agent、App 和 Space 生成。
7. Harness `StepPersistence` 不替代现有 SQL 会话、SSE 和 Deferred/Pending；只有副作用账本、子 Agent 或崩溃恢复成为真实需求时再接入。
8. Harness 仍为 0.x。引入时必须将 `pydantic-ai-slim` 与 `pydantic-ai-harness` 固定为经过联合回归的准确版本，禁止继续依赖无上限 `>=` 漂移。
9. 不要通过修改 `AgentRunner` 主循环重复实现 Pydantic AI 已提供的机制。

完整审计见 [`../docs/pydantic-ai-native-capabilities-and-deepagent-usage-audit.md`](../docs/pydantic-ai-native-capabilities-and-deepagent-usage-audit.md)。

---

## 模块索引

### `agent/`

| 文件 | 职责 |
| --- | --- |
| `spec.py` | `AgentSpec`：`from_row()`、model、instructions、capabilities |
| `history.py` | `ModelMessage[]` → 前端时间线 |
| `factory.py` | `build_agent()` — model 字符串或 OpenAI 兼容 endpoint |
| `runner.py` | `run()` / `run_stream()` — 会话锁、history、事件映射 |
| `deps.py` | `AgentDeps`：session_id、user、agent_id、request_headers |
| `headers.py` | HTTP → MCP 的 header 白名单提取 |
| `events.py` | Pydantic AI 事件 → SSE 字典（使用 `singledispatch` 分层映射） |

### `capabilities/`

| 路径 | 职责 |
| --- | --- |
| `registry.py` | `Capability` / `CapabilityRegistry` |
| `native/builtin.py` | `get_current_time`、`http_get` |
| `skills/loader.py` | 扫描 `skills/<name>/__init__.py` 的 `SKILL` |
| `cursor_skills/` | SKILL.md → `read_<skill>()` 渐进式披露 |
| `mcp/db_loader.py` | 从 `mcp_servers` 构建 MCPToolset |
| `mcp/builtin.py` | 发现 `mcps/<name>/server.py` |
| `mcp/toolset_builder.py` | url / stdio / builtin + header 注入 |
| `mcp/run_scoped.py` | builtin MCP：按 agent run 租约 HTTP client |
| `mcp/lease_manager.py` | worker 级 MCP lease + shared registry touch |
| `ui/registry.py` | UI 工具合并、`ui:<name>` 正向关联、可见性、`prepare_ui_toolset_for_spec` |
| `mcp/ui_discovery.py` | MCP manifest 发现 → `UiToolStore.sync_mcp_tools` |

### `api/v1/`

| 文件 | 路由 |
| --- | --- |
| `chat.py` | `POST /chat`, `POST /chat/stream` |
| `agents.py` | CRUD `/agents` |
| `mcp.py` | CRUD `/mcp`、UI 工具刷新（写后热重载 registry） |
| `ui_tools.py` | CRUD `/ui-tools` |
| `sessions.py` | 会话列表 / 历史 / 删除 |
| `auxiliary_models.py` | CRUD `/auxiliary-models` |
| `tools.py` | `GET /tools` |
| `deps.py` | `build_runner` / `build_runner_for_agent`，统一承接多入口的运行器组装 |

### `db/`

| 路径 | 职责 |
| --- | --- |
| `deps.py` | `get_*_store(ctx)` Depends |
| `conversation.py` | `SessionStore` 协议 + SQL/内存协调器（跨表写入、进程锁） |
| `stores/` | 7 个表 Store（`agent/`、`llm_model/`、`auxiliary_model/`、`mcp/`、`ui_tool/`、`chat_session/`、`message/`）+ `bundle.DataStores` |
| `factory.py` / `dialect.py` | `create_engine` / `close_db`、`bootstrap_database()`、方言解析 |
| `session_scope.py` / `session_list.py` | 会话作用域解析、列表过滤 DTO |

| 表 | 用途 |
| --- | --- |
| `agents` | DB Agent（UUID PK，name 唯一） |
| `llm_models` | LLM 模型预设 |
| `auxiliary_model_configs` | 辅助模型场景（如标题生成）绑定 |
| `mcp_servers` | MCP transport + config JSON |
| `ui_tool_definitions` | UI 工具定义（builtin / mcp / manual） |
| `chat_sessions` | 会话元数据 |
| `chat_messages` | `ModelMessage` JSONB 历史 |

## 扩展指南

### native 工具

在 `capabilities/native/builtin.py` 的 `FunctionToolset` 上装饰函数，类型注解即 schema。

### Python Skill

1. `skills/<name>/__init__.py` 导出 `SKILL = Skill(...)`
2. 参考 `capabilities/skills/base.py`
3. 注册名：`skill:<name>`

### Cursor Skill

1. `.cursor/skills/<name>/SKILL.md`（YAML frontmatter + 正文）
2. 注册名：`cursor-skill:<name>`，工具 `read_<name>()`
3. `disable-model-invocation: true` → `default_enabled=False`，需显式列入 `capabilities`

### MCP Server

开发内置 server → 见 [`../mcps/AGENTS.md`](../mcps/AGENTS.md)。

生产配置 → `POST /api/v1/mcp` 或 `/console/#/configuration`；`transport`: `url` | `stdio` | `builtin`。

### DB Agent

`POST /api/v1/agents` 或 Alembic 种子；流式请求带 `agent_id`（UUID）。

---

## 配置

`.env` 在仓库根，经 `options.py` → `deepfos_platform.conf.option` 加载。

### 本地 Test 调试入口

生产环境继续启动 `deep_agent.main:app`，只使用平台网关注入的身份。需要从本地浏览器调试 Test 数据和服务时，使用独立入口：

```bash
python -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

配置文件必须位于仓库外，且在 macOS/Linux 上执行 `chmod 600`。这个文件作为完整的本地运行 Profile，可同时包含 `DEEP_AGENT_DBMS_*`、`EXPORT_*` 和以下身份项：

```text
DEEP_AGENT_LOCAL_USER=<平台网关注入的用户 ID>
DEEP_AGENT_LOCAL_TOKEN=<当前环境的有效 Token>
DEEP_AGENT_LOCAL_TOKEN_COOKIE=<可选，仅在目标环境要求 Cookie 时填写 Cookie 名>
```

本地入口只监听 `127.0.0.1`。它会覆盖浏览器提交的 `user / authorization / x-user-token / cookie`，保留控制台调试上下文提交的 `app / space / language`。身份和 Token 不得进入源码、仓库、浏览器存储、日志或测试夹具。

注意：应用启动会自动执行 Alembic `upgrade head`。本地 Profile 如果连接共享 Test 数据库，新增迁移会随本地服务启动直接应用；涉及删列等不兼容 DDL 时，必须先协调 Test 服务维护窗口，不能直接启动新代码。

### Agent（`DEEP_AGENT_` 前缀）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODEL` | `openai:gpt-4o` | `<provider>:<model>` |
| `MODEL_BASE_URL` / `MODEL_API_KEY` | — | OpenAI 兼容 API |
| `SYSTEM_PROMPT` | 内置 | 系统指令 |
| `MAX_REQUESTS` | `50` | 全局 loop 上限 |
| `MAX_TOTAL_TOKENS` | 无限制 | Token 上限 |
| `SKILLS_DIR` | `skills` | 相对仓库根 |
| `CURSOR_SKILLS_DIR` | `.cursor/skills` | 相对仓库根 |
| `MCP_FORWARD_REQUEST_HEADERS` | `authorization,x-tenant-id,x-user-token` | 转发到 MCP |
| `MCP_STDIO_SESSION_SCOPE` | `true` | stdio MCP 按 session 保活 |

### 数据库（`DEEP_AGENT_DBMS_` 前缀）

| 变量 | 说明 |
| --- | --- |
| `NAME` | `postgres` \| `mysql`（kingbase/dameng 需额外驱动） |
| `HOSTNAME` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | 连接信息 |
| 全部未设置 | 内存 Store；Agent/MCP CRUD → 503 |

Provider Key（`OPENAI_API_KEY` 等）由 Pydantic AI 直接读取。

### 迁移

```bash
# 仓库根
alembic upgrade head
```

迁移：`alembic/versions/001` … `018`。本地 Postgres：`docker compose up -d`。

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
| | `/api/v1/sessions/*` | 是 |
| | `/console/#/configuration` | 是 |

SSE 协议：[`../docs/sse-protocol.md`](../docs/sse-protocol.md)。前端对接：[`../frontend/AGENTS.md`](../frontend/AGENTS.md)。

---

## 约定与陷阱

0. **重构风格**：事件多态映射集中在 `agent/events.py`；多入口共享组装集中在 `api/v1/deps.py`，变化量留在调用入口
   - 事件多态映射 → `agent/events.py`（`singledispatch` 分层）
   - 多入口共享组装 → `api/v1/deps.py`（变化量留在入口，上下文在 `_build_*` 内统一解包）
1. 包名 **`deep_agent`**，启动 `deep_agent.main:app`（非 `app.main`）
2. 扩展优先走路径注册，避免改 `runner.py` 核心
3. 能力命名：`native` \| `skill:<name>` \| `cursor-skill:<name>` \| `mcp:<name>` \| `ui:<canonical_tool_name>`；扩展 UI 工具由 Agent 正向引用
4. DB Agent 会话：复合键 `(agent_id, session_id)`；无 agent 时 `agent_id=''`
5. 同 session 用 `SessionStore.lock()` 串行；多实例 DB 级锁未实现

| 现象 | 原因 |
| --- | --- |
| MCP 工具为空 | 未配 DB 或 MCP 未 enabled |
| cursor-skill 不出现 | `default_enabled=False`，需显式指定 |
| 历史丢失 | 未配 `DEEP_AGENT_DBMS_*` |
