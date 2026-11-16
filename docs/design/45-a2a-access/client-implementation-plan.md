# DeepAgent 外部调用实施方案

45 号课题 · 连接表与关联方式已确认

[打开 HTML 原型](<Prototype A2A客户端接入/index.html>) · [A2A 协议与版本边界](service-and-client-design.md)

## 1. Console 与调用入口

一级导航用「外部调用」，放在 MCP 附近。「外部调用」页上方是连接，支持卡片和列表，下方是全局近期调用，提供任务名称或 ID、连接、发起方式、状态筛选及右侧详情。连接上的「外部 · A2A」表示协议。任务的发起方式单独记录为直连调试或本地 Agent。

进入某条连接后，左侧设置 Card 地址和请求 Header，右侧直接展示「直连调试」和当前对话。直连调试和本地 Agent 的调用都进入首页近期列表。在首页选择连接即可查看该连接的记录，点行在右侧看远端 Task ID、Context ID、结果和进度。仍有效的直连记录可打开原调试对话。本地 Agent 的调用在现有会话轨迹中呈现为可展开的工具调用。

管理员登记连接并填写对方要求的 Header。本地 Agent 编辑页沿用能力选择入口，显式选择可用连接。运行时按当前租户、已配置的 Agent 关联和当前调用者权限过滤，直连调试要求配置管理权限。配置后，调试和已授权的本地 Agent 调用共用出站客户端。对方没有 App 概念也可接入。对方要求的租户、项目等静态值走自定义 Header。确实有业务报文契约时，由该连接的适配层显式映射到规范消息字段或双方约定的 `metadata`，普通连接页只配置传输信息。 [A2A 1.0.1 规范](https://a2a-protocol.org/v1.0.1/specification/)

## 2. Card、地址与鉴权

| 配置 | 建议实现 |
| --- | --- |
| Agent Card | 填完整 URL。读取后展示远端名称、`supportedInterfaces`、认证声明和能力。首版选择受支持的 A2A 1.0 JSON-RPC 接口，找不到匹配接口时展示诊断。连接校验只读取 Card、验证接口和配置，调试发送才创建任务。 |
| 鉴权声明 | 读取 Card 的 `securitySchemes` 和 `security`，便于确定对端期望的 Header。首版能发送公开访问、HTTP Bearer 和 Header API Key 所需的请求。OAuth 登录、令牌刷新、证书和其他方案按真实接入需求评审。 |
| 请求 Header | 表格与 JSON 对象是同一份可编辑键值。Bearer 填名称 `Authorization` 和完整值 `Bearer <Token>`。API Key 和租户等参数按对方要求填写名称和值。JSON 要求值为字符串，非法内容保留在编辑区并提示修改。名称按大小写不敏感去重，拦截 `Host`、`Content-Length`、`A2A-Version` 等传输层保留字段。协议版本由所选接口产生。 |
| 源与凭据 | Card 探测默认匿名。确需给 Card 发 Header 时，由连接管理员确认 Card 地址是否也可接收同一组 Header。运行请求只向已批准的完整接口地址注入 Header。Card 声明的地址变化后重新确认。自动重定向关闭，Card URL 和调用地址都纳入出站策略。生产走 HTTPS，回环测试使用隔离运行配置。 |
| 诊断 | 使用脱敏后的方法、状态、耗时、远端 ID 和错误分类。Header 值只在有配置权限的连接编辑页和授权出站请求中使用。模型、调用记录和普通导出排除 Header 值。 |

上述区分来自 A2A 对凭据和消息字段的定义。 [认证与授权](https://a2a-protocol.org/v1.0.1/specification/#7-authentication-and-authorization) · [Agent Card](https://a2a-protocol.org/latest/specification/agent-card/)

## 3. 连接表与任务记录

### 同类实现参考

| **项目** | **已核实的命名和关联方式** |
| --- | --- |
| Microsoft Foundry | 配置资源叫 **Project Connection**，类型 `RemoteA2A`。调用方在 Agent 的工具定义中添加 `A2ATool`，填写 `project_connection_id`。公开资料展示的是资源接口。 [配置与 Agent 示例](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/agent-to-agent) |
| Google ADK | 运行类叫 `RemoteA2aAgent`，构造时传 `agent_card`，调用方在自己的 `sub_agents` 中引用。示例用 Python 代码保存关系。 [ADK 接入示例](https://adk.dev/a2a/quickstart-consuming/) |
| Microsoft Agent Framework | `A2AAgent` 将远端端点包装成可运行的 Agent，`A2ACardResolver` 用于读取 Card，`A2AClient` 用于协议调用。 [框架接口](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/agent-services/a2a) |
| DiCompute | API 叫 `/remote-agents`，返回对象类型为 `remote_agent`，保存的连接可作为运行时工具。 [产品 API](https://dicompute.ai/docs/remote-agents) |
| Project NANDA Registry | 初始 SQL 创建 `agents` 表，保存 `card_url` 等发现字段，面向 Agent Card 目录。 [迁移源码](https://github.com/projnanda/nanda-registry-server-repo/blob/main/server/db/migrations/001_init.sql) |

### `agent_connections` 表结构

ORM 类名为 `AgentConnection`，协议调用复用官方 A2A 客户端。一条连接对应一套远端 Agent Card 和请求 Header。同一 Card 可用不同 Header 登记为不同连接。保存后进入连接目录，可在外部调用页直连调试，也可在 Agent 编辑页选用。首版沿用 MCP 的 Console 管理权限来读写配置和直连调试。选用或解除关联都在 Agent 编辑页操作，连接详情的「已选用」从 `agents.capabilities` 反查。

| **字段** | **类型** | **语义** |
| --- | --- | --- |
| `id` | `String(36)`，主键 | 稳定 ID，供 Agent 关联与会话事件引用。改名不换 ID。 |
| `name` | `String(80)`，唯一 | 连接的本地显示名称。 |
| `description` | 可空 `Text` | 用途说明。 |
| `revision` | `Integer`，默认 1 | 当前连接记录的修改版本。保存时用读取版本做并发校验，旧编辑返回冲突。连通性快照也按此版本写回。 |
| `config` | 现有 `JSONType` | `version`、`protocol`、`card_url`、`headers`、`send_headers_to_card`、`approved_interface`。 |
| `inspection` | 可空 `JSONType` | 最近检查的版本、时间、结果、远端名称、接口和能力摘要。首次保存为空，地址或 Header 修改时失效。 |
| `created_at` | 现有 `UtcTimestamp` | 创建时间。 |
| `updated_at` | 现有 `UtcTimestamp` | 配置更新时间。检查时间单独记录在 `inspection`。 |

`config.version` 表示配置 JSON 的格式版本，`revision` 表示这条连接改过几版，Alembic Revision `016` 表示整个数据库的结构版本。

```json
{
  "version": 1,
  "protocol": "a2a",
  "card_url": "https://budget.example.com/.well-known/agent-card.json",
  "headers": {"X-API-Key": "<示例占位>", "X-Tenant": "finance-demo"},
  "send_headers_to_card": false,
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
| 进程内近期调用 | 本地调用 ID、发起方式、所属身份及 App/Space、连接版本、可选会话关联、远端状态与 ID、时间线、有限的对话和结果摘要。首页列表从这里筛选。 | 原型显示最近 30 条。首版单实例，容量和 TTL 由联调测定，首页窗口与独立调试对话重启清空。 |

工具达到等待预算时及时返回带已知远端 ID 的结果，供现有事件存储落下回查锚点。进程若在结果事件写入前退出，可能没有可用的远端 ID。此时显示发送结果未知。首页的有界列表和正在处理的调用分别限额，列表溢出不取消正在处理的任务。第三方产物首版只投影可展示的名称、类型和安全引用摘要，文件处理随 Workspace 交付。

### 合入 Revision `016`

设计原计划在 Revision `016` 正式发布前合入 `agent_connections`，按上面的八个字段建立，并为 `name` 加唯一约束。Agent 侧关联继续写入现有的 `agents.capabilities`。当前 A2A 服务端分支的 Revision `016` 只有 `agent_publications`，出站迁移尚未实施。

实施前核对目标数据库的实物表结构。已经执行过旧版 `016` 的开发库会跳过同 revision 的迁移，需保留数据并单独补齐结构。发布验证以从 `015` 升级和新建库两条路径为准，并确认两张表、唯一约束和原有数据。实际执行时间、锁影响、DDL 权限与发版窗口由目标数据库验证。回退应用可保留配置表及数据。

## 4. 实现路线

1. **出站客户端。** 在隔离夹具里先用仓库已有的官方 SDK 验证 Card、JSON-RPC 1.0、`SendMessage` 和 `GetTask`。服务端统一检查配置允许的出站目标、验证请求参数、注入 Header 和处理超时。保存和探测是独立操作。探测结果可标识「Card 可读」「接口待确认」或相应失败阶段，真实消息返回后才能确认调用成功。
2. **连接管理与直连。** 单独提供连接列表／详情、保存、连通性检查和调试接口。Console 浏览器只请求自己的后端。一次调试对话绑当前操作者、连接和版本，「新对话」分配新的本地对话句柄。首次发送可收标准 Message 或 Task。对直接 Message 只显示返回内容及可用的 Context ID。收到 Task 后用有界轮询跟踪，切走页面只停止观察，回到详情可主动回查。取消需得到远端确认。
3. **本地 Agent 委托。** 复用同一个出站客户端。Agent 编辑和执行两处都校验绑定；执行上下文由服务端提供，模型只提交任务文本。补答时从同一用户、会话和连接下的待补任务读出 ID。多个待补任务时请用户明确选择，发送前核对远端仍在等待同一问题。调用结果用现有工具事件写入，授权的诊断视图据此显示任务轨迹。
4. **近期调用。** 接入首页的连接、发起方式、状态和关键字筛选。行详情区分远端失败、远端任务不存在与本地观察超时；超时且已有 Task ID 时提供回查。对需要鉴权的响应显示「待鉴权」，由连接管理员处理。远端鉴权信息仅走配置与出站客户端。

## 5. 调试和验收

| **场景** | **操作与断言** |
| --- | --- |
| 配好连接 | 填 Card 地址及对方要求的 Header，保存后点「连通性」。对照远端名称、协议、接口、租户和检查时间。分别验证公开 Card、需凭据的 Card、错 Key、无兼容接口和 Card 声明地址变化。 |
| 发起与观察 | 在右侧发送「核对预算 100 和实际 120 的差额」。先用可控 A2A 夹具验证直接 Message、立即受理及后台执行，再用两套隔离实例验证当前 DeepAgent 服务端，最后用实际第三方 Agent 验证兼容性。抓取脱敏方法、状态、耗时和 ID。左侧显示 Card 检查，右侧显示远端回复与 Task 状态，首页能找到同一记录。 |
| 补答与追问 | 让远端返回 `INPUT_REQUIRED` 并提问会计期间。输入「2026-10」后，发送新的 `messageId` 与原 `taskId`、`contextId`，核对同一 Task 继续。完成后追问沿用 Context，由远端决定返回新 Task 或直接 Message。「新对话」从空 Context 开始。 [A2A 多轮交互](https://a2a-protocol.org/v1.0.1/specification/#34-multi-turn-interactions) |
| 断开、重启与重试 | 已拿到 Task ID 时用 `GetTask` 查远端状态。仅重启调用方，验证首页窗口变空，已落库工具事件仍能从原会话回查。再测远端过期、连接变更和撤权。发送响应丢失又无 Task ID 时显示结果未知。只有对端明确支持按 `messageId` 去重才按原 ID 重试。 [A2A 幂等性](https://a2a-protocol.org/v1.0.1/specification/#331-idempotency) |
| 授权与回归 | 用有绑定和无绑定的本地 Agent 分别调用，复测解除绑定、撤权、跨会话补答和 Header 脱敏。检查原型的表格和 JSON 切换、首页筛选、从记录打开有效调试对话，以及现有 A2A 服务端、MCP、普通聊天的回归。 |
