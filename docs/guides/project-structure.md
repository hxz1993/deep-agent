# DeepAgent 项目结构与职责

本文说明当前仓库的稳定目录边界。具体实现细节继续以各目录的 `AGENTS.md` 为准。

## 目录总览

```text
deep-agent/
├── deep_agent/       DeepAgent 产品后端
├── frontend/         后端随包发布的一体化 Console
├── mcps/             仓库内置 MCP 启动入口
├── skills/           Python Skill 扩展
├── deepfos_platform/ 平台配置、客户端、发现和部署运行时支撑
├── alembic/          数据库结构迁移
├── maintenance/      人工执行的维护程序，不进入 Agent 工具列表
├── tests/            Python、接口、MCP 和 Console 测试
└── docs/             当前说明与历史设计
```

## 运行链路

```text
FastAPI API
  -> 请求身份和上下文
  -> AgentSpec 与能力解析
  -> Pydantic AI Agent
  -> Native / Python Skill / MCP / UI Tool
  -> SQL 会话与事件持久化
  -> SSE / JSON 响应
```

`deep_agent/` 是业务实现主体。其内部职责为：

- `api/`：HTTP 接口和依赖组装。
- `agent/`：模型、运行器、消息、附件和事件处理。
- `capabilities/`：Native、Python Skill、MCP 和 UI Tool 适配。
- `db/`：ORM、Store、会话协调和数据库连接。
- `schemas/`：API 输入输出结构。
- `core/`：应用上下文、日志和通用异常。

## 能力边界

当前能力来源只有：

| 名称 | 来源 | 用途 |
| --- | --- | --- |
| `native` | `deep_agent/capabilities/native/` | 进程内基础函数工具 |
| `attachment` | `deep_agent/capabilities/attachments/` | Agent 显式启用的会话附件按需读取 |
| `skill:<name>` | `skills/<name>/` | Instructions 与 Python 工具组合 |
| `mcp:<name>` | 数据库或 `mcps/` | 外部或内置 MCP 工具 |
| `ui:<name>` | 代码或数据库 | Agent 显式选择的 builtin/manual HITL 工具 |

仓库不再读取 Cursor 的 `.cursor/skills`。原实现只是把 Cursor Markdown Skill
适配成读取指令工具，与业务 Python Skill 不同。未来若需要长 Skill 按需加载，
应在通用 `skill:` 机制中设计渐进披露，而不是重新引入 Cursor 专属能力类型。

## 容易混淆的目录

### `deepfos_platform/`

平台运行时依赖，不是 DeepFOS Platform 产品源码，也不是 Agent 能力。它承载
DeepAgent 已经依赖的配置、平台客户端、服务发现和部署适配。

### `maintenance/`

只供开发或运维人员从命令行执行。例如配置数据库备份。这里的程序不会注册为
Pydantic AI Tool，也不会出现在 Console 的 Agent 能力列表。

### `mcps/` 与 `skills/`

- `mcps/` 是 MCP Server 的本地实现或启动入口，运行时仍通过 MCP 协议接入。
- `skills/` 是 DeepAgent 进程内加载的 Python 扩展，可同时提供 Instructions
  和 `FunctionToolset`。

两者都能向模型提供工具，但生命周期、协议和部署方式不同，不应混放。
