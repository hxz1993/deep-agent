# Agent 发布的四种接入方式

> **0.17.0a4 发布状态**　正式启用 A2A 和 MCP。本文中的 CLI、SDK、通用 HTTP 调用和安装包分发均为保留的开发草案，未在产品注册，后续可能改写或移除。MCP 的鉴权取件路径继续开放。

[45 号课题](README.md)。A2A、MCP、公共 HTTP、CLI 和 Python SDK 共用同一套发布与任务服务。企业分发地址由部署方配置。

## 一个 Agent 编码，一套任务服务

发布仍用现有 Agent 编码、启用状态和 `scope.apps`。A2A 从 Agent Card 找到发布地址。MCP、CLI 和 Python SDK 连接环境后先列目录，提交时用 `agent_code` 指定发布，查询、补答、取消和取件用返回的 Task ID。

```text
A2A 客户端 ── Agent Card / JSON-RPC ─────────┐
MCP 宿主 ──── Streamable HTTP / tools/call ──┼─ 平台鉴权 → 发布与任务服务 → Runner / TaskStore / FileStore
CLI ─→ Python 客户端 ── 公共 HTTP ──────────┘
```

三种新增入口共用现有发布记录、权限检查、Runner、近期任务和 FileStore。A2A Handler 调用共享任务服务。A2A 层保留官方 SDK、协议消息、Card 和地址生成。MCP 与 HTTP 在同一进程调用共享服务。CLI 直接复用 Python SDK。任务和 Context 继续使用进程内存，部署需要保障同一任务由同一任务存储处理。Console 近期任务按服务端实际入口记录 A2A、MCP 或 HTTP。CLI 和 SDK 可提供客户端标识供诊断，身份与权限均由平台凭据决定。

| **用途** | **路径** |
| --- | --- |
| Console 发布管理 | `/api/v1/agent-service/publications` |
| A2A 通用入口 | `/api/v1/agent-service/a2a/{agent_code}` 下的 Card、RPC 和取件 |
| A2A 带 App 入口 | `/api/v1/agent-service/a2a/{agent_code}/apps/{app_id}` 下的 Card、RPC 和取件 |
| Agents via MCP | `/api/v1/agent-service/mcp`，一个环境级 Streamable HTTP 地址 |
| CLI 和 Python SDK | `/api/v1/agent-service/agents` 和 `/api/v1/agent-service/tasks` 下的公共 HTTP 操作 |

MCP 连接使用 URL、凭据和 App Header。工具名固定，发布开关变动体现在目录结果里。需要同时接两个应用时，可在客户端配置两条连接，共用一个 URL，各自填写 App 和对应凭据。HTTP 的 `/api/v1` 是 DeepAgent API 版本，A2A 协议版本仍由 Card 和 `A2A-Version` 表达。 [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) · [A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)

## 目录、鉴权和 App

| **请求** | **凭据与 App 来源** | **服务端检查** |
| --- | --- | --- |
| MCP `list_agents()`、CLI `agents list`、SDK `list_agents()` | 从连接或客户端配置取凭据。API Key 需要 `app` 来验 Key，平台 Token 可单独验身份。目录操作无 App 过滤参数 | 返回同一环境中所有已启用的发布。不同用户和不同 App 得到相同目录。每条只返回 Agent 编码、发布名和用途说明 |
| MCP 任务工具 | 连接 Header 提供 App。工具参数只含 Agent 编码、Task ID、消息等业务数据 | 校验目标 App、发布范围和当前任务归属 |
| CLI / SDK 任务请求 | 默认取客户端配置的 App，CLI `--app` 或 SDK 单次请求选项可覆盖。最终统一发送 `app` Header | 同上。覆盖 App 时使用的凭据也必须有该 App 的访问权限 |
| A2A | 通用入口读取 `app` Header，带 App 入口以路径为准 | 复用同一平台鉴权、发布范围及任务归属检查 |

请求凭据沿用 A2A 已实现的方式。平台 API Key 用 `X-Deepfos-Key`，平台 Token 用 `Authorization: Bearer <Token>`，客户端二选一。应用 ID 填在 `app` Header。服务端根据 App 确定 Space，可选的 `space` Header 需要与 App 所属空间一致。MCP 可使用和 DeepFOS MCP 一样的 API Key 配置。目录校验复用现有平台 Key 解析和账号身份查询，Token 查目录时可以省略 App。其余任务操作每次都解析并验证 App。

```json
{
  "app": "finops001",
  "X-Deepfos-Key": "<该应用的 DeepFOS API Key>"
}
```

身份由平台从凭据解析。服务端只把经校验的 App、Space、身份和下游需要的凭据放入受控执行上下文。目录中的 Agent 仍可能超出当前 App 的发布范围。新建任务时校验发布启用、App 权限及 `scope.apps`。当前发布已停用时，已有任务仍按现行 A2A 规则处理，App 移出 Scope 或访问权限被撤销后失去访问权。

Task ID 操作按现有内存记录定位发布和任务，核对调用主体、App、Space，再读取同一发布的当前范围。缺凭据或无效凭据返回 401，App 未获授权或超出发布范围返回 403。未知、过期和其他主体的任务统一返回 404。应用缺失或格式错误明确提示 Header。MCP 在连接鉴权或工具结果返回对应错误，CLI 和 SDK 保留统一的错误码和可读说明。

## 工具对照

HTTP 路径均相对于 `/api/v1/agent-service`。CLI 示例省略 `deepfos-agent`，SDK 示例省略 `client.`。

| **操作** | **现有 A2A** | **公共 HTTP** | **MCP 工具** | **CLI** | **Python SDK** |
| --- | --- | --- | --- | --- | --- |
| 列出发布 | 单 Agent Card 发现 | `GET /agents` | `list_agents()` | `agents list` | `list_agents()` |
| 发起任务 | `SendMessage` | `POST /agents/{agent_code}/tasks` | `submit_task(agent_code, text, …)` | `tasks submit <agent_code> --text …` | `submit(agent_code=…, text=…)` |
| 查任务、状态和结果 | `GetTask` | `GET /tasks/{task_id}` | `get_task(task_id)` | `tasks get <id>` | `get_task(task_id)` |
| 查某个 Agent 的近期任务 | `ListTasks` | `GET /agents/{agent_code}/tasks` | `list_tasks(agent_code, …)` | `tasks list --agent <agent_code>` | `list_tasks(agent_code=…)` |
| 原任务补材料 | `SendMessage` 带原 Task ID | `POST /tasks/{task_id}/messages` | `reply_task(task_id, text, …)` | `tasks reply <id> --text …` | `reply(task_id, text=…)` |
| 取消任务 | `CancelTask` | `POST /tasks/{task_id}/cancel` | `cancel_task(task_id)` | `tasks cancel <id>` | `cancel(task_id)` |
| 等待任务变化 | `SendStreamingMessage` / `SubscribeToTask` | 首版通过查询接口轮询 | `get_task(task_id)` | `tasks wait <id>` | `wait(task_id, timeout=…)` |
| 读取文件产物 | Task Artifact 引用及授权下载 | `GET /tasks/{task_id}/artifacts/{artifact_id}/download` | `get_task` 或 `get_artifact` 返回完整下载 URL，至多 20 MiB 可显式请求 Base64 | `artifacts download <id> <artifact> -o report.xlsx` | `download_artifact(task_id, artifact_id, destination=…)` |

- MCP 的 `tools/list` 负责工具定义，业务目录由 `list_agents` 返回。发起和补答快速返回统一任务快照。Task ID 用于继续查询，Context ID 用于在原会话发起下一项任务。提交或补答携带稳定 `message_id`，同一次请求重试沿用该值。补材料时使用原 Task ID 和返回的问题标识。
- MCP 工具返回结构化快照，包含状态、待补说明、文本／结构化结果及文件引用。CLI、SDK 等到终态或待输入／待鉴权就返回，等待超时保留 Task ID，任务仍可查询。已有 A2A SSE 照常使用。
- CLI `--attach` 和 SDK `attachments=[Path(…)]` 由客户端读取文件并作为内联附件提交。MCP 使用同一内联附件结构，服务端复用现有 MIME、数量和体积限制。MCP 客户端默认用配置的凭据和 App 对返回的完整地址发起流式下载。显式传 `format="base64"` 且文件至多 20 MiB 时，`get_artifact` 在文本 JSON 中返回 `data_base64` 供客户端解码。

例如用 `app: finops001` 调 `POST /agents/budget-variance-report/tasks`，HTTP 请求体与 MCP `submit_task` 使用相同的消息字段。MCP 把 `agent_code` 放在工具参数，HTTP 从路径取得。

```json
{"message_id":"msg-001","text":"生成预算差异报告"}
```

首次受理返回 Task ID 和 Context ID。后续 `get_task` 若返回 `input_required` 与 `question_id`，调用方用 `POST /tasks/t-001/messages` 或 `reply_task` 提交补充。每条新消息使用新的 `message_id`。

```json
{"message_id":"msg-002","reply_to":"q-001","text":"预算期间是 2026-10"}
```

补答后沿用 `t-001` 查结果。若任务已完成并希望用原会话做下一项工作，再发起一个任务，在提交时传上一次返回的 `context_id`。任务数据由执行身份和 App 约束，跨渠道续接仍需使用同一主体和 App。

## Console 和客户端安装

| **页签** | **展示内容** | **客户端操作** |
| --- | --- | --- |
| MCP | 环境级 URL、复制地址、Header 示例、连接说明 | 在现有 MCP 宿主配置 Streamable HTTP、`app` 和凭据，再执行 `list_agents` 验证 |
| CLI | 「复制安装命令」「复制 Agent 安装指令」和简短使用说明 | 人或脚本只装 CLI。走终端的 Agent 通过安装指令装 CLI 和配套 Skill |
| Python SDK | 「复制安装命令」、最低 Python 版本、快速开始 | 在业务项目虚拟环境中安装 Python 包并导入 SDK |
| A2A | 当前发布的两条 Card URL 各自的应用传法和复制入口 | 客户端选择通用 Card 或带 App 的 Card |

MCP、CLI、SDK 的内容随环境变化。A2A 两条地址随当前选中的发布变化。CLI 和 SDK 用同一个外部分发包 `deepfos-agent-client`，提供 `deepfos-agent` 命令和 `deepfos_agent_client` Python 模块。CLI 用 `uv tool install` 独立安装，SDK 用项目虚拟环境的 `python -m pip install`。Console 的安装命令指向部署方配置的带版本 wheel 地址。离线部署可提供同版本 wheel。 [uv 工具安装](https://docs.astral.sh/uv/concepts/tools/)

### CLI 给人使用

```bash
uv tool install 'deepfos-agent-client==<版本>' --default-index '<企业包源>'
deepfos-agent --version
deepfos-agent agents list
deepfos-agent tasks submit budget-variance-report --text "生成 10 月预算差异报告"
deepfos-agent tasks wait <task_id>
deepfos-agent artifacts download <task_id> <artifact_id> -o report.xlsx
```

命令从环境读取服务地址和 App，变量为 `DEEPFOS_AGENT_URL`、`DEEPFOS_AGENT_APP`。API Key 和 Token 分别由 `DEEPFOS_AGENT_API_KEY`、`DEEPFOS_AGENT_TOKEN` 注入。Console 复制项只含环境地址、配置变量名和安装说明。

### CLI 给 Agent 使用

「复制 Agent 安装指令」复制可交给 Agent 的操作说明，包含下列步骤。

1. 从指定包源安装给定版本的 `deepfos-agent-client`。
2. 将包内同版本的 `deepfos-agent-call/SKILL.md` 安装到当前 Agent 宿主支持的位置。`deepfos-agent skill install --dir <skills 根目录>` 将内置 Skill 安装到调用方指定的目录。
3. 使用宿主已注入的 App 和凭据，先执行 `deepfos-agent --version`，再执行 `deepfos-agent agents list` 验证连接。

Skill 负责指导 Agent 发现发布、通过 CLI 发任务、处理待补材料、查询和取件。凭据由宿主提供。命令、Skill 与调用契约随一个版本交付，便于同步更新。用 MCP 接入的 Agent 直接使用工具契约。

### Python SDK 给程序使用

```bash
python -m pip install 'deepfos-agent-client==<版本>' --index-url '<企业包源>'
```

```python
from deepfos_agent_client import AgentClient

async with AgentClient.from_env() as client:
    agents = await client.list_agents()
    task = await client.submit(
        agent_code="budget-variance-report",
        text="生成 10 月预算差异报告",
    )
    result = await client.wait(task.id, timeout=120)
```

同一个 SDK 客户端也可执行补答、取消或下载。程序依赖仅安装 Python 包，Skill 由使用 CLI 的 Agent 按需安装。

## 联调验收

- 用两个不同 App 的有效凭据和普通用户调用目录，对比结果一致，并验证停用发布从目录消失。用其中一个 App 调用范围外的发布，验证目录仍可见且执行请求被拒绝。
- 用真实 MCP 宿主、终端和独立 Python 项目各跑一次目录、提交、查任务、补材料和取件。跨入口操作同一 Task 时，同用户与同 App 可以访问，换 App、换身份和权限撤销按规则拒绝。用真实附件和 XLSX 校验输入、文件内容及下载权限。
- 用实际企业包源验收 CLI 安装、Agent 安装指令和 SDK 安装。随版本确认 Python 最低版本、受支持宿主的安装位置和 MCP 资源读取能力。
