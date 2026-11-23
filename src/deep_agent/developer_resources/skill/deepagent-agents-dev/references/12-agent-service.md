# 发布与远程 Agent

外部客户端调用本环境的 Agent 时，在「服务发布 → 发布 Agent」创建发布。让本地 Agent 委派到 A2A、Codex 或 CodeBuddy 时，使用「设置 → 远程 Agent」。调用另一个 DeepAgent 的 MCP 发布时，在「设置 → MCP」添加 Streamable HTTP 连接，并在本地 Agent 启用对应工具包。

## 发布与鉴权

Console 管理员选择已有 Agent 并保存发布编码、名称和应用范围。运行时沿用 Agent 的模型、Skill 与非 UI 工具配置。遇到必要信息缺失时，发布 Agent 用 `INPUT_REQUIRED` 返回澄清问题。调用凭据放在客户端私有配置中，使用 `Authorization: Bearer <Token>`、`token` 或 `X-Deepfos-Key` 中的一种。

| 入口 | 地址与请求头 |
| --- | --- |
| 通用 A2A | 从发布页复制完整 Agent Card URL，读取其中 `supportedInterfaces[].url` 作为 JSON-RPC 地址。携带 `app` 和一种凭据。 |
| 指定应用 A2A | 从发布页复制指定应用卡片地址，把 `{app_id}` 替换为实际应用 ID。携带凭据，服务端根据路径中的应用 ID 推导空间。 |
| MCP | 从发布页复制 MCP 地址和 Header 示例，使用 Streamable HTTP，填写 `app`、所属 `space` 和一种凭据。 |

A2A JSON-RPC 调用加 `A2A-Version: 1.0`，读取卡片时可以省略。回环地址仅供在同一台机器执行的客户端使用。地址通过网关时保留完整网关前缀。

## 任务、补答与续跑

| 操作 | A2A | MCP |
| --- | --- | --- |
| 首次提交 | `SendMessage` 或 `SendStreamingMessage`，保存返回的 Task 与 Context ID | 先 `list_agents`，再用 `send_message`，保存返回的 `id` 和 `contextId` |
| 查询 | `GetTask` 读状态、历史和产物，`SubscribeToTask` 观察活动任务 | `get_task` 读状态、输出、待答问题及产物，`list_tasks` 查近期任务 |
| 回复待答问题 | 在新消息里带原 Task ID | 同样用 `send_message`，在消息里带原 `taskId` 和新 `messageId` |
| 完成后追问 | 带原 Context ID 发送新消息，获得新的 Task ID | 用原 `contextId` 和新 `messageId` 再调用 `send_message` |
| 停止 | `CancelTask` | `cancel_task`，根据返回状态确认是否停止 |

MCP 的 `send_message` 和 `get_task` 默认最多等待 120 秒。调用方分别用 `configuration.waitSeconds` 和顶层 `waitSeconds` 指定等待时长，传 `0` 立即取得当前状态。超时仍返回原 `id` 和 `contextId`，任务继续执行，可立即再用原 `id` 调用 `get_task`。

同一次提交重试时保留消息 ID 和原内容。A2A 和 MCP 输入附件都用 Message Part 的 `raw`、`mediaType` 和 `filename`。每条消息内联文件的解码总量最多 8 MiB，并受目标环境的上传限制。MCP 完整调用示例可通过 `resources/read` 读取 `deepagent://guides/mcp`。当前发布入口为 A2A 和 MCP。

## 获取产物

- **A2A** 从任务的 Artifact 读取文件 URL，沿用该任务的调用身份下载。通用链接携带原 `app` Header，指定应用链接使用 URL 中的应用。
- **MCP** 从 `send_message` 或 `get_task` 的 `artifacts[].parts[].url` 取得下载地址。对原链接发 HTTP GET，携带原 `app`、`space`（如有）和同一凭据。请求地址无法解析时，任务省略 HTTP 链接。
- **报告交付**可委托发布 Agent 将文件登记到 DeepFOS 下载中心，确认成功后告知用户文件名和领取位置。完整操作见 MCP 资源 `deepagent://guides/mcp` 的「结果与文件」。

只在可信发布服务返回的预期地址上发送凭据。取件 URL 随任务记录过期而失效。交付时确认文件实际下载并可读，批量验收可另外核对 SHA-256。

## 在会话中委派与取件

管理员保存并检查 A2A、Codex 或 CodeBuddy 连接，在目标 Agent 的资源配置中显式启用。先在远程 Agent 页直连调试，再通过已绑定的 Agent 验证委派、补答、续跑和文件交付。MCP 连接走普通工具轨迹。

`delegate_task` 和 `answer_task` 用 `attachments: [{"id": "<File ID>"}]` 选择本次发送的文件。省略时本次不新增文件。待补答时在原会话回复，如有多个待答任务，用工具卡选定任务。后台完成后使用 `remote_task_result` 查询原任务，产物回收到调用方 FileStore 后可在原会话下载。Codex 默认只读，生成文件需可写工作区。

## 排查接入与取件

在 Console「诊断 → 日志」选定事发时间和实例，再按下表查询。

| 现象 | 查询依据 |
| --- | --- |
| 读取卡片或建立 MCP 连接返回 401、403 | 用请求 ID 查 `published_agent_access_failed`，核对凭据类型、App 与发布范围 |
| 已建立任务但执行或取件失败 | 用发布页 Task ID 查 `published_agent_task`。取件看 `phase=artifact_read` 和 `reason`，运行问题可用同条记录的 Session ID 继续查 |
| 远程 Agent 连接检查失败 | 用连接 ID 查 `remote_agent_operation` 的 `phase=inspect` |
| 远程委派失败或缺少文件 | 用委派返回的 `task_id` 查 `remote_agent_operation`，核对发起、补答、续跑、取消或产物上传阶段与错误类型 |

近期任务与直连调试状态保存在进程中，重启后查已持久化的会话与诊断日志。日志包含阶段、状态和关联 ID，凭据、提示词及附件正文从原授权入口核查。
