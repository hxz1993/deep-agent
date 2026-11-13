# Agent 发布的四种接入方式

2026-11-13 · [45 号课题](README.md)设计稿。下文的产品接口、工具名、安装包名和代码是拟议契约。

## 一个发布编码，一套任务服务

发布编码如 `budget-variance-report` 是对外稳定标识。A2A 通过该发布的 Agent Card 和调用地址找到它，MCP、CLI 和 Python SDK 在同环境的统一入口用该编码指定发布。四个入口调用同一服务，复用权限校验、任务状态、补答、取消与产物授权。CLI 调用 Python 客户端。MCP 通过工具暴露这套任务能力。

```text
A2A per-publication URL ─────────┐
MCP /api/v1/agent-service/mcp ─────────────────┼─ 同一发布、鉴权、任务与产物服务
CLI → Python 客户端 → 统一 HTTP ┘
```

## 统一路由前缀

四种入口共用 `/api/v1/agent-service`，按用途使用下一段路径。

| 用途 | 路径 |
| --- | --- |
| Console 发布配置 | `GET/POST /api/v1/agent-service/publications`，单条发布用 `/{code}` |
| A2A 发现 | `GET /api/v1/agent-service/a2a/{code}/agent-card.json` |
| A2A JSON-RPC | `POST /api/v1/agent-service/a2a/{code}`，调用地址由 Card 的 `supportedInterfaces[].url` 声明 |
| Agents via MCP | `/api/v1/agent-service/mcp`，环境级 Streamable HTTP 入口 |
| CLI 和 Python SDK | 共用 HTTP 接口，例如 `GET /api/v1/agent-service/agents` 列出可调用的发布 |

`/api/v1` 是 DeepAgent 路由版本。A2A 调用仍以 `A2A-Version: 1.0` 表达协议版本。多发布场景提供每张 Card 的完整 URL。已有 `/api/v1/integrations/{code}` 继续服务现有场景配置。[A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)

## 功能对照

CLI 示例省略命令前缀 `deepfos-agent`，Python 示例省略客户端初始化。表内 `p`、`t`、`a` 分别是发布编码、Task ID、App ID。MCP 的 `app` 由请求上下文或工具参数提供，下面以工具参数为例。

| 功能 | A2A 1.0／DeepAgent 扩展 | MCP 工具 | CLI 子命令 | Python SDK |
| --- | --- | --- | --- | --- |
| 列出有权调用的发布 | 使用独立目录。标准没有统一的批量 Card API | `list_agents()` | `agents list` | `client.list_agents()` |
| 查看发布 | 单发布 Agent Card HTTP GET | `get_agent(code=p, app=a)` | `agents get <code> --app <app>` | `client.get_agent(p, app=a)` |
| 提交任务 | `SendMessage` | `submit_task(code=p, app=a, text="生成报告")` | `tasks submit <code> --app <app> --text "生成报告"` | `client.submit(p, app=a, text="生成报告")` |
| 边执行边看 | `SendStreamingMessage` | 先 `submit_task`，再 `get_task` 查进度 | `tasks submit … --follow` | `client.submit_stream(p, app=a, text="生成报告")` |
| 查询任务与结果 | `GetTask` | `get_task(task_id=t, app=a)` | `tasks get <id> --app <app>` | `client.get_task(t, app=a)` |
| 列出近期任务 | `ListTasks` | `list_tasks(code=p, app=a)` | `tasks list --agent <code> --app <app>` | `client.list_tasks(p, app=a)` |
| 在原任务补材料 | `SendMessage`，带原 Task ID | `reply_task(task_id=t, app=a, text="2026-10")` | `tasks reply <id> --app <app> --text "2026-10"` | `client.reply(t, app=a, text="2026-10")` |
| 跟踪已有任务 | `SubscribeToTask` | `get_task` 按需查询 | `tasks watch <id> --app <app>` | `client.subscribe(t, app=a)` |
| 取消任务 | `CancelTask` | `cancel_task(task_id=t, app=a)` | `tasks cancel <id> --app <app>` | `client.cancel(t, app=a)` |
| 读取交付文件 | 从 Artifact 读授权引用。文件下载是 DeepAgent 扩展 | `get_artifact(task_id=t, artifact_id=id, app=a)` 返回可读引用 | `artifacts download <id> <artifact> --app <app> -o report.xlsx` | `client.download_artifact(t, id, app=a, destination="report.xlsx")` |
| 推送通知配置 | Create／Get／List／Delete 四个标准方法，现阶段暂缓 | 暂缓 | 暂缓 | 暂缓 |
| 扩展 Agent Card | `GetExtendedAgentCard`，现阶段暂缓 | 暂缓 | 暂缓 | 暂缓 |

MCP 的 `tools/list` 返回工具定义，`list_agents` 才返回当前调用方可用的业务发布。基础 MCP 客户端可用 Task ID 提交和轮询。MCP Tasks 扩展与订阅能力按实际宿主验证。Artifact 取件需要验证真实宿主能读到文件字节，例如使用 `resource_link` 和授权的 `resources/read`。CLI 和 SDK 仅在实际返回附件时下载。

## App 和身份

- **列表可省 App，身份必传。** 用平台 Token 取调用方有权访问的 App，再应用发布范围。把调用方有权访问的集合与发布配置取交集。只返回有交集的启用发布及调用者有权看到的 App。当前已知 `checkApiKey` 需要 App 和 Space，Key 模式先要求配置一个 App／Space，直到实际确认可安全做无 App 的 Key 查询。
- **单发布详情和任务操作需要 App。** A2A／CLI／SDK 通过 HTTP Header 送入 `app`、`space` 和平台凭据，语言可选。MCP 宿主的凭据放连接配置。若宿主只能设置固定 Header，可以由业务工具传 `app`，服务端校验后转为规范上下文。两个位置同时传时必须一致。API Key 仅能用于其授权范围。
- **校验后再传。** 网关或平台解析主体，校验发布启用、App 在允许范围、主体有 App 权限以及 Task 与 App／主体匹配。只传下游所需且允许的凭据和业务字段。禁止直接信任外部 `user`、转发任意 Header 或把平台密钥暴露给模型。补答、查任务和取件沿用原任务 App。

## 安装与 Console

| 入口 | 客户端如何接入 | Console 怎么放 |
| --- | --- | --- |
| A2A | 使用支持 A2A 的客户端。自行开发时可用 A2A SDK 或 HTTP 客户端，配置所选发布的 Agent Card URL 和平台请求 Header | 选择 A2A API 后显示当前发布的 Card 地址和复制按钮 |
| Agents via MCP | 在现有 MCP 客户端添加一个环境级 Streamable HTTP 地址，拟为 `https://HOST/api/v1/agent-service/mcp`，配置环境凭据。用 `list_agents` 查看可用发布 | 安装说明提供服务器地址、传输方式和凭据配置 |
| CLI | 独立发布 CLI 包。安装候选 `uv tool install deepfos-agent-cli`，在企业包源发布后使用。通过 CLI 配置环境及凭据来源 | 提供安装说明 |
| Python SDK | 优先在现有 `deepfos` 包增加轻量客户端，候选模块 `deepfos.agent`，共用 HTTP 契约。先确认其 Python 版本和依赖兼容 | 提供 DeepFOS SDK 安装及使用说明 |

MCP、CLI 和 SDK 按环境接入一次，各自可列出调用者有权使用的发布。选择发布只更新 A2A 卡片的内容。当前原型给出 A2A Card 地址与简要用法，另外三张接入卡先占位。上表的安装说明为后续正式界面的设计。

**CLI 可附带一个可选的调用 Skill。** 人工终端和业务脚本安装 CLI 即可。让 Coding Agent 通过终端调用时，可以再给它安装一份 Skill，说明先查可用发布、如何选择应用、提交、查询状态及取件。Skill 随 CLI 命令契约维护，只保存调用指引。CLI 独立安装，凭据由宿主提供，权限仍由服务端校验。支持 MCP 的 Agent 可直接使用工具定义，Python 程序直接用 SDK。

CLI 和 SDK 的接入示例如下。接口名为设计稿，凭据由环境变量或企业凭据管理提供。

```bash
uv tool install deepfos-agent-cli
deepfos-agent agents list
deepfos-agent tasks submit budget-variance-report --app finops001 --text "生成 10 月预算差异报告"
```

```python
from deepfos.agent import AgentClient

async with AgentClient.from_env() as client:
    agents = await client.list_agents()
    task = await client.submit("budget-variance-report", app="finops001", text="生成 10 月预算差异报告")
    result = await client.wait(task.id, app="finops001", timeout=120)
```

## 服务端联调前确认

1. Token 无 App 时可用的权限目录，API Key 能否独立验证作用域，以及目标 A2A／MCP 客户端能否发送所需 Header。
2. Task 的超时、并发、断线重连与重启后的行为，及客户端取件授权和保留期。
3. SDK 模块归属、企业包源和最低 Python 版本。CLI 优先复用客户端，请求与发布协议独立发版。

规范参考 [A2A 1.0.1](https://a2a-protocol.org/v1.0.1/specification/) · [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) · [uv 工具安装](https://docs.astral.sh/uv/concepts/tools/)。
