# 发布选路、A2A 操作与联调

[实施总案](server-implementation-plan.md) · [服务端原型](<Prototype A2A服务端接入/index.html>)

## 三个助手怎么选

管理员为每个对外业务建立发布配置。每份发布有自己的编码、Agent Card URL、JSON-RPC URL 和关联 Agent。调用方手工选择、在程序配置中指定，或由它的上层 Agent 根据 Card 的能力描述选中目标。

| **任务** | **调用方预先登记的 Agent 编码** | **服务端根据地址绑定的 Agent** |
| --- | --- | --- |
| 比较 10 月预算与实际 | `budget-variance-report` | 预算分析助手 |
| 核对两份台账 | `reconciliation-review` | 智能对账助手 |
| 分析当前表格数据 | `spreadsheet-analysis` | 电子表格助手 |

通用入口通过 `app` Header 传 App。指定 App 的入口在同一 RPC 基址后加 `/apps/{app_id}`，Card 和取件地址沿用这一段。两种地址按同一权限规则读取和执行。

以对账为例，调用方从 `GET /api/v1/agent-service/a2a/reconciliation-review/agent-card.json` 读取 Card，随后向 Card 的 `supportedInterfaces[].url` 发 `SendMessage`。服务端由 URL 中的 Agent 编码查生效配置，核对调用主体，确定本任务的 Agent、执行身份及 App/Space。任务的 ID 也绑定发布和调用主体。编辑关联后，旧任务继续使用受理时的执行范围。客户端若带旧 Context 提交新任务，服务端明确要求去掉旧 Context 后重新提交。

A2A 标准的根发现路径是 `/.well-known/agent-card.json`。这里的发布子路径作为直接配置的完整 Card URL 提供，客户端若只接受域名根发现，可用独立域名或网关为它映射根路径。Card 的 `skills[].id` 用于描述能力，规范的 `SendMessageRequest` 没有统一的 `agentId` 或 `skillId` 选路字段。接口声明的可选 `tenant` 是路由值，身份和授权仍由服务端校验。[A2A 发现](https://a2a-protocol.org/latest/specification/#82-discovery-mechanisms) · [请求结构](https://a2a-protocol.org/latest/specification/#321-sendmessage)

子代理可用类似 [OpenAI Agents SDK 的 agents as tools](https://openai.github.io/openai-agents-python/tools/#agents-as-tools) 的组织方式。调用方为预算、对账和表格分别登记具名工具，工具描述写清用途，工具内部绑定各自的 Card URL。上层模型负责选工具，服务端只执行该发布已经绑定并授权的 Agent。如果未来要提供一个「财务总助手」统一分派，可把它独立发布成第四个 Agent。

## 客户端怎么管理远端 Agent

A2A 统一了 [Agent Card](https://a2a-protocol.org/latest/specification/agent-card/) 和调用协议。客户端可以直接登记 Card URL，或从企业目录导入。Card 返回远端 Agent 的名称、用途、能力说明 `skills`、接口和鉴权要求；本地的连接别名、凭据引用、授权给哪些 Agent、何时委托，由客户端自己保存。Card 的 `skills` 是能力描述，客户端不用安装 DeepAgent 的 Skill 包。

| 调用方式 | 怎么组织 |
| --- | --- |
| 原生 A2A | [Google ADK](https://adk.dev/a2a/quickstart-consuming/#how-it-works) 将名称、用途和 Card 配成一个 `RemoteA2aAgent`，再挂到主 Agent 的 `sub_agents`。这是 ADK 自己的组织方式；调用走 A2A。 |
| 只有 MCP 工具能力 | 可登记 MCP 连接，绑定远端发布的适配工具；适配层把提交、查询等动作转交给任务服务。 |
| 能执行命令或脚本 | [官方 A2A CLI](https://github.com/a2aproject/a2a-cli/blob/main/specification/SPEC.md) 用 `--agent-card` 指定目标，脚本可直接调用。Skill 可以记录何时用哪个远端 Agent、怎么等结果和取件。 |

A2A 定义网络上的发现、消息、任务状态和产物。原生调用可由 SDK 或一段 HTTP 客户端代码发起。用 JSON-RPC 接入时，程序先 GET Card，再向 `supportedInterfaces[].url` POST 一条带 `method: "SendMessage"` 的 JSON-RPC 请求。服务端返回 Task 后，程序用同一接口的 `GetTask` 查询结果。模型可通过工具触发这段程序，普通业务脚本也可以直接调用。 [A2A 规范](https://a2a-protocol.org/latest/specification/#5-protocol-bindings)

Card 的 `securitySchemes` 和 `securityRequirements` 声明凭据类型和用法。Bearer Token 由服务方通过接入流程发给调用方，调用程序从本地凭据配置读取，再送入 `Authorization: Bearer <token>`。API Key 由服务方发放，程序按 Card 声明的位置和名称发送。OAuth 由管理员配置已注册的客户端或用户登录流程，程序通过相应授权流程换取 Access Token。若 Card 本身需鉴权，连接初始化就需要配置能读取该 Card 的凭据。服务端每次根据凭据识别调用者，再校验该发布的权限。 [A2A 鉴权规范](https://a2a-protocol.org/latest/specification/#73-client-authentication-process)

DeepAgent 作为客户端时，「外部 Agent」是一条可管理的连接记录。以预算助手为例，配置本地名称、Card URL、选择的鉴权方案、凭据引用和委托说明。管理员为本地「经营分析助手」关联该连接，运行时便可以提供一个具名的内置工具「生成预算差异报告」。模型选工具并传业务输入，DeepAgent 后端按绑定读取 Card、解析凭据、发出 A2A 请求，再把远端 Task 状态和交付结果返回给本地 Agent。工具参数由业务定义，远端地址和凭据由连接配置提供。接入方若使用 MCP 或 CLI，仍可由相应的适配程序处理同样的连接和任务。此处仅记录客户端设计方向。

## 批量发现 Agent

A2A 1.0 规定单张 Agent Card 的格式，承认目录是发现方式之一。目录的地址、认证、筛选和返回格式由目录服务与客户端约定。域名根路径的 `/.well-known/agent-card.json` 返回一张 Card，`ListTasks` 查询的是任务。标准操作没有 `ListAgents`。[官方发现指南](https://a2a-protocol.org/v1.0.1/topics/agent-discovery/#2-curated-registries-catalog-based-discovery)

首版在 Console 复制所选发布的完整 Card URL，交给接入方配置。需要通过一个链接动态查多个发布时，可以增设独立目录入口。接入方配置一次目录地址和凭据，目录根据当前身份返回可发现的已发布条目及各自的 Card URL。客户端选择其中一张 Card 后按 A2A 规范调用，运行时重新校验调用权限。目录只需暴露名称、用途与 Card URL 等可公开字段，后台管理接口仍用于管理员编辑发布。

## A2A 1.0 操作表

首版发现采用 HTTP GET，11 个标准操作在 JSON-RPC 下共用 JSON-RPC POST，通用地址为 `/api/v1/agent-service/a2a/{agent_code}`，带 App 地址在其后追加 `/apps/{app_id}`。两种地址都由报文中的 `method` 区分操作。gRPC 使用表中相同方法名，HTTP+JSON 使用右列路径。两种绑定暂缓。0.3 仅在有明确客户端需求时单独适配，首版客户端显式发 `A2A-Version: 1.0`。规范将缺失版本解释为 0.3，纯 1.0 入口按不支持版本处理。[规范的绑定映射](https://a2a-protocol.org/latest/specification/#53-method-mapping-reference) · [版本处理](https://a2a-protocol.org/latest/specification/#362-server-responsibilities)

| **发现或 JSON-RPC 方法** | **HTTP+JSON 对照** | **首版** | **与 DeepAgent 现有接口的关系** |
| --- | --- | --- | --- |
| Agent Card | `GET /api/v1/agent-service/a2a/{agent_code}/agent-card.json` | 支持发现 | 由发布配置生成，通过完整 Card URL 接入。 |
| `SendMessage` | `POST /message:send` | 支持 | 复用 `POST /api/v1/chat` 背后的运行组装、Runner 和会话事件。可等待或立即返回 Task ID。同 Task 补材料仍用本方法。 |
| `SendStreamingMessage` | `POST /message:stream` | 支持 | 复用 `POST /api/v1/chat/stream` 背后的执行与事件，将其投影为 A2A 流。观察连接断开后，运行继续。 |
| `GetTask` | `GET /tasks/{id}` | 支持 | 查内存 Task 的状态、公开结果和产物。类似 `/sessions/{session_id}/run-status`，但有独立外部 ID 和近期终态。 |
| `ListTasks` | `GET /tasks` | 支持 | 在当前发布和调用者权限内分页列内存任务。`GET /sessions` 继续列内部会话。 |
| `CancelTask` | `POST /tasks/{id}:cancel` | 支持 | 复用 `/sessions/{session_id}/cancel` 背后的运行取消令牌，按 Task 定位 Run，并在实际停止后确认取消。 |
| `SubscribeToTask` | `POST /tasks/{id}:subscribe` | 支持 | 类似 `/sessions/{session_id}/live` 的只读观察。A2A 使用自己的 Task 快照及状态、产物流。已终态用 `GetTask` 查询。 |
| `CreateTaskPushNotificationConfig` | `POST /tasks/{id}/pushNotificationConfigs` | 暂缓 | 新增任务状态回调配置，现有会话无对应接口。 |
| `GetTaskPushNotificationConfig` | `GET /tasks/{id}/pushNotificationConfigs/{configId}` | 暂缓 | 查一条回调配置，现有会话无对应接口。 |
| `ListTaskPushNotificationConfigs` | `GET /tasks/{id}/pushNotificationConfigs` | 暂缓 | 列出任务回调配置，现有会话无对应接口。 |
| `DeleteTaskPushNotificationConfig` | `DELETE /tasks/{id}/pushNotificationConfigs/{configId}` | 暂缓 | 删除任务回调配置，现有会话无对应接口。 |
| `GetExtendedAgentCard` | `GET /extendedAgentCard` | 暂缓 | 提供认证后的扩展 Card，现有会话无对应接口。 |
| Artifact 取件 | DeepAgent 扩展 `GET /api/v1/agent-service/a2a/{agent_code}/tasks/{taskId}/artifacts/{artifactId}/download` | 支持 | Task 或 SSE 按当前接入地址返回取件 URL。二进制文件走现有 FileStore，每次取件重新校验归属。A2A 1.0 无 `DownloadArtifact` RPC。 |

以上是能力和执行层映射。A2A 入口在进程内调用共用服务，避免发 HTTP 请求回调自己。内部历史和诊断继续使用已有会话与事件查询，外部只收到该发布允许披露的内容。[Chat 源码](../../../src/deep_agent/api/v1/chat.py) · [Session 源码](../../../src/deep_agent/api/v1/sessions.py)

## 联调入口

| **工具** | **怎么用** | **能验证什么** |
| --- | --- | --- |
| [在线 A2D Playground](https://a2playground.a2d-ai.com/) | 选 A2A，贴可公网访问的测试 Card URL，发送测试消息。支持访客入口。 | 查看 Card、发送、流式和多轮。适合公开的测试地址与脱敏样例。[使用说明](https://docs.a2d-ai.com/playground/protocol-tester/) |
| [官方 Inspector](https://github.com/a2aproject/a2a-inspector) | 安装后运行 `a2a-inspector` 打开本地网页，输入完整 Card URL，在本地填测试鉴权。 | 本机和内网的主要验收工具。检查 Card、发消息、补材料、查／取消／订阅任务和原始流量。 |
| [官方 A2A CLI](https://github.com/a2aproject/a2a-cli#usage) | 在终端发现、发送和查任务，`-a` 指向测试 Agent。 | 在无浏览器登录态的情况下复验同一条链路，也可用于回归脚本。 |
| [CodeBuddy CLI](https://www.workbuddy.cn/docs/cli/release-notes/v2.143.0) | 最后用实际接入方的版本连接测试 Card。 | 验证客户端能选择指定助手并交付结果。WorkBuddy 桌面版按实测另记。 |

已有官方 CLI 时，可先用两个终端运行 `a2a server --echo --port 8080` 和 `a2a send -a http://127.0.0.1:8080 "hello"`。随后将 `http://127.0.0.1:8080` 换为实际测试发布的 Card URL，对三个发布各发送一次，核对服务端绑定。对预算发布再做一次异步提交、查 Task、同 Task 补材料、取 XLSX 和重启验证。公网工具先用公开测试地址验证，内网凭据留在本地官方工具。[官方 CLI 实验教程](https://a2a-protocol.org/latest/blog/2026/10/01/introducing-a2a-cli/)

原型里的域名、发布状态与任务是示例。DeepAgent 的服务实现和真实互通在开发阶段验收。
