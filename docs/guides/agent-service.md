# Agent 集成服务

Console 的「集成」可以为现有 Agent 新建发布。每份发布有独立的 Agent 编码、用途和应用范围。每份发布提供通用和指定 App 两种 Agent Card 地址。选中发布可复制这两条地址，并按实际调用应用筛选该发布的近期任务。

发布管理 API 使用 `agent_code` 表示对外编码，`agent_id` 指向实际执行的内部 Agent。Agent 编码创建后固定。

## 部署与本地启动

- `DEEP_AGENT_AGENT_SERVICE_ADMIN_USERS` 填逗号分隔的平台用户 ID。发布管理同时要求该名单和 Console 管理权限。名单为空时关闭发布管理。
- `DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL` 填调用方可达的 HTTPS 基址。开发机可使用 `http://127.0.0.1:8002`。部署在网关子路径下时，填带该路径的基址。
- Task 和 Context 由当前 API 进程管理。首期使用单 API 进程、单实例。重启会清空近期任务和旧 Task、Context，现有会话及其文件沿用原保留策略。
- 从仓库根目录使用独立的本地 SQLite、FileStore、渠道令牌和 Profile。该 Profile 的渠道端口用 8012，并停用复制过来的渠道账号，以免重复收消息。

```sh
PYTHONPATH=src .venv/bin/python -m deep_agent.combined_runtime \
  --local-config "$HOME/.config/deep-agent/TMP_to_delete_a2a_8002.env" \
  --api-port 8002
```

## 接入与取件

从「集成」复制完整 Card URL，在 A2A 1.0 客户端登记。两张 Card 都描述同一个 Agent，`supportedInterfaces[].url` 各自指向对应的 JSON-RPC 基址。

| 入口 | Card 路径 | 应用上下文 |
|---|---|---|
| 通用 | `/api/v1/agent-service/a2a/{agent_code}/agent-card.json` | 用 `app` Header 指定应用。服务端确定 Space，显式传入的 `space` 必须匹配。 |
| 指定 App | `/api/v1/agent-service/a2a/{agent_code}/apps/{app_id}/agent-card.json` | 将占位符替换为实际 App ID。服务端采用路径中的 App，并忽略 `app`、`space` Header。 |

两个 RPC 基址均支持相对发现路径 `/.well-known/agent-card.json`。Card、任务操作和取件都需要一个有效凭据。平台 Token 可用 `token` 或 `Authorization: Bearer <Token>`，DeepFOS API Key 用 `X-Deepfos-Key`。只传一个鉴权 Header。服务端验证调用身份、App 权限和发布范围，缺少凭据返回 `401`，超出应用权限或范围返回 `403`。

读取 Agent Card 时可省略协议版本。调用 JSON-RPC 任务接口时传 `A2A-Version: 1.0`。缺省按 A2A 0.3 处理，当前任务接口会返回版本不支持。

发布的「应用范围」可以选择全部有权访问的应用，或指定一项及多项应用。两种入口在同一发布、调用主体和 App 下共用 Task 与 Context。

可传 `language`，默认 `zh-cn`。

| 操作 | 行为 |
|---|---|
| `SendMessage` | 默认等待结果。设 `returnImmediately` 可先取得 Task ID。向待补充任务发送消息时，沿用 Task ID 和 Context ID，并为新消息生成不同的 Message ID。 |
| `SendStreamingMessage` | 先发任务快照，再发状态及 Artifact 事件。断开连接后可重新订阅。 |
| `GetTask` | 读取状态、历史和 Artifact。 |
| `ListTasks` | 按同一发布、身份、App 和 Space 查询，可分页和筛选。 |
| `CancelTask` | 取消当前任务。 |
| `SubscribeToTask` | 观察已有的活动任务。任务结束后用 `GetTask` 读取结果。 |

文本和结构化数据在 Artifact 中返回。Agent 已发布的文件会返回 Artifact URL，地址沿用本次请求选择应用的方式。下载时使用原任务的调用身份，通用 URL 需要 `app` Header，带应用的 URL 使用路径。DeepAgent 会重新校验权限。临时任务记录到期后，A2A 下载地址随记录失效。

输入附件用 `message.parts[]` 的 `raw`（Base64）、`mediaType` 和 `filename`。Card 的 `defaultInputModes` 包含当前接收的附件类型。单次内联附件合计上限为 8 MiB，仍受平台配置的单文件大小和数量限制。补交附件时，带上待补材料的 `taskId`、`contextId`，可通过 `metadata.replyTo` 指定待回答的问题。任务完成后的普通追问只带 `contextId`，会创建同一会话下的新 Task。

## Task、Context 和会话

一个 A2A `contextId` 对应一个 DeepAgent `session_id`，两个 ID 分别生成。会话保存消息、工具调用和附件引用。`taskId` 对应「集成 → 近期任务」里的工作项，一条会话可以包含多个 Task。每次实际执行有独立的 Run，等待补材料后会在同一 Task、同一会话里启动下一次 Run。

首次请求不带这两个 ID，服务端创建 Context 和 Task。已有任务完成后，客户端带上 `contextId` 继续追问，服务端在同一会话创建新 Task。任务仍待补材料时，客户端带上原来的两个 ID 继续该 Task。同一 Context 同时只处理一个活动 Task。

## MCP 接入

在 Console「集成 → MCP」复制当前环境的 Streamable HTTP 地址和请求 Header 示例。将 `app` 填为应用 ID。平台 Token 可用 `token` 或 `Authorization: Bearer <Token>`，DeepFOS API Key 用 `X-Deepfos-Key`。只传一个鉴权 Header。服务端解析所属空间。把「复制 Agent 接入指引」的内容发给本地 Agent，由它配置宿主连接，凭据通过宿主设置提供。回环地址仅供同机客户端使用。

调用 `list_agents` 验证连接并取得 Agent 编码。目录包含所有启用的发布，`submit_task` 会单独检查应用权限与发布范围。`get_task` 查询结果，`list_tasks` 查看近期任务，`reply_task` 用原 Task ID 和问题 ID 补材料，`cancel_task` 取消任务。`get_artifact` 返回资源引用及鉴权下载路径。MCP 资源读取限 1 MiB，完整文件通过下载路径获取，仍需传凭据和 App。

同一凭据主体和应用可以在 A2A 与 MCP 间继续同一个任务。任务 ID 保持不变。

支持浏览 MCP 资源的客户端可以用 `resources/list` 发现两份调用指南，再用 `resources/read` 读取示例：

- `deepagent://guides/agent-tasks` 覆盖发现 Agent、提交、查看状态、补材料和在同一会话下追问。
- `deepagent://guides/attachments-artifacts` 覆盖发送文件、读取任务结果和下载文件。

任务产物通过 `get_artifact` 按任务授权取得资源地址。

CLI、Python SDK 和通用 HTTP 调用是待评审草案，本版仅在 Console 保留入口占位。草案可能改写或移除，当前服务仅注册 A2A、MCP 及必要的鉴权取件接口。

## 联调

- **本地官方 Inspector** 使用支持 A2A 1.0 的 Inspector，执行 `a2a-inspector --no-open --port 8085`，在本机浏览器打开提示的地址。添加完整 Card URL，在 Connection 设置 Bearer Token 或 API Key。通用地址另填 `app` Header，然后点 Fetch again。分别发送普通和流式消息，再查看协议事件。
- **A²D Playground** 选择 A2A、填写发布的 RPC 基址，工具会从相对的 well-known 路径读取 Card。打开 Authorization & Headers，填写样例凭据。通用地址另填 `app` Header。连接成功后在对话区使用 Stream response (SSE) 切换普通和流式发送。在线工具联调使用隔离样例和它可访问的 HTTPS 地址。
- **Langflow** 原生出站 A2A 节点把消息作为文本发送。本地验收流使用自定义节点，接收 Playground 的完整 Chat Message，将已上传文件编码成 A2A 内联附件，并在该 Playground 聊天中保留 Task 与 Context ID。收到待补材料问题后，在同一聊天补交。服务或适配器重启导致旧关联失效时，节点会在聊天内提示点击 New Chat 后重发，原聊天仍可查看。
- **CodeBuddy Code** 使用 CLI 的 `/login` 登录。实测 2.183.0 的发现入口接受 Card 数组或 `{"cards":[…]}`。把对应目录 URL 配给 `CODEBUDDY_REMOTE_AGENTS_URL`，并设置 `CODEBUDDY_A2A_OUTBOUND_ENABLED=1` 后启动 `codebuddy`。本地验收的回环适配器提供测试目录，通过带 App 的地址转发，并补齐测试凭据。可直接配置所需 Header 的客户端可以直接调用 DeepAgent。CodeBuddy 使用 URL 附件的验收细节见[验收记录](../design/45-a2a-access/server-validation.md)。

仓库中的协议、任务、文件和权限回归可直接运行。

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.integration.test_agent_service
```

参考 [A2A 1.0 规范](https://a2a-protocol.org/v1.0.1/specification/)、[Inspector](https://github.com/a2aproject/a2a-inspector)、[A²D Playground](https://docs.a2d-ai.com/playground/protocol-tester/) 和 [CodeBuddy 出站 A2A](https://www.codebuddy.ai/docs/cli/env-vars)。

## 依赖变更（运维）

相对一期已含官方 `a2a-sdk[http-server]==1.3.0` 的基线，二期服务端依赖变更为无。上线前确认基础镜像已包含一期 A2A SDK 及其传递依赖，以及当前运行依赖提供的 MCP SDK。二期本机验证使用 Python 3.12、MCP SDK 1.30.0、AnyIO 4.16.0。CLI 和 SDK 草案的依赖不进入本版服务端镜像。

一期由 `015` 升到 `016`，新增发布配置表和索引。二期沿用 `016`。若环境尚在 `015`，生产迁移前核对 DDL 权限、数据库方言、备份、锁和回退窗口。有发布记录时迁移会拒绝降级。
