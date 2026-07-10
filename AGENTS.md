# AGENTS.md — 项目索引

> 面向 AI Agent 的渐进式披露文档。**本文件仅为索引**；具体章节见各子目录下的 `AGENTS.md`。
>
> **排除范围**：`deepfos_platform/` 为平台配置依赖，不在本仓库迭代范围内。

---

## 快速上手（30 秒）

| 项 | 内容 |
| --- | --- |
| **是什么** | 通用 AI Agent 全栈：FastAPI 后端 + Pydantic AI 运行时 + 静态前端 |
| **启动** | 仓库根：`venv\Scripts\python.exe -m uvicorn deep_agent.main:app --reload` |
| **测试** | `venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v` |
| **配置** | 仓库根 `.env`（`DEEP_AGENT_*` / `DEEP_AGENT_DBMS_*` / `DEEP_AGENT_SQLITE_PATH`） |

---

## 子文档索引

按需打开对应目录的 `AGENTS.md`，不必一次读完。

| 你想… | 阅读 |
| --- | --- |
| 改后端、API、能力层、数据库、配置 | [`deep_agent/AGENTS.md`](deep_agent/AGENTS.md) |
| 新增或调试内置 MCP server | [`mcps/AGENTS.md`](mcps/AGENTS.md) |
| 改聊天 UI、admin 或 SSE 客户端 | [`frontend/AGENTS.md`](frontend/AGENTS.md) |
| 写或跑测试 | [`tests/AGENTS.md`](tests/AGENTS.md) |

---

## 仓库结构（鸟瞰）

```
deep-agent/
├── AGENTS.md              ← 本文件（索引）
├── .env                   ← 运行时配置
├── requirements.txt
├── alembic/               ← 数据库迁移（详见 deep_agent/AGENTS.md）
├── deep_agent/            ← 后端 Python 包 → deep_agent/AGENTS.md
├── mcps/                  ← 内置 MCP → mcps/AGENTS.md
├── frontend/              ← 静态前端 → frontend/AGENTS.md
├── tests/                 ← 测试 → tests/AGENTS.md
├── docs/                  ← SSE 协议等人读文档
├── skills/                ← Python Skill（可选）
├── .cursor/skills/        ← Cursor SKILL.md → cursor-skill 能力
└── deepfos_platform/      ← ✗ 排除，勿改
```

---

## 架构一图

```
HTTP → deep_agent/api/v1 → AgentRunner → pydantic_ai.Agent → Capabilities
                                      └→ SessionStore (postgres/mysql/sqlite)
```

能力来源：`native` | `skill:*` | `cursor-skill:*` | `mcp:*`（DB + `mcps/` 兜底）

---

## 其他文档

| 文档 | 用途 |
| --- | --- |
| [`README.md`](README.md) | 人类快速开始 |
| [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) | 历史架构总结（部分路径已过时） |
| [`docs/sse-protocol.md`](docs/sse-protocol.md) | SSE 事件协议详解 |
| [`docs/experience/llm-model-modalities.md`](docs/experience/llm-model-modalities.md) | LLM 模型 `input_modalities` 元数据：原则、来源、刷新与离线兜底 |
| [`docs/experience/ai-title-generator.md`](docs/experience/ai-title-generator.md) | AI 会话标题生成实现经验 |
| [`docs/ui-tools.md`](docs/ui-tools.md) | UI 工具扩展（**内部**，改仓库代码） |
| [`docs/ui-tools-external.md`](docs/ui-tools-external.md) | UI 工具使用（**外部集成方**，不改代码） |
| [`docs/ui-tools-extension-proposals.md`](docs/ui-tools-extension-proposals.md) | UI 工具扩展能力 — 方案对比（产品/架构） |
| [`docs/rfc/0001-ui-tools-mcp-discovery-and-api.md`](docs/rfc/0001-ui-tools-mcp-discovery-and-api.md) | **RFC**：方案 A+B 详细设计（OpenAPI / MCP 协议） |
