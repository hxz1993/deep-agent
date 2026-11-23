# DeepAgent 远程 Agent 实施方案

45 号课题 · 连接配置与关联设计

[A2A 协议与版本边界](service-and-client-design.md) · [Agent 接入指南](../../guides/agent-service.md)

## 1. Console 与调用入口

一级导航用「远程 Agent」，放在 MCP 附近。页面上方是已接入的 Agent，支持卡片和列表。下方的近期调用记录本地 Agent 发起的任务，「远程 Agent」列分两行显示名称和接入方式。支持按任务名称或 ID、远程 Agent 和状态筛选，点击任务在右侧查看详情。

远程 Agent 配置 A2A、Codex 和 CodeBuddy。DeepAgent 通过 A2A 和标准 MCP 分别调用自身发布服务。MCP 在普通 MCP 目录登记，在本地 Agent 的 MCP 资源中启用。调用时用 `list_agents` 获取发布编码，再用 `submit_task` 等工具执行，调用记录进入普通工具轨迹。

进入某条连接后，左侧配置连接，中间的直连调试区显示本次对话与结果，右侧显示连接概要。检查操作和保存配置放在页头。同一连接的本地 Agent 调用在首页近期列表及原会话中查看，列表详情展示远端任务或会话标识、结果和进度。

管理员登记连接并填写对方要求的 Header。本地 Agent 编辑页沿用能力选择入口，显式选择可用连接。运行时按当前租户、已配置的 Agent 关联和当前调用者权限过滤，直连调试要求配置管理权限。配置后，调试和已授权的本地 Agent 调用共用出站客户端。对方没有 App 概念也可接入。对方要求的租户、项目等静态值走自定义 Header。确实有业务报文契约时，由该连接的适配层显式映射到规范消息字段或双方约定的 `metadata`，普通连接页只配置传输信息。 [A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)

连接归属待确认的是配置由实例管理员共用，还是由企业或 App 分开管理。八字段表按实例管理设计。管理员保存凭据、绑定连接和执行直连调试。会话用户在当前 App／Space 有权使用本地 Agent 时，只能通过该 Agent 调用它绑定的连接；补答、继续和取件仍分别校验会话和文件归属。远端凭据、SSH 登录身份与本地调用人独立，适配器只在调用授权通过后取用执行凭据。自调用验收用本地调用方 Agent 和独立发布的接收方 Agent，沿用服务端现有的发布权限检查。

## 2. Card、地址与鉴权

| 配置 | 建议实现 |
| --- | --- |
| Agent Card | 填完整 URL。读取后展示远端名称、`supportedInterfaces`、认证声明和能力。首版选择受支持的 A2A 1.0 JSON-RPC 接口，找不到匹配接口时展示诊断。连接校验只读取 Card、验证接口和配置，调试发送才创建任务。 |
| 鉴权声明 | 读取 Card 后展示 `securitySchemes` 和 `security`。若网关要求鉴权，管理员在首次读取前填入对方要求的凭据。首版支持无凭据、HTTP Bearer 和 Header API Key。OAuth 登录、令牌刷新、证书和其他方案按真实接入需求评审。 |
| 请求 Header | 表格与 JSON 对象是同一份可编辑键值。Bearer 填名称 `Authorization` 和完整值 `Bearer <Token>`。API Key 和租户等参数按对方要求填写名称和值。JSON 要求值为字符串，非法内容保留在编辑区并提示修改。名称按大小写不敏感去重，拦截 `Host`、`Content-Length`、`A2A-Version` 等传输层保留字段。协议版本由所选接口产生。 |
| 源与凭据 | 读取 Card 时直接向管理员填写的 Card 地址发送已配置的 Header。公开 Card 可留空凭据。调用任务接口时，仅向已批准的完整接口地址发送这一组 Header。Card 声明的地址变化后重新确认。自动重定向关闭，Card URL 和调用地址都纳入出站策略。生产走 HTTPS，回环测试使用隔离运行配置。 |
| 诊断 | 使用脱敏后的方法、状态、耗时、远端 ID 和错误分类。Header 值只在有配置权限的连接编辑页和授权出站请求中使用。模型、调用记录和普通导出排除 Header 值。 |

上述区分来自 A2A 对凭据和消息字段的定义。 [认证与授权](https://a2a-protocol.org/v1.0.1/specification/#7-authentication-and-authorization) · [Agent Card](https://a2a-protocol.org/latest/specification/agent-card/)

### 高级配置

CLI 高级配置默认折叠。A2A 配置展示 Agent Card 地址和请求头。

Codex 和 CodeBuddy 复用 Builtin MCP 的进程启动结构，将命令、参数、环境和工作目录分别传给执行器。首版开放「启动命令」，留空用对应 CLI 名，管理员可填写执行机上已批准的命令名或可执行文件绝对路径。子命令、非交互参数和输出格式由各自适配器提供，采用参数数组启动。凭据与环境变量由执行账号配置管理。同一配置下环境检查、发起任务和会话续接使用相同的命令。Codex 另可指定配置档和模型支持的推理强度，留空沿用执行账号配置。

## 3. 连接表与任务记录

### 同类实现参考

| **项目** | **已核实的命名和关联方式** |
| --- | --- |
| Microsoft Foundry | 配置资源叫 **Project Connection**，类型 `RemoteA2A`。调用方在 Agent 的工具定义中添加 `A2ATool`，填写 `project_connection_id`。公开资料展示的是资源接口。 [配置与 Agent 示例](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/agent-to-agent) |
| Google ADK | 运行类叫 `RemoteA2aAgent`，构造时传 `agent_card`，调用方在自己的 `sub_agents` 中引用。示例用 Python 代码保存关系。 [ADK 接入示例](https://adk.dev/a2a/quickstart-consuming/) |
| Microsoft Agent Framework | `A2AAgent` 将远端端点包装成可运行的 Agent，`A2ACardResolver` 用于读取 Card，`A2AClient` 用于协议调用。 [框架接口](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/agent-services/a2a) |
| DiCompute | API 叫 `/remote-agents`，返回对象类型为 `remote_agent`，保存的连接可作为运行时工具。 [产品 API](https://dicompute.ai/docs/remote-agents) |
| Project NANDA Registry | 初始 SQL 创建 `agents` 表，保存 `card_url` 等发现字段，面向 Agent Card 目录。 [迁移源码](https://github.com/projnanda/nanda-registry-server-repo/blob/main/server/db/migrations/001_init.sql) |

### `remote_agent_connections` 表结构

ORM 类名拟为 `RemoteAgentConnection`。一条记录对应一个可委派的目标。同一 Card 可用不同 Header 登记为不同连接。首版沿用 MCP 的 Console 管理权限来读写配置和直连调试。选用或解除关联在 Agent 编辑页操作，`agents.capabilities` 拟保存 `agent_connection:<id>` 引用，连接详情据此反查「已选用」。

| **字段** | **类型** | **语义** |
| --- | --- | --- |
| `id` | `String(36)`，主键 | 稳定 ID，供 Agent 关联与会话事件引用。改名不换 ID。 |
| `name` | `String(80)`，唯一 | 连接的本地显示名称。 |
| `description` | 可空 `Text` | 用途说明。 |
| `revision` | `Integer`，默认 1 | 当前连接记录的修改版本。保存时用读取版本做并发校验，旧编辑返回冲突。连通性快照也按此版本写回。 |
| `config` | 现有 `JSONType` | `version` 是配置格式版本，`protocol` 区分 `a2a`、`codex`、`codebuddy`，各自字段见下表。 |
| `inspection` | 可空 `JSONType` | 最近检查的连接版本、时间、结果及能力或执行环境摘要。首次保存为空，影响连接或执行的配置变更时失效。 |
| `created_at` | 现有 `UtcTimestamp` | 创建时间。 |
| `updated_at` | 现有 `UtcTimestamp` | 配置更新时间。检查时间单独记录在 `inspection`。 |

`config.version` 首版为 1。`revision` 属于单条连接，用于并发保存和检查结果校验。

| `config.protocol` | 专用字段 |
| --- | --- |
| `a2a` | `card_url` 必填，`headers` 可省略，`approved_interface` 由确认流程写入。 |
| `codex` | `execution` 为 `local` 或 `ssh`，SSH 时填写 `ssh_host`。可选 `workspace_root`、`model`、`command`、`profile`、`reasoning_effort`。 `sandbox` 为 `read-only` 或 `workspace-write`，默认只读。 |
| `codebuddy` | 同样配置 `execution` 和适用的 `ssh_host`。可选 `workspace_root`、`model`、`command`。 |

工作根目录默认是执行账号的 `~/.deep-agent/remote-agent-workspaces`。指定时填写执行机器上的绝对路径。两种模式均从 `<工作根目录>/<任务 ID>/work` 启动 CLI，补答和续跑复用目录和原生会话 ID。只有本轮选择附件时才创建 `work/inputs/<轮次 ID>/`，并把附件路径传给 CLI。声明的产物从 `work/outputs/<轮次 ID>/` 回收到 FileStore。任务目录暂不自动清理。

```json
{
  "version": 1,
  "protocol": "a2a",
  "card_url": "https://budget.example.com/.well-known/agent-card.json",
  "headers": {"X-API-Key": "<示例占位>", "X-Tenant": "finance-demo"},
  "approved_interface": {
    "url": "https://budget.example.com/a2a",
    "protocolBinding": "JSONRPC",
    "protocolVersion": "1.0"
  }
}
```

`approved_interface` 在管理员确认 Card 中的目标后写入。接口如声明 `tenant`，保留并随 A2A 请求发送。Header 按键值整组保存，删除行即从配置删除。沿用现有配置存储方式，含凭据行具有与其他明文凭据相同的数据库和备份访问边界。列表只回非敏感摘要。Agent 包、普通导出和诊断排除 Header 值。表按连接 ID 读取，名称唯一索引即可支撑首版目录。 [现有模型](../../../src/deep_agent/db/models.py) · [A2A 请求参数](https://a2a-protocol.org/v1.0.1/specification/#321-sendmessagerequest)

### 复用已有记录

| **记录** | **写入内容** | **重启后的表现** |
| --- | --- | --- |
| `agents.capabilities` | 外部连接的稳定 ID 引用。从 Agent 编辑页添加或移除。执行时校验当前用户、App/Space、执行中的 Agent 与所选连接。 | 已保存的引用可继续使用。关联解除、配置缺失或授权变化后拒绝该 Agent 发起、补答或回查。 |
| `chat_session_events` | 复用工具调用／结果事件和 `session_id`、`turn_id`、`run_id`、`tool_call_id`。工具结果的受控 metadata 保存 `connection_id`、`connection_revision`、`invocation_id`、`outbound_message_id`、已知的 `remote_task_id`／`remote_context_id`、最新状态与时间、暂停提问标识和脱敏结果摘要。 | 已写入的结果可从原会话查看。有远端 Task ID 且同一有效连接版本和当前授权通过时，按需发 `GetTask`。远端任务过期或连接已变更时保留历史，并标明回查不可用。 |
| 进程内近期调用 | 保存本地 Agent 调用 ID、接入方式、所属身份及 App/Space、连接版本、会话关联、远端状态与 ID、时间线和摘要。首版默认保留提交后 24 小时、每个进程最多 30 条，按提交时间淘汰。 | 重启后清空，从空列表开始。 |
| 直连调试 | 对话、附件引用和临时续接句柄只保存在调试区的进程内状态。 | 重启后清空。 |

近期调用 Store 独立于适配器、调试对话和会话事件。服务按调用 ID 写入和更新有界记录，列表、详情按授权范围读取，具体 TTL 与容量由内存实现处理。后续可在同一接口下换成持久化实现。运行中的任务和它的取消句柄由执行层持有，列表淘汰不会取消执行。

工具达到等待预算时及时返回带已知远端 ID 的结果，供现有事件存储落下回查锚点。进程若在结果事件写入前退出，可能没有可用的远端 ID。此时显示发送结果未知。

### 迁移范围

`016` 仍为同一内部开发版本。把 `remote_agent_connections` 及 `name` 唯一约束合入原有 `016`，与 `agent_publications` 同时交付，`down_revision` 保持 `015`。Agent 绑定和恢复锚点沿用 `agents.capabilities` 和 `chat_session_events` 的现有结构。

验证从 `015` 升级及新建库两条路径，确认配置表、唯一约束和原有数据。早期内部 `016` 测试库须比对实际表结构，隔离联调用新库。实际执行时间、锁影响、DDL 权限与发版窗口由目标数据库验证。回退应用可保留配置表及数据。

## 4. 实现路线

### 主 Agent 委派与文件链路

使用 Pydantic AI Harness 的 `SubAgents`、`RemoteSubAgent` 和 `TaskControl`。优先复用 `delegate_task`、`answer_task`、`message_task` 和 `cancel_task`，各执行适配器处理协议差异。A2A 使用官方 SDK，Codex 和 CodeBuddy 按各自 CLI 接口执行。按 Agent 已选用的连接生成可用委派对象。各适配器把自己的 Task／Context／Thread／Session ID 关联到 Harness Task ID。

Harness 的委派工具输入是任务文本，远程任务完成事件的结果是文本。DeepAgent 在宿主层把当前调用授权的附件引用与委派关联，按适配器提交文件，必要时为按需选择附件补充窄工具。执行结束后由适配器读取授权产物并写入现有 FileStore，再用可信的 `generated_files` 元数据把文件引用挂到本地工具结果与会话。模型获得业务结果和可用文件引用，文件字节、搬运和远端凭据留在执行层。后续大沙盒接入时，文件落点可在文件适配层调整。

所有接入都要求提交附件、等待补答、续跑、返回文字结果和交付生成文件。同一 Run 内优先用 Harness 原生任务工具。需要用户下一轮提供信息时，优先复用 Harness 的 `DelegationTasks` 在进程内维持同一会话的任务，并以任务 ID 和问题 ID 接收已授权的答复。原型验证使用 `InMemoryTransport` 和模型夹具，已确认主 Agent 的首次 Run 可以结束，宿主随后补答时远端继续原任务。取消按执行端确认的最终状态展示。CLI 联调使用隔离工作目录，验证输入暂存、原会话续跑和产物回收。生成文件用有权限写出文件的配置验收，Codex 只读配置另测其权限行为。 [Harness 托管委派任务](https://pydantic.dev/docs/ai/harness/subagents/#managed-delegation-sessions)

### 开发顺序

1. **出站客户端。** 在隔离夹具里先用仓库已有的官方 SDK 验证 Card、JSON-RPC 1.0、`SendMessage` 和 `GetTask`。服务端统一检查配置允许的出站目标、验证请求参数、注入 Header 和处理超时。保存和探测是独立操作。探测结果可标识「Card 可读」「接口待确认」或相应失败阶段，真实消息返回后才能确认调用成功。
2. **连接管理与直连。** 单独提供连接列表／详情、保存、连通性检查和调试接口。Console 浏览器只请求自己的后端。一次调试对话绑当前操作者、连接和版本，「新对话」分配新的本地对话句柄。首次发送可收标准 Message 或 Task。对直接 Message 只显示返回内容及可用的 Context ID。收到 Task 后用有界轮询跟踪，切走页面只停止观察，回到详情可主动回查。取消需得到远端确认。
3. **本地 Agent 委托。** 复用同一个出站客户端。Agent 编辑和执行两处都校验绑定；执行上下文由服务端提供，模型只提交任务文本。补答时从同一用户、会话和连接下的待补任务读出 ID。多个待补任务时请用户明确选择，发送前核对远端仍在等待同一问题。调用结果用现有工具事件写入，授权的诊断视图据此显示任务轨迹。
4. **近期调用。** 接入首页的连接、接入方式、状态和关键字筛选。行详情区分远端失败、远端任务不存在与本地观察超时；超时且已有 Task ID 时提供回查。对需要鉴权的响应显示「待鉴权」，由连接管理员处理。远端鉴权信息仅走配置与出站客户端。

## 5. 调试和验收

从真实主 Agent 会话发起，使用一份合成附件。接收方主动提出问题，补答后继续原任务或原外部会话，返回文字结果并生成一个文件。调用方从 DeepAgent 会话下载 FileStore 上的产物，核对名称、内容及授权。

| 接入 | 验收对象 |
| --- | --- |
| A2A | DeepAgent 调自身发布服务完整闭环。另用现有 Langflow 或通过检查选出的外部 A2A 服务验证对端兼容。 |
| DeepAgent MCP | 通过普通 MCP 配置调自身发布服务，覆盖提交附件、`reply_task`、读结果和授权取件。远程 Agent 近期调用表无该会话记录。 |
| Codex | 本机 CLI 完成附件、补答、原 Thread 续跑、结果和产物回收。 |
| CodeBuddy | 本机 CLI 完成附件、补答、原 Session 续跑、结果和产物回收。 |

| **场景** | **操作与断言** |
| --- | --- |
| 配好连接 | 填 Card 地址及对方要求的 Header，保存后点「连通性」。对照远端名称、协议、接口、租户和检查时间。分别验证公开 Card、需凭据的 Card、错 Key、无兼容接口和 Card 声明地址变化。 |
| 发起与观察 | 在直连调试区发送「核对预算 100 和实际 120 的差额」，在同一区域查看回复和 Task 状态。再从本地 Agent 委派相同任务，确认只有委派记录出现在首页。先用可控 A2A 夹具验证，再用两套隔离实例及实际第三方 Agent 验证兼容性。 |
| 补答与追问 | 让远端返回 `INPUT_REQUIRED` 并提问会计期间。输入「2026-10」后，发送新的 `messageId` 与原 `taskId`、`contextId`，核对同一 Task 继续。完成后追问沿用 Context，由远端决定返回新 Task 或直接 Message。「新对话」从空 Context 开始。 [A2A 多轮交互](https://a2a-protocol.org/v1.0.1/specification/#34-multi-turn-interactions) |
| 断开、重启与重试 | 已拿到 Task ID 时用 `GetTask` 查远端状态。验证近期调用超时和超量淘汰不影响正在执行的任务。仅重启调用方，验证近期调用和调试对话清空，已落库工具事件仍能从原会话回查。再测远端过期、连接变更和撤权。发送响应丢失又无 Task ID 时显示结果未知。只有对端明确支持按 `messageId` 去重才按原 ID 重试。 [A2A 幂等性](https://a2a-protocol.org/v1.0.1/specification/#331-idempotency) |
| 授权与回归 | 用有绑定和无绑定的本地 Agent 分别调用，复测解除绑定、撤权、跨会话补答和 Header 脱敏。检查表格和 JSON 切换、首页按接入方式筛选、直连调试与近期调用隔离，以及现有 A2A 服务端、MCP、普通聊天的回归。 |
