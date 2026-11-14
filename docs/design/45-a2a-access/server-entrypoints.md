# Agent 发布的四种接入方式

2026-11-13 · [45 号课题](README.md)设计稿。下文的产品接口、工具名、安装包名和代码是拟议契约。

## 一个 Agent 编码，一套任务服务

Agent 编码如 `budget-variance-report` 是对外稳定标识。A2A 通过该发布的 Agent Card 和调用地址找到它，MCP、CLI 和 Python SDK 在同环境的统一入口用该编码指定发布。四个入口调用同一服务，复用权限校验、任务状态、补答、取消与产物授权。CLI 调用 Python 客户端。MCP 通过工具暴露这套任务能力。

```text
A2A per-publication URL ─────────┐
MCP /api/v1/agent-service/mcp ─────────────────┼─ 同一发布、鉴权、任务与产物服务
CLI → Python 客户端 → 统一 HTTP ┘
```

## 统一路由前缀

四种入口共用 `/api/v1/agent-service`，按用途使用下一段路径。

| 用途 | 路径 |
| --- | --- |
| Console 发布配置 | `GET/POST /api/v1/agent-service/publications`，单条发布用 `/{agent_code}` |
| A2A 发现 | `GET /api/v1/agent-service/a2a/{agent_code}/agent-card.json` |
| A2A 指定 App | 在 A2A 基址后追加 `/apps/{app_id}`，Card、RPC 和取件共用该路径段 |
| A2A JSON-RPC | `POST /api/v1/agent-service/a2a/{agent_code}`，调用地址由 Card 的 `supportedInterfaces[].url` 声明 |
| Agents via MCP | `/api/v1/agent-service/mcp`，环境级 Streamable HTTP 入口 |
| CLI 和 Python SDK | 共用 HTTP 接口，例如 `GET /api/v1/agent-service/agents` 列出可调用的发布 |

`/api/v1` 是 DeepAgent 路由版本。A2A 调用仍以 `A2A-Version: 1.0` 表达协议版本。多发布场景提供每张 Card 的完整 URL。已有 `/api/v1/integrations/{code}` 继续服务现有场景配置。[A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)

## 功能对照

CLI 示例省略命令前缀 `deepfos-agent`，Python 示例省略客户端初始化。表内 `p`、`t`、`a` 分别是 Agent 编码、Task ID、App ID。MCP 的 `app` 由请求上下文或工具参数提供，下面以工具参数为例。

| 功能 | A2A 1.0／DeepAgent 扩展 | MCP 工具 | CLI 子命令 | Python SDK |
| --- | --- | --- | --- | --- |
| 列出有权调用的发布 | 使用独立目录。标准没有统一的批量 Card API | `list_agents()` | `agents list` | `client.list_agents()` |
| 查看发布 | 单发布 Agent Card HTTP GET | `get_agent(agent_code=p, app=a)` | `agents get <agent_code> --app <app>` | `client.get_agent(agent_code=p, app=a)` |
| 提交任务 | `SendMessage` | `submit_task(agent_code=p, app=a, text="生成报告")` | `tasks submit <agent_code> --app <app> --text "生成报告"` | `client.submit(agent_code=p, app=a, text="生成报告")` |
| 边执行边看 | `SendStreamingMessage` | 先 `submit_task`，再 `get_task` 查进度 | `tasks submit … --follow` | `client.submit_stream(p, app=a, text="生成报告")` |
| 查询任务与结果 | `GetTask` | `get_task(task_id=t, app=a)` | `tasks get <id> --app <app>` | `client.get_task(t, app=a)` |
| 列出近期任务 | `ListTasks` | `list_tasks(agent_code=p, app=a)` | `tasks list --agent <agent_code> --app <app>` | `client.list_tasks(agent_code=p, app=a)` |
| 在原任务补材料 | `SendMessage`，带原 Task ID | `reply_task(task_id=t, app=a, text="2026-10")` | `tasks reply <id> --app <app> --text "2026-10"` | `client.reply(t, app=a, text="2026-10")` |
| 跟踪已有任务 | `SubscribeToTask` | `get_task` 按需查询 | `tasks watch <id> --app <app>` | `client.subscribe(t, app=a)` |
| 取消任务 | `CancelTask` | `cancel_task(task_id=t, app=a)` | `tasks cancel <id> --app <app>` | `client.cancel(t, app=a)` |
| 读取交付文件 | 从 Artifact 读授权引用。文件下载是 DeepAgent 扩展 | `get_artifact(task_id=t, artifact_id=id, app=a)` 返回可读引用 | `artifacts download <id> <artifact> --app <app> -o report.xlsx` | `client.download_artifact(t, id, app=a, destination="report.xlsx")` |
| 推送通知配置 | Create／Get／List／Delete 四个标准方法，现阶段暂缓 | 暂缓 | 暂缓 | 暂缓 |
| 扩展 Agent Card | `GetExtendedAgentCard`，现阶段暂缓 | 暂缓 | 暂缓 | 暂缓 |

MCP 的 `tools/list` 返回工具定义，`list_agents` 才返回当前调用方可用的业务发布。基础 MCP 客户端可用 Task ID 提交和轮询。MCP Tasks 扩展与订阅能力按实际宿主验证。Artifact 取件需要验证真实宿主能读到文件字节，例如使用 `resource_link` 和授权的 `resources/read`。CLI 和 SDK 仅在实际返回附件时下载。

## App 和身份

- **`list_agents` 只传凭据。** 返回调用者可见的已启用发布。目录不接受 App 筛选。平台 Token 可单独确认主体，App 级 API Key 的目录能力留待平台提供独立校验。
- **目录之外的调用明确选择 App。** A2A 通用入口传 `app` Header，指定 App 的入口按路径。CLI、SDK 和 MCP 计划在业务调用显式传 App。服务端根据所选应用确定 Space，核对发布允许范围及调用者权限。
- **校验后再传。** 网关或平台解析主体，校验发布启用、App 在允许范围、主体有 App 权限以及 Task 与 App／主体匹配。只传下游所需且允许的凭据和业务字段。禁止直接信任外部 `user`、转发任意 Header 或把平台密钥暴露给模型。补答、查任务和取件沿用原任务 App。

## 安装与 Console

| 入口 | 客户端如何接入 | Console 怎么放 |
| --- | --- | --- |
| A2A | 使用支持 A2A 的客户端。自行开发时可用 A2A SDK 或 HTTP 客户端，配置所选发布的 Agent Card URL 和平台请求 Header | 选择 A2A API 后显示当前发布的 Card 地址和复制按钮 |
| Agents via MCP | 在现有 MCP 客户端添加环境级 Streamable HTTP 地址 `https://HOST/api/v1/agent-service/mcp`，在客户端配置凭据。用 `list_agents` 查看可用发布 | 服务器地址、复制地址、连接说明 |
| CLI | 配置企业包源后运行 `uv tool install deepfos-agent-client`，也可从 wheel 安装。使用 `deepfos-agent` 命令，配置环境及凭据来源 | 安装命令、复制命令、安装说明、可选的「下载调用 Skill」 |
| Python SDK | 在业务项目虚拟环境里从企业包源运行 `pip install deepfos-agent-client`，导入拟议的 `deepfos_agent_client` 模块。CLI 使用同一套客户端代码 | 安装命令、复制命令、SDK 文档 |

MCP、CLI 和 SDK 按环境接入一次，各自可列出调用者有权使用的发布。选择发布只更新 A2A 卡片的内容。三种通用接入方式按页签显示安装或连接入口，配置细节与调用示例放入相应说明。正式 Console 当前保留三项占位，包名与安装命令在分发渠道确认前仅用于设计。

**CLI 可附带一个可选的调用 Skill。** 人工终端和业务脚本安装 CLI 即可。让 Coding Agent 通过终端调用时，可以再给它安装一份 Skill，说明先查可用发布、如何选择应用、提交、查询状态及取件。Skill 随 CLI 命令契约维护，只保存调用指引。CLI 独立安装，凭据由宿主提供，权限仍由服务端校验。支持 MCP 的 Agent 可直接使用工具定义，Python 程序直接用 SDK。

CLI 和 SDK 的接入示例如下。接口名为设计稿，凭据由环境变量或企业凭据管理提供。

```bash
uv tool install deepfos-agent-client
deepfos-agent agents list
deepfos-agent tasks submit budget-variance-report --app finops001 --text "生成 10 月预算差异报告"
```

```python
from deepfos_agent_client import AgentClient

async with AgentClient.from_env() as client:
    agents = await client.list_agents()
    task = await client.submit(agent_code="budget-variance-report", app="finops001", text="生成 10 月预算差异报告")
    result = await client.wait(task.id, app="finops001", timeout=120)
```

## 服务端联调前确认

1. Token 无 App 时可用的权限目录，API Key 能否独立验证作用域，以及目标 A2A／MCP 客户端能否发送所需 Header。
2. Task 的超时、并发、断线重连与重启后的行为，及客户端取件授权和保留期。
3. SDK 模块归属、企业包源和最低 Python 版本。CLI 优先复用客户端，请求与发布协议独立发版。

规范参考 [A2A 1.0.1](https://a2a-protocol.org/v1.0.1/specification/) · [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) · [uv 工具安装](https://docs.astral.sh/uv/concepts/tools/)。
