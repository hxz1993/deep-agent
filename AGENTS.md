# AGENTS.md — 项目索引

> 面向 AI Agent 的渐进式披露文档。**本文件仅为索引**；具体章节见各子目录下的 `AGENTS.md`。
>
> **边界说明**：`deepfos_platform/` 是随仓库发布的平台运行时支撑包，不是
> Agent 能力目录；只在平台配置、客户端、服务发现或部署运行时任务中修改。

---

## 快速上手（30 秒）

| 项 | 内容 |
| --- | --- |
| **是什么** | 通用 AI Agent 全栈：FastAPI 后端 + Pydantic AI 运行时 + 静态前端 |
| **产品版本** | `deep_agent.__version__` 为唯一来源；当前 `0.3.8`，默认发布递增第三位 |
| **启动** | 本地 Test 联调固定使用下方命令；部署入口才使用 `deep_agent.main:app` |
| **测试** | `python3.12 -m unittest discover -s tests -p "test_*.py" -v` |
| **配置** | 仓库根 `.env`（`DEEP_AGENT_*` / `DEEP_AGENT_DBMS_*` / `DEEP_AGENT_SQLITE_PATH`） |

### 本地 Test 联调固定启动命令

用户要求“启动/重启本地 8000”时，在仓库根直接执行：

```bash
# 首次或缺 .venv-sandbox 时：local_dev 默认会自动 setup；也可手动：
# bash scripts/sandbox_venv/setup.sh

python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

- 该配置连接 Test 数据库，并注入本地可信登录身份。
- 默认先执行启动命令，再用 `/health`、`/console/` 和 `/api/v1/health/sandbox` 快速验证。
- 只有端口占用、启动失败、数据库版本不兼容或用户明确要求审计时，才展开额外检查。
- `subprocess-dev` 默认启用；缺 sandbox 解释器时 `local_dev` 默认跑 `scripts/sandbox_venv/setup.sh`（`--no-sandbox-env` 可关）。

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
├── docs/                  ← 说明与设计文档，入口见 docs/README.md
├── skills/                ← Python Skill（可选）
└── deepfos_platform/      ← 平台配置、客户端、服务发现和部署运行时支撑
```

---

## 架构一图

```
HTTP → deep_agent/api/v1 → AgentRunner → pydantic_ai.Agent → Toolsets
                                      └→ SessionStore (postgres/mysql/sqlite)
```

工具包来源：`builtin:general` | `builtin:sandbox` | `builtin:attachment` | `skill:*` | `mcp:*`
（DB + `mcps/` 兜底）| `ui:builtin` | `ui:<package>`。

### Pydantic AI 运行边界

- DeepAgent 自研 `ToolPackage` 是产品配置单位，负责把分类、展示元数据和恰好一个 Toolset 组织起来；Pydantic AI 的 `AbstractCapability` 只表示压缩、重试等运行扩展，二者不能混用。
- 模型直接接收 Instructions、历史、当前输入和 Tool Definitions。MCP Resource/Prompt 不会自动成为模型工具，必须由宿主读取、注入或显式包装。
- Pydantic AI 负责 Agent loop、参数校验、工具调用、重试、流式事件和 Deferred 原语；DeepAgent 负责 SQL 会话、用户隔离、MCP 配置/Header、HITL、SSE 和 Console。
- 原始 `chat_messages` 始终完整保存；启用全局上下文压缩后，模型输入可复用
  `chat_context_checkpoints` 派生投影。v0.3.4 增加独立主动压缩接口；主动入口不受自动阈值、
  历史长度或现有 Checkpoint 限制，无实际收益时由后端返回 `noop`，
  Console 只展示当前有效压缩边界，不保存压缩审计列表。压缩不得破坏 Tool Call/Result 配对、原始历史、
  分支和 Pending 恢复；模型未配置上下文窗口时自动停用。
- v0.3.5 增加结构化 `context_refs`：原始引用随 `chat_messages` 保存，`metadata` 只用于历史和
  前端，模型输入与摘要只使用安全派生投影。快捷命令仍是业务 Prompt 文本，不新增命令框架。
- v0.3.6 将语义压缩改为一次安全摘要；不再滚动分块或二次激进摘要。瞬时 Provider 错误只以
  完全相同的 Prompt 重试一次，无法安全放入单次请求时自动链路仅临时 Sliding Window，主动
  链路直接失败，二者均不写入 Checkpoint。
- 新的运行时横切能力优先评估 Core 的 `PrepareTools`、Hooks、`ToolSearch` 和 Harness 的 Compaction/Overflow；不要默认把逻辑继续写入 `AgentRunner`。
- Harness 是可选能力库，不是产品平台。会话、身份、配置、HITL、SSE 和 Console 继续由 DeepAgent 负责；引入 Harness 0.x 时必须与 `pydantic-ai-slim` 一起固定准确版本。
- Provider Usage 是 best effort；OpenAI-compatible 或私有端点返回 0 时可能代表缺失。上下文预算必须结合模型窗口元数据和保守估算，不能只依赖 `result.usage`。
- 新上传只保存附件引用；默认关闭的 `builtin:attachment` 工具包通过 1-based
  `offset/limit`、`has_more/next_offset` 渐进读取 TXT、DOCX、XLSX。合法 `file_id`
  必须来自当前原始会话，不能从 Checkpoint 推断。详见
  [`docs/design/06-attachment-parsing/`](docs/design/06-attachment-parsing/)。

---

## 其他文档

| 文档 | 类型与用途 |
| --- | --- |
| [`README.md`](README.md) | 人类快速开始 |
| [`docs/README.md`](docs/README.md) | 文档总入口 |
| [`docs/guides/project-structure.md`](docs/guides/project-structure.md) | **说明**：当前目录、运行链路与职责边界 |
| [`docs/guides/README.md`](docs/guides/README.md) | **说明**：协议、使用指南、实现经验和排障记录 |
| [`docs/design/README.md`](docs/design/README.md) | **设计**：当前方案、研究依据、历史决策和原型 |
| [`docs/design/TODO.md`](docs/design/TODO.md) | **待办**：跨课题唯一待办清单和来源索引 |
