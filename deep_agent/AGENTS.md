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
| MCP 加载 | `capabilities/mcp/db_loader.py`、`capabilities/mcp/builtin.py` |

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
        └─ mcp          mcp_servers 表 + mcps/ 内置兜底
    → SessionStore（`db/stores/`；`memory/` 已 deprecated）
```

### 启动生命周期（`main.py` lifespan → `AppContext.bootstrap()`）

1. `configure_logging()` → `AppContext.bootstrap()`（见 `core/context.py`）
2. `OPTION` → `build_registry()`（native + skill + cursor-skill）；非 Linux 本地开发时 `options.py` 模块级自动加载 `.env`（可用 `DEEP_AGENT_LOAD_DOTENV` 覆盖）
3. 有数据库：`upgrade_database()` → `bootstrap_database()` → `PostgresSessionStore` / `MySQLSessionStore` / `InMemorySessionStore`
4. `load_and_register_mcp(registry, session)` — DB 加载 MCP；DB 未覆盖的 `mcps/<name>/` 自动兜底
5. `init_context(ctx)` — 路由通过 `Depends(AppContextDep)` 注入
6. 关闭：`ctx.aclose()` → `reset_context()`

无数据库 → `InMemorySessionStore`；`load_and_register_mcp` 跳过，**builtin MCP 也不注册**。调试 MCP 需配置 `DEEP_AGENT_DBMS_*`。

### 对话请求流（`POST /api/v1/chat/stream`）

1. 解析 `StreamChatRequest`；`session_id` 缺省则生成 UUID
2. **无 `agent_id`**：`build_runner(model?, capabilities?)` → `OPTION` 默认 `AgentSpec`
3. **有 `agent_id`**：`build_runner_for_agent()` → `agents` 表 → `AgentSpec.from_row()` → 存储键 `{agent_id}:{session_id}`
4. `AgentRunner.run_stream()`：session 锁 → history → `agent.iter()` → SSE 事件 → `append(new_messages)`
5. `capabilities: null` → 仅 `default_enabled=True`；显式列表按名启用

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
| `events.py` | Pydantic AI 事件 → SSE 字典（`singledispatch` 分层映射，见[重构风格规则](../.cursor/rules/python-refactoring.mdc)） |

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
| `mcp/session_scoped.py` | stdio MCP 按 chat session 保活子进程 |
| `mcp/session_pool.py` | 全局 MCP 会话池 |
| `ui/registry.py` | UI 工具合并、可见性、`prepare_ui_toolset_for_spec` |
| `mcp/ui_discovery.py` | MCP manifest 发现 → `db/services/ui_tool_sync.sync_mcp_tools` |

### `api/v1/`

| 文件 | 路由 |
| --- | --- |
| `chat.py` | `POST /chat`, `POST /chat/stream` |
| `agents.py` | CRUD `/agents` |
| `mcp.py` | CRUD `/mcp`、UI 工具刷新（写后热重载 registry） |
| `ui_tools.py` | CRUD `/ui-tools` |
| `sessions.py` | 会话列表 / 历史 / 删除 |
| `tools.py` | `GET /tools` |
| `deps.py` | `build_runner` / `build_runner_for_agent`（多入口共享组装，见[重构风格规则](../.cursor/rules/python-refactoring.mdc)） |

### `db/`

| 路径 | 职责 |
| --- | --- |
| `deps.py` | `get_db`（只读）、`get_uow`（写事务）、repository Depends 单例 |
| `unit_of_work.py` | 请求级 commit / rollback |
| `factory.py` / `dialect.py` | 方言工厂、`bootstrap_database()` |
| `stores/` | `SessionStore` 实现（postgres / mysql / in-memory） |
| `repositories/` | ORM 访问（无内部 `commit()`） |
| `services/ui_tool_sync.py` | MCP UI 工具同步（来源优先级、override 保护） |
| `session_key.py` / `session_list.py` | 会话键解析、列表过滤 DTO |

| 表 | 用途 |
| --- | --- |
| `agents` | DB Agent（UUID PK，name 唯一） |
| `mcp_servers` | MCP transport + config JSON |
| `ui_tool_definitions` | UI 工具定义（builtin / mcp / manual） |
| `chat_sessions` | 会话元数据 |
| `chat_messages` | `ModelMessage` JSONB 历史 |

### `memory/`（deprecated）

仅 re-export `db/stores` 与 `db/session_key`；新代码请直接 `from deep_agent.db.stores import ...`。

---

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

生产配置 → `POST /api/v1/mcp` 或 `/admin`；`transport`: `url` | `stdio` | `builtin`。

### DB Agent

`POST /api/v1/agents` 或 Alembic 种子；流式请求带 `agent_id`（UUID）。

---

## 配置

`.env` 在仓库根，经 `options.py` → `deepfos_platform.conf.option` 加载。

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

迁移：`alembic/versions/001` … `006`。本地 Postgres：`docker compose up -d`。

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
| | `/admin` | 是 |

SSE 协议：[`../docs/sse-protocol.md`](../docs/sse-protocol.md)。前端对接：[`../frontend/AGENTS.md`](../frontend/AGENTS.md)。

---

## 约定与陷阱

0. **重构风格**：Python 后端重构遵循 [`.cursor/rules/python-refactoring.mdc`](../.cursor/rules/python-refactoring.mdc)
   - 事件多态映射 → `agent/events.py`（`singledispatch` 分层）
   - 多入口共享组装 → `api/v1/deps.py`（变化量留在入口，上下文在 `_build_*` 内统一解包）
1. 包名 **`deep_agent`**，启动 `deep_agent.main:app`（非 `app.main`）
2. 扩展优先走路径注册，避免改 `runner.py` 核心
3. 能力命名：`native` \| `skill:<name>` \| `cursor-skill:<name>` \| `mcp:<name>`
4. DB Agent 会话键：`{agent_id}:{session_id}`
5. 同 session 用 `SessionStore.lock()` 串行；多实例 DB 级锁未实现

| 现象 | 原因 |
| --- | --- |
| MCP 工具为空 | 未配 DB 或 MCP 未 enabled |
| cursor-skill 不出现 | `default_enabled=False`，需显式指定 |
| 历史丢失 | 未配 `DEEP_AGENT_DBMS_*` |
