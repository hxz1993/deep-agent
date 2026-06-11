# deep-agent 项目概览

> 本文档为项目的功能与架构总结，供后续 AI agent 对话快速了解项目上下文。

## 1. 项目定位

**deep-agent** 是一个**通用 AI Agent 后端**，基于 [Pydantic AI](https://ai.pydantic.dev/) 构建，提供可扩展的能力层与 HTTP（REST + SSE）传输层。

职责边界：

- **Pydantic AI 负责**：agent loop、多 provider 切换、tool calling、结构化输出、流式输出、MCP 客户端、usage 限额。
- **本项目负责**：
  - 可扩展的**能力层**（native / skill / cursor-skill / MCP 统一抽象为 toolset）
  - **FastAPI 传输层**（REST + SSE）
  - **会话存储层**（PostgreSQL 持久化，可回退内存实现）
  - **Agent 配置存储**（PostgreSQL，支持 CRUD + 按名称动态组装 Agent）

典型场景：通过 HTTP 与 LLM Agent 对话，Agent 可调用内置工具、Python Skill、Cursor Skill 文档、本地/远程 MCP server 完成任务。

## 2. 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3 |
| Web 框架 | FastAPI + Uvicorn |
| Agent 运行时 | Pydantic AI (`pydantic-ai-slim[openai,anthropic,google,mcp]`) |
| 配置 | pydantic-settings（`.env` + `DEEP_AGENT_` 前缀） |
| 流式 | sse-starlette（SSE） |
| MCP | `pydantic_ai.mcp` + `mcp.server.fastmcp` |
| 数据库 | PostgreSQL + SQLAlchemy 2（async）+ asyncpg + Alembic |
| 其他 | PyYAML（解析 SKILL.md frontmatter）、httpx（native `http_get` 工具） |
| 前端 | 纯 HTML/CSS/JS，无构建工具 |

> 无 `pyproject.toml` / `package.json`，暂无自动化测试。

## 3. 目录结构

```
deep-agent/                      # 仓库根目录
├── README.md
├── frontend/                    # 静态前端（聊天 UI + 管理后台）
├── deep_agent/                  # 后端（FastAPI + Pydantic AI）
│   ├── README.md
│   ├── requirements.txt
│   ├── docker-compose.yml
│   ├── .env / .env.example
│   ├── alembic/
│   ├── app/                     # 后端核心
│   │   ├── main.py
│   │   ├── agent/
│   │   ├── api/v1/
│   │   ├── capabilities/
│   │   ├── core/
│   │   ├── db/
│   │   ├── memory/
│   │   └── schemas/
│   ├── config/mcp.json
│   ├── mcps/
│   └── skills/
└── .cursor/skills/              # Cursor SKILL.md（code-review、commit-helper 等）
```

## 4. 分层架构

```
HTTP (REST/SSE)  →  AgentRunner  →  Pydantic AI Agent  →  Capabilities (toolsets)
                        |                                       ├─ native @tool
                    SessionStore                                ├─ skills/
                  (message_history)                             └─ mcp_servers (PostgreSQL)
```

### 模块依赖关系

```
app/main.py
├── app/core/config.py          (Settings)
├── app/core/logging.py / exceptions.py
├── app/capabilities/__init__.py → build_registry
│   ├── native/builtin.py
│   ├── skills/loader.py → skills/*/__init__.py
│   ├── cursor_skills/loader.py → discovery + parser
│   └── mcp/db_loader.py → pydantic_ai.mcp.MCPToolset
├── app/db/engine.py + models.py
├── app/memory/postgres.py（或 in_memory.py）
└── app/api/v1/router.py
    ├── chat.py → agent.runner + deps.build_runner / build_runner_for_agent
    ├── agents.py → db.repositories.agent
    ├── sessions.py → memory + history
    └── tools.py → registry.describe()

deps.build_runner
├── agent/spec.default_spec
├── agent/factory.build_agent → pydantic_ai.Agent
└── agent/runner.AgentRunner → memory + pydantic_ai events

deps.build_runner_for_agent
├── db.repositories.agent.get_by_name → AgentSpec
└── 同上 build_agent + AgentRunner
```

## 5. 核心模块说明

### app/main.py — 应用入口
- **lifespan 启动时**：配置日志 → 加载 `Settings` → `build_registry()` → 按 `database_url` 选择存储：
  - **已配置** `DEEP_AGENT_DATABASE_URL`：创建 async SQLAlchemy engine + `PostgresSessionStore`。
  - **未配置**：回退 `InMemorySessionStore`（便于无 DB 本地调试）。
- 关闭时 `dispose()` 数据库 engine。
- 注册 CORS（`allow_origins=["*"]`），挂载 `/api/v1` 路由。
- 全局异常：`DeepAgentError` → 400；`AgentNotFoundError` → 404。
- 元接口：`GET /health`、`GET /`。

### app/agent — Agent 运行时
- **`AgentSpec`** (`spec.py`)：声明式 Agent 配置（model、instructions、capabilities 子集、可选 OpenAI 兼容 `base_url`/`api_key`）。
- **`build_agent`** (`factory.py`)：解析 model 字符串（如 `openai:gpt-4o`）或自定义 endpoint，从 Registry 取 toolsets，构造 `pydantic_ai.Agent`（`deps_type=AgentDeps`，`retries=2`）。
- **`AgentRunner`** (`runner.py`)：与 FastAPI 解耦的纯 async 入口。
  - `run()`：加 session 锁 → 读 history → `agent.run()` → 持久化 `new_messages()` → 返回 `{output, usage}`。
  - `run_stream()`：`agent.iter()` 迭代节点，把 Pydantic AI 事件映射为 SSE 字典。
- **`AgentDeps`** (`deps.py`)：每轮注入 `session_id`、`user?`、`agent_id?`（DB agent 流式接口用于会话元数据），供工具通过 `RunContext[AgentDeps]` 读取。

### app/capabilities — 能力层
统一抽象 `Capability`：

| kind | 名称格式 | 来源 | default_enabled |
|------|----------|------|-----------------|
| `native` | `native` | `native/builtin.py` | true |
| `skill` | `skill:<name>` | `skills/<name>/__init__.py` 导出 `SKILL` | true |
| `cursor-skill` | `cursor-skill:<name>` | `.cursor/skills/<name>/SKILL.md` | 由 frontmatter 决定 |
| `mcp` | `mcp:<name>` | PostgreSQL `mcp_servers` 表（url / stdio） | true |

`build_registry` 注册顺序：native → skills/ → cursor skills；MCP 在 lifespan 中从数据库加载（`load_and_register_mcp`），写操作后热重载。

当前实际注册能力（推断）：
- `native`：`get_current_time`、`http_get`
- `cursor-skill:code-review`、`cursor-skill:commit-helper`
- `mcp:text_tools`：`word_count`、`char_count`、`slugify`、`reverse`
- `mcp:cubeQuery`：远程 SSE MCP（`http://172.16.1.23:8080/sse`）
- `skill:calculator`：**未启用**（`SKILL = ...` 被注释）

### app/db — 数据库与 Agent 配置
- **ORM 表**（迁移见 `alembic/versions/`）：

| 表 | 说明 |
|----|------|
| `agents` | Agent 配置：`name`(PK)、`model`、`base_url`、`api_key`、`capabilities`(JSONB)、`prompt`、`created_at`、`updated_at` |
| `mcp_servers` | MCP 配置：`name`(PK)、`transport`(url/stdio)、`config`(JSONB)、`description`、`enabled`、`created_at`、`updated_at` |
| `chat_sessions` | 会话元数据：`session_key`(PK)、`agent_name`(FK, 可空)、`user_id`、`created_at`、`updated_at` |
| `chat_messages` | 消息历史：`session_key`、`sequence`、`payload`(JSONB，单条 `ModelMessage`) |

- **`AgentRepository`**：CRUD + `to_spec(row) → AgentSpec`，供 `build_runner_for_agent` 使用。
- 种子数据：迁移插入 `default` agent（与默认 Settings 对齐）。

### app/memory — 会话存储
- **`SessionStore` Protocol**：`get_history` / `append`（可选 `agent_name`、`user_id`）/ `list_sessions` / `delete`，存储 Pydantic AI `ModelMessage`。
- **`PostgresSessionStore`**：消息写入 JSONB 时使用 `TypeAdapter.dump_python(..., mode="json")`，避免 `datetime` 等类型无法 JSON 序列化；按 `sequence` 有序读写；进程内 `asyncio.Lock` 串行化同 session 并发。
- **`InMemorySessionStore`**：`database_url` 为空时回退。
- **`scoped_session_key(agent_id, session_id)`**：带 `agent_id` 的流式请求会话隔离为 `{agent_id}:{session_id}`；未传 `agent_id` 时仍用原始 `session_id`。
- **`model_messages_to_history`** (`history.py`)：将 `ModelMessage[]` 转为前端可渲染时间线（user 文本 + assistant 的 text / tool_call / tool_result）。

### app/schemas — 数据模型
| 模型 | 字段 |
|------|------|
| `ChatRequest` | `message`, `session_id?`, `model?`, `capabilities?`, `user?` |
| `StreamChatRequest` | 继承 `ChatRequest`，额外 `agent_id?`（有值时从 DB 加载 Agent，忽略 `model`/`capabilities`） |
| `ChatResponse` | `session_id`, `output`, `usage` |
| `AgentChatRequest` | 历史类型，字段为 `agent_id`, `message`, `session_id?`, `user?`（API 已合并至 `StreamChatRequest`） |
| `AgentCreate` / `AgentUpdate` / `AgentResponse` | Agent CRUD（响应中 `api_key` 脱敏为 `api_key_set`） |
| `SessionHistoryResponse` | `session_id`, `messages[]`（`role` + `content` 或 `parts[]`） |
| `CapabilityDescription` | `name`, `kind`, `description`, `tools[]` |
| `SessionInfo` | `session_id`, `message_count` |

## 6. API 接口

### 元接口
| 方法 | 路径 | 响应 |
|------|------|------|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/` | `{name, model, capabilities[], docs}` |

### /api/v1 — 对话
| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| POST | `/api/v1/chat` | `ChatRequest` | `ChatResponse` |
| POST | `/api/v1/chat/stream` | `StreamChatRequest` | SSE 流（无 `agent_id` 用 Settings 默认 Agent；有 `agent_id` 从 DB 加载） |

### /api/v1 — Agent 配置
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/agents` | 列表（不含 api_key 明文） |
| GET | `/api/v1/agents/{name}` | 详情（`api_key_set` 布尔） |
| POST | `/api/v1/agents` | 创建 |
| PATCH | `/api/v1/agents/{name}` | 部分更新 |
| DELETE | `/api/v1/agents/{name}` | 删除 |

### /api/v1 — MCP 配置
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mcp` | 列表 |
| GET | `/api/v1/mcp/{name}` | 详情 |
| POST | `/api/v1/mcp` | 创建（写后热重载 registry） |
| PATCH | `/api/v1/mcp/{name}` | 部分更新 |
| DELETE | `/api/v1/mcp/{name}` | 删除 |

### /api/v1 — 能力与会话
| 方法 | 路径 | 响应 |
|------|------|------|
| GET | `/api/v1/tools` | `CapabilityDescription[]` |
| GET | `/api/v1/sessions` | `SessionInfo[]` |
| GET | `/api/v1/sessions/{id}` | `SessionInfo` |
| GET | `/api/v1/sessions/{id}/history` | `SessionHistoryResponse`（前端历史回放） |
| DELETE | `/api/v1/sessions/{id}` | 204 |

> Agent / 会话相关接口需配置 `DEEP_AGENT_DATABASE_URL`，否则 `get_db` 返回 503。

**StreamChatRequest 示例（默认 Agent，无 `agent_id`）：**
```json
{
  "message": "现在几点了？",
  "session_id": "optional-hex-id",
  "model": "anthropic:claude-sonnet-4-5",
  "capabilities": ["native", "mcp:text_tools"],
  "user": "user-123"
}
```

**StreamChatRequest 示例（DB Agent，带 `agent_id`）：**
```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "你好",
  "session_id": "sess-001",
  "user": "alice"
}
```
存储键为 `{agent_id}:sess-001`，与未传 `agent_id` 时的裸 `session_id` 隔离。

### SSE 事件（/chat/stream）

> 完整协议文档：[docs/sse-protocol.md](docs/sse-protocol.md)

| event | data 内容 |
|-------|-----------|
| `session` | `{"session_id": "..."}` 或 `{"session_id": "...", "agent_id": "..."}`（DB Agent 模式） |
| `text` | `{"type":"text","delta":"..."}` |
| `tool_call` | `{"type":"tool_call","tool","args","tool_call_id"}`（同 ID 可能推送 2 次，首帧 `args` 可能为 `{}`） |
| `tool_result` | `{"type":"tool_result","tool","result","tool_call_id"}` |
| `final` | `{"type":"final","output","usage"}` |
| `error` | `{"type":"error","message"}` |

## 7. 配置项

### .env 环境变量
| 变量 | 用途 |
|------|------|
| `DEEP_AGENT_MODEL` | 默认模型，格式 `<provider>:<model>`，如 `openai:gpt-4o` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | provider key（Pydantic AI 直接读取，不在 Settings 建模） |
| `DEEP_AGENT_MAX_REQUESTS` | Agent loop 最大 LLM 请求次数（默认 12） |
| `DEEP_AGENT_MAX_TOTAL_TOKENS` | 总 token 上限，空则不限 |
| `DEEP_AGENT_MCP_CONFIG_PATH` | 外部 MCP JSON 路径（默认 `config/mcp.json`） |
| `DEEP_AGENT_MCPS_DIR` | 内置 MCP server 目录（默认 `mcps`） |
| `DEEP_AGENT_SKILLS_DIR` | Python Skill 目录（默认 `skills`） |
| `DEEP_AGENT_CURSOR_SKILLS_DIR` | Cursor SKILL.md 目录（默认 `.cursor/skills`） |
| `DEEP_AGENT_MODEL_BASE_URL` | （config 支持）OpenAI 兼容 API 地址（DeepSeek、Qwen 等） |
| `DEEP_AGENT_MODEL_API_KEY` | （config 支持）配合 base_url 的 API key |
| `DEEP_AGENT_SYSTEM_PROMPT` | （config 支持）系统提示词覆盖 |
| `DEEP_AGENT_DATABASE_URL` | PostgreSQL 连接串，如 `postgresql+asyncpg://postgres:postgres@localhost:5432/deep_agent`；留空则用内存 SessionStore |

### config/mcp.json
Claude 风格 MCP 配置，每个 key 注册为 capability `mcp:<key>`，工具名自动加 `<key>_` 前缀。
- 远程：`{ "url": "http://..." }`（SSE/HTTP transport）
- 本地子进程：`{ "command": "uvx", "args": ["mcp-server-fetch"] }`

当前内容：
```json
{
  "mcpServers": {
    "cubeQuery": { "url": "http://172.16.1.23:8080/sse" }
  }
}
```

## 8. 运行流程

### 启动
```
docker compose up -d          # 可选：启动 Postgres
alembic upgrade head          # 建表 + 种子 default agent
uvicorn app.main:app --reload
  → lifespan: configure_logging() → get_settings() → build_registry()
  → database_url 已配置 ? PostgresSessionStore : InMemorySessionStore()
```

### Chat 流式请求（`/chat/stream`）
1. `chat.py` 解析 `StreamChatRequest`，`session_id = body.session_id or uuid`。
2. **无 `agent_id`**：`build_runner(model, capabilities)` → `build_agent(spec, registry)` 组装 Agent + toolsets；存储键为 `session_id`。
3. **有 `agent_id`**：`build_runner_for_agent(db, agent_id)` 从 `agents` 表加载配置 → `AgentSpec` → `build_agent()`；存储键为 `{agent_id}:{session_id}`。
4. `AgentRunner.run_stream(session_key, message, AgentDeps)`。
5. 从 Store 读 `message_history` → `agent.iter()` 迭代 agent loop。
6. LLM 请求/工具调用过程中实时映射为 SSE 事件（text / tool_call / tool_result）。
7. 结束后 `append(new_messages)` 持久化，发送 `final` 事件（含 output + usage）。

**会话连续性**：同一 `session_id`（及相同 `agent_id` 作用域）多次请求会加载完整历史，run 结束后追加新消息。

**能力筛选**（仅无 `agent_id` 时生效）：`capabilities: null`（默认）→ 仅 `default_enabled=True` 能力；显式传列表 → 只启用指定能力。

### 前端历史会话
- 启动时 `GET /api/v1/sessions` 同步侧边栏会话列表。
- 切换会话时 `GET /api/v1/sessions/{id}/history`，将 `messages` 渲染为用户气泡与 assistant（含工具块）。

## 9. 如何运行

### 后端
```bash
cd deep_agent
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # 编辑 DEEP_AGENT_MODEL、API key、DATABASE_URL
docker compose up -d            # 可选
alembic upgrade head
uvicorn app.main:app --reload
```
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health
- **管理后台**：http://127.0.0.1:8000/admin（Agent / MCP 配置 CRUD，需 PostgreSQL）

### 前端
```bash
cd frontend
python -m http.server 5173
# 浏览器打开 http://localhost:5173
# 侧边栏「管理后台」链接指向后端 /admin
```

### 快速测试
```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "现在几点了？"}'
```

## 10. 扩展点速查

| 目标 | 操作 | 文件 |
|------|------|------|
| 加 native 工具 | `@toolset.tool_plain` 装饰函数 | `app/capabilities/native/builtin.py` |
| 加 Python Skill | 新建 `skills/<name>/__init__.py` 导出 `SKILL` | `skills/` |
| 加 Cursor Skill | 新建 `<dir>/<name>/SKILL.md`（YAML frontmatter） | `DEEP_AGENT_CURSOR_SKILLS_DIR` |
| 加 MCP | 管理后台或 `POST /api/v1/mcp`（url / stdio） | `mcp_servers` 表 |
| 内置 MCP 源码 | `mcps/<name>/server.py` + FastMCP（迁移时导入 DB） | `mcps/` |
| 换模型 | `.env` 或请求体 `model` | — |
| 限定能力 | 请求体 `capabilities: [...]` | — |
| 持久化会话 | 已实现 `PostgresSessionStore`；无 DB 时设空 `DATABASE_URL` | `app/memory/postgres.py` |
| 新增 DB Agent | `POST /api/v1/agents` 或迁移种子 | `agents` 表 |
| 按 DB Agent 对话 | `POST /api/v1/chat/stream`（请求体带 `agent_id`） | `StreamChatRequest` |
| 自定义 OpenAI 兼容 API | `DEEP_AGENT_MODEL_BASE_URL` + `DEEP_AGENT_MODEL_API_KEY` | `.env` |

## 11. 注意事项

1. `skills/calculator` 当前未注册（`SKILL = Skill(...)` 被注释）。
2. `Settings.request_retries` 定义了但 `factory.build_agent` 写死 `retries=2`。
3. 无自动化测试。
4. MCP 连接懒加载：`async with agent` 时才建立 MCP 子进程/网络连接。
5. Cursor Skill 的 `default_enabled` 由 frontmatter `disable-model-invocation` 控制，缺省不自动启用，需显式在 `capabilities` 中指定。
6. 生产化路线（README）：上下文压缩、HITL、OpenTelemetry、多实例 DB 级 session 锁、多租户限流、durable execution、pydantic-graph 编排等为后续规划。
7. DB Agent 同步版（非流式，`POST /chat` 带 `agent_id`）尚未实现；多实例部署时 session 锁仍为进程内 `asyncio.Lock`。
