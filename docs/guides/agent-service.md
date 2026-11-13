# A2A 服务端

Console 的「集成」可以为现有 Agent 新建发布。每份发布有独立的编码、用途和应用范围。一张 Agent Card 对应一份发布。选中发布可复制完整 Card 地址，并按实际调用应用筛选该发布的近期任务。

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

复制 `/api/v1/agent-service/a2a/{code}/agent-card.json` 的完整地址，在 A2A 1.0 客户端里登记。Card 中的 `supportedInterfaces` 给出 JSON-RPC 地址。也可使用发布 RPC 地址和相对的 `/.well-known/agent-card.json` 发现卡片。

Card 和业务请求都需要一项有效的平台凭据。平台 Token 用 `Authorization: Bearer <Token>`，DeepFOS API Key 用 `X-Deepfos-Key`。从客户端连接配置提供凭据，服务端以平台验证出的主体检查应用权限。

- **固定应用**在发布中选择一个 App。客户端无需发送 `app` 和 `space`，服务端始终使用发布配置里的 App 和 Space。
- **调用方指定**默认允许全部有权访问的应用，也可以改为指定应用并添加一项或多项。客户端每次通过 `app` Header 指定应用。服务端校验调用方权限及发布范围，并确定 Space。如果同时传入 `space`，也会校验与 App 的对应关系。

可传 `language`。

| 操作 | 行为 |
|---|---|
| `SendMessage` | 默认等待结果。设 `returnImmediately` 可先取得 Task ID。向待补充任务发送消息时，沿用 Task ID 和 Context ID，并为新消息生成不同的 Message ID。 |
| `SendStreamingMessage` | 先发任务快照，再发状态及 Artifact 事件。断开连接后可重新订阅。 |
| `GetTask` | 读取状态、历史和 Artifact。 |
| `ListTasks` | 按同一发布、身份、App 和 Space 查询，可分页和筛选。 |
| `CancelTask` | 取消当前任务。 |
| `SubscribeToTask` | 观察已有的活动任务。任务结束后用 `GetTask` 读取结果。 |

文本和结构化数据在 Artifact 中返回。Agent 已发布的文件会返回 Artifact URL。下载时使用原任务的调用身份，应用 Header 遵循该发布的应用来源配置。DeepAgent 会重新校验权限。临时任务记录到期后，A2A 下载地址随记录失效。

输入附件用 `message.parts[]` 的 `raw`（Base64）、`mediaType` 和 `filename`。Card 的 `defaultInputModes` 包含当前接收的附件类型。单次内联附件合计上限为 8 MiB，仍受平台配置的单文件大小和数量限制。补交附件时，带上待补材料的 `taskId`、`contextId`，可通过 `metadata.replyTo` 指定待回答的问题。任务完成后的普通追问只带 `contextId`，会创建同一会话下的新 Task。

## Task、Context 和会话

一个 A2A `contextId` 对应一个 DeepAgent `session_id`，两个 ID 分别生成。会话保存消息、工具调用和附件引用。`taskId` 对应「集成 → 近期任务」里的工作项，一条会话可以包含多个 Task。每次实际执行有独立的 Run，等待补材料后会在同一 Task、同一会话里启动下一次 Run。

首次请求不带这两个 ID，服务端创建 Context 和 Task。已有任务完成后，客户端带上 `contextId` 继续追问，服务端在同一会话创建新 Task。任务仍待补材料时，客户端带上原来的两个 ID 继续该 Task。同一 Context 同时只处理一个活动 Task。

## 联调

- **本地官方 Inspector** 使用支持 A2A 1.0 的 Inspector，执行 `a2a-inspector --no-open --port 8085`，在本机浏览器打开提示的地址。添加完整 Card URL，在 Connection 设置 Bearer Token 或 API Key 以及 App/Space，然后点 Fetch again。分别发送普通和流式消息，再查看协议事件。
- **A²D Playground** 选择 A2A、填写发布的 RPC 基址，工具会从相对的 well-known 路径读取 Card。打开 Authorization & Headers，填写样例凭据和 App/Space。连接成功后在对话区使用 Stream response (SSE) 切换普通和流式发送。在线工具联调使用隔离样例和它可访问的 HTTPS 地址。
- **Langflow** 原生出站 A2A 节点把消息作为文本发送。本地验收流使用自定义节点，接收 Playground 的完整 Chat Message，将已上传文件编码成 A2A 内联附件，并在该 Playground 聊天中保留 Task 与 Context ID。收到待补材料问题后，在同一聊天补交。服务或适配器重启导致旧关联失效时，节点会在聊天内提示点击 New Chat 后重发，原聊天仍可查看。
- **CodeBuddy Code** 使用 CLI 的 `/login` 登录。实测 2.183.0 的发现入口接受 Card 数组或 `{"cards":[…]}`。把对应目录 URL 配给 `CODEBUDDY_REMOTE_AGENTS_URL`，并设置 `CODEBUDDY_A2A_OUTBOUND_ENABLED=1` 后启动 `codebuddy`。本地验收的回环适配器提供测试目录，并为客户端缺失的请求 Header 补齐测试凭据与 App/Space。可直接配置所需 Header 的客户端可以直接调用 DeepAgent。CodeBuddy 使用 URL 附件的验收细节见[验收记录](../design/45-a2a-access/server-validation.md)。

仓库中的协议、任务、文件和权限回归可直接运行。

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.integration.test_agent_service
```

参考 [A2A 1.0 规范](https://a2a-protocol.org/v1.0.1/specification/)、[Inspector](https://github.com/a2aproject/a2a-inspector)、[A²D Playground](https://docs.a2d-ai.com/playground/protocol-tester/) 和 [CodeBuddy 出站 A2A](https://www.codebuddy.ai/docs/cli/env-vars)。

## 依赖变更（运维）

运行镜像新增 `a2a-sdk[http-server]==1.3.0`。本机解析并验证了 `culsans 0.11.0`、`google-api-core 2.44.0`、`googleapis-common-protos 1.76.0`、`protobuf 7.36.2` 和 `json-rpc 1.15.0` 等传递依赖。本机运行与回归使用 Python 3.12。现有 FastAPI、Pydantic AI、Harness 及沙箱依赖声明没有调整。请从实际基础镜像的清单核对差异，重新构建并验证镜像后与代码配套发版。

数据库由 `015` 升到 `016`，只新增发布配置表和索引。生产迁移前核对 DDL 权限、数据库方言、备份、锁和回退窗口。有发布记录时迁移会拒绝降级。
