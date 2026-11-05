# DeepAgent 后端架构

## 目录

- 请求与启动流程
- 运行时职责边界
- 持久化事实与投影
- 模型与目录边界
- 身份、请求头与缓存安全

## 请求与启动流程

```text
FastAPI /api/v1
  -> trusted request identity + forwarded-header extraction
  -> api/v1/deps.py builds AgentSpec/AgentRunner
  -> AgentRunner acquires session lock and loads the effective Event Surface
  -> pydantic_ai.Agent + Toolsets + runtime AbstractCapabilities
  -> complete Pydantic Message Events + live SSE
  -> Event, Conversation History, and context-status projections
```

普通启动过程会执行写入操作（显式只读预览改用 `local_preview.preview_lifespan`，不执行此初始化链）：

```text
main lifespan
  -> AppContext.bootstrap()
  -> Alembic upgrade head
  -> bootstrap stores and SqlConversationCoordinator
  -> build static ToolPackageRegistry
  -> initialize routes/context
```

当前值得关注的源码区域：

| 关注点 | 源码 |
| --- | --- |
| API 路由 | `deep_agent/api/v1/` |
| 共用运行器组装 | `deep_agent/api/v1/deps.py` |
| 请求身份 | `deep_agent/api/v1/request_identity.py` |
| 请求头转发 | `deep_agent/agent/headers.py` |
| Agent 构建 | `deep_agent/agent/factory.py` |
| 运行器 | `deep_agent/agent/runner.py` |
| Event 映射 | `deep_agent/agent/events.py` |
| Conversation 投影 | `deep_agent/conversation/projector.py` |
| Event 编解码与持久化 | `deep_agent/db/event_codec.py`, `deep_agent/agent/event_persister.py` |
| 持久化协调器 | `deep_agent/db/conversation.py` |
| 表存储层 | `deep_agent/db/stores/` |
| 工具包 | `deep_agent/capabilities/` |
| 上下文预算与压缩 | `deep_agent/agent/context/` |
| MCP 运行时 | `deep_agent/capabilities/mcp/` |

请在当前分支上确认路径；不要把这份列表当作规划中的模块已经实现的证据。

## 职责边界

### DeepAgent 负责

- HTTP/SSE API、经认证的身份和请求上下文。
- 会话归属、重命名、删除、历史、Deferred 恢复和手动压缩。
- 按顺序持久化 `chat_session_events`，以及从最新完成的 Compaction Event 恢复模型可见上下文（Surface）。
- 将 Pydantic AI Message 映射为稳定的 Event 和 ConversationItem/SSE 契约。
- ToolPackage 目录、Agent 工具包选择、MCP 配置与运行时、UI Tool 传输、附件及 Sandbox 集成。
- 作为运行时事实的模型预设，以及作为管理员元数据的 models.dev 目录刷新。
- 随附的 `/console/` 运维界面。

### Pydantic AI 负责

- Agent 循环、Model 请求与响应、Function/MCP/External Toolset 原语、参数校验、用量统计、流式事件及 Deferred 原语。
- 为运行时横切行为提供 `AbstractCapability` 生命周期钩子。

不要将 DeepAgent 的 `ToolPackage` 重命名为 Pydantic AI 的 `AbstractCapability`，也不要混淆两者。

### 启用时由 Harness 负责

- DeepAgent 明确采用的可复用压缩、溢出处理和钩子算法。

Harness 不负责 SQL 会话、身份、Console、配置或产品持久化。Pydantic AI 与 Harness 必须固定为经过联合测试的版本组合。

### DeepCube 负责

- DeepCube MCP 工具 Schema，以及 Cube、成员和查询语义。
- 包与 Rust 构建、元数据、编译器及计算行为。

在 DeepCube 中修复包缺陷，将验证过的 wheel 安装到 DeepAgent 使用的解释器中，重启后通过 DeepAgent 验证。

### 外部前端负责

- 业务专属的上下文引用构建，以及 Chip 标签与详情交互。
- 业务专属的结构化渲染和 UI Tool 组件。
- 各自的代理和浏览器回归测试。

DeepAgent 仍须负责提供有效的传输与历史契约。不要在 DeepAgent 任务中编辑其他前端仓库。
向其负责人提供 History 测试夹具、SSE 测试夹具、后端 SHA、Test 环境的会话与运行 ID，以及准确的预期顺序。

## 持久化事实与投影

当前配置与运行时表包括：

```text
agents
agent_output_configs
llm_models
model_catalog
mcp_servers
ui_tool_packages
ui_tool_definitions
chat_sessions
chat_session_events
```

规则：

- chat_session_events是有序事实源；完整Message、带source的Part事件及混合旧历史都可能存在，按真实来源和Part位置恢复同一消息，不能假设每条模型响应只有一个Event。Compaction和Title是独立语义事件，配置清理不改写既有消息／结果。
- `/events` 是轨迹与审计投影；`/history` 是普通的 `ConversationItem[]` 投影。两者
  都不是另一套持久化模型。
- 实时 SSE 渲染当前 Run，并在正常完成后保持可见；`/history` 是最终持久化的
  Conversation 投影，在切换、刷新、重连或重新打开时使用。
- 已完成的 Compaction Event 是派生的模型输入锚点。压缩绝不改写原始 Message Event。
- Context Reference 的 `payload` 和 `metadata` 会被持久化；模型投影排除 `metadata`。
- 工具检测结果、模型目录和agent_output_configs属于配置，不是聊天结果事实。原生输出output/attempt在同一Event Store中保存；accepted投影为structured_output，失败仅供Events/轨迹。输出定义每Run从当前配置装配，在内存完成校验，不复制为会话快照、引用或在接受事务回查；其他既有历史机制不据此扩大清理。见[结构化输出](structured-output.md)。

## 模型与目录边界

- 将 `llm_models` 作为 Agent 运行时模型事实的唯一来源，涵盖 Provider 连接、模型代码、输入模态、上下文窗口、最大输出及标签。
- 将 `model_catalog` 视为仅用于浏览与预填的数据库快照。从 models.dev 手动刷新时，必须完整校验并在事务中替换。
- 运行时绝不能根据模型名称推断上下文窗口或模态。
- 所选运行时模型缺少有效的 `context_window_tokens` 时，禁用上下文压缩。
- 会话标题和压缩使用当前生效的 Agent 模型或覆盖模型；不存在辅助模型表，
  也不存在标题或摘要模型配置。
- 保存模型或执行 Agent 时，不得为目录发起网络请求。

## 身份与请求头流转

1. 从可信网关或仅限回环地址的 `local_dev` 配置解析用户。
2. 对每项会话操作校验归属，包括历史、重命名、压缩和删除。
3. 按当前请求上下文规则推导测试用的 App/Space/Language；不要接受浏览器提供的身份。
4. 仅向 URL MCP 转发配置的请求头白名单。对于支持的 stdio/builtin MCP，显式提供等价的请求上下文。

权限敏感的缓存必须包含足够的作用域：

```text
environment/host + app + space + user/authority + language + resource identity
```

若无法避免以 Token 标识身份，须对其进行哈希；绝不能在缓存键中记录或持久化原始 Token。
