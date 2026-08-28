# DeepAgent 项目结构与职责

本文说明当前仓库的稳定目录边界。具体实现细节继续以各目录的 `AGENTS.md` 为准。

## 目录总览

```text
deep-agent/
├── deep_agent/       DeepAgent 产品后端
├── frontend/         后端随包发布的一体化 Console
├── mcps/             仓库内置 MCP 启动入口
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
  -> AgentSpec 与工具包解析
  -> Pydantic AI Agent
  -> ToolPackage 选择 -> Function / MCP / External Toolset
  -> SQL 会话与事件持久化
  -> SSE / JSON 响应
```

`deep_agent/` 是业务实现主体。其内部职责为：

- `api/`：HTTP 接口和依赖组装。
- `agent/`：模型、运行器、消息、附件和事件处理。
- `capabilities/`：工具包目录以及 Native、MCP、UI Toolset 适配。
- `files/`：附件与 Skill 文件统一读取、权限校验、分页和 Run内缓存。
- `skills/`：线上 Skill 包校验、目录、运行时物化和只读资源工具。
- `builtin_skills/`：随代码发布、只读的内置 Skill 包。
- `db/`：ORM、Store、会话协调和数据库连接。
- `schemas/`：API 输入输出结构。
- `core/`：应用上下文、日志和通用异常。

## 工具包边界

当前工具包来源只有：

| 名称 | 来源 | 用途 |
| --- | --- | --- |
| `builtin:general` | `deep_agent/capabilities/native/` | 内置“通用工具”，提供 UTC 时间 |
| `builtin:sandbox` | `deep_agent/sandbox/` | 内置“Python 沙箱”，提供无状态 Python 计算 |
| `mcp:<name>` | 数据库或 `mcps/` | 外部或内置 MCP 工具 |
| `ui:builtin` | 代码 | 确认和候选选择 HITL 工具包 |
| `ui:<package>` | `ui_tool_packages` + `ui_tool_definitions` | Agent 显式选择的手工 HITL 工具包 |

分类只是 ToolPackage 的 `category` 字段；ToolPackage 是 Agent 配置单位，Toolset 是运行时
交给 Pydantic AI 的对象。一个包严格包装一个 Toolset，当前不提供包内逐工具授权；未来可在
该 Toolset 上使用过滤包装器实现包内工具选择。

所有工具包只在 `agents.capabilities` 中显式关联后进入Run；新Agent默认空列表。Skill 不属于
工具包，也不进入 `agents.capabilities`。Agent 使用独立 `agents.skills` 选择
Builtin 或上传 Skill；Harness 只先披露名称和简介，模型按需调用 `load_capability` 加载正文。
Skill 不能扩大 MCP 或其他工具授权。

`read_file`等系统工具也不属于 ToolPackage。它们按本轮附件、已选 Skill或 MCP能力自动注入，
只在 Console“系统工具”目录中只读展示，不能写入 `agents.capabilities`。

## 容易混淆的目录

### `deepfos_platform/`

平台运行时依赖，不是 DeepFOS Platform 产品源码，也不是 Agent 能力。它承载
DeepAgent 已经依赖的配置、平台客户端、服务发现和部署适配。

### `maintenance/`

只供开发或运维人员从命令行执行。例如配置数据库备份。这里的程序不会注册为
Pydantic AI Tool，也不会出现在 Console 的 Agent 能力列表。

### `mcps/` 与 `deep_agent/skills/`

- `mcps/` 是 MCP Server 的本地实现或启动入口，运行时仍通过 MCP 协议接入。
- `deep_agent/skills/` 是线上 Skill 管理和运行适配；Skill 只提供任务方法、References 和 Assets，
  不自动授予工具权限；Agent同时关联含脚本Skill和 `builtin:sandbox`时，可以通过条件式系统工具
  `execute_skill_script`执行已索引脚本。

MCP 提供可执行工具；Skill 提供渐进披露的说明和素材。两者生命周期、权限和协议不同，不应混放。
