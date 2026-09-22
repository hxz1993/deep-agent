# AGENTS.md — 后端（deep_agent）

> 父索引：[`../../AGENTS.md`](../../AGENTS.md)

后端 Python 包：FastAPI 传输层、Pydantic AI 运行时封装、能力层、会话存储、Agent/MCP 配置。

源码唯一位于 `src/deep_agent/`；启动、维护与测试从仓库根执行并设置 `PYTHONPATH=src`。
本地与线上都加载这份源码，根目录不再保留包链接，渠道入口统一为 `deep_agent.channels.gateway`。

---

## 快速定位

| 任务 | 先看文件 |
| --- | --- |
| 启动与全局状态 | `main.py` → `core/context.py` |
| 对话入口 | `api/v1/chat.py` |
| Agent 执行 | `agent/runner.py` |
| 模型与 Provider 适配、目录标准化 | `models/`；CRUD 编排在 `services/llm_configuration.py` |
| 能力注册 | `capabilities/__init__.py` |
| 配置解析 | `options.py`（`OPTION` + accessor）；DB URL 见 `db/url.py` |
| MCP 加载 | `capabilities/mcp/resolver.py`、`db_loader.py`、`builtin.py` |
| 渠道网关与企微 Adapter | `channels/gateway/`、`channels/`、`api/v1/channel_accounts.py`、`db/stores/channel.py` |
| 组件集成 | `integrations/catalog.py` 定义目录；`api/v1/integrations.py` 管理配置、`integration_runtime.py` 读取与接口示例；`db/stores/integration_settings.py` 持久化 |
| 管理员诊断日志 | `diagnostics/`、`api/v1/diagnostics.py`；模型/工具观察在 `agent/diagnostics.py` |

---

## 架构与数据流

### 诊断日志边界

`diagnostics.emit` 只接收白名单事实，后台有界队列负责安全序列化后的双出口与文件轮转；
日志失败不改变模型、工具、SSE 或持久化行为。诊断文件不是会话 Event Store，不新增 RUN 回执。
管理员查询只读取当前实例受控文件，与普通会话所有者权限分开；不增加 MCP 容器日志或自动探测。
新增日志点先注册事件名与基础描述，由 `diagnostics/messages.py` 从安全事实生成可读正文；不传用户或模型正文、完整参数、结果、异常原文或凭据。查询保留实际写入正文。详见
[`诊断日志指南`](../../docs/guides/diagnostic-logs.md)。

取消诊断按 Session/Run 关联停止接口入口、活动执行首次取消来源和发送连接结束记录。
`http_disconnect` 只在实际收到 ASGI 断连消息时使用，不能由 `CancelledError` 推断；
后者无明确证据时为 `task_cancelled_unknown`，更外层的服务退出记录可能晚于执行终态。
`api/v1/stream_diagnostics.py` 仅包装现有 ASGI 收发，不额外读消息、不改取消及心跳策略；
观察流 `/live` 不接入发送连接的取消观察。回归见 `test_cancel_diagnostics.py` 和 `test_session_live_api.py`。

OpenAI 兼容协议的 HTTP 日志在 `agent/model_api_diagnostics.py`；只记录安全地址、状态与工具名清单。
原 HTTP 字节流透明包装额外记录首字节、累计传输量、最近字节年龄与消费状态，响应头后30秒、之后60秒
快照不随心跳重置；不读正文、不解析协议、不加超时。字节到达不能证明模型已输出有效内容。
流式请求在 SDK 消费者任务中延迟发出，须在透明模型包装器的实际调用边界建立 ContextVar，
不能仅依赖 `wrap_model_request` 的任务上下文。测试须覆盖真实 SDK 的流式成功/400及请求体等价。

诊断账号资料使用 `security/diagnostic_users.py`，沿用其他业务组件的 System 跨空间接口
`POST /user/get-user-detail-list`，由 System 转查 Account。读取 `PLATFORM_OPTION.server.system`
（`EXPORT_SYSTEM_SERVER_NAME`），只传管理员当前认证和本次涉及的 ID，不传 App / Space。
每批最多100个、总预算2秒；独立返回 username/nickname/email/avatar_url，兼容 display_name 仍优先用户名；仅无平台凭证的 local bypass 才使用本地资料，
缓存按调用者权限隔离。服务地址与 `BaseAPI` 共用
`deepfos_platform.discovery.target.normalize_service_target`：`http://system-server` 先变成
逻辑服务名再走发现；不能因包含协议就绕过 Eureka。真实域名、显式端口与网关路径仍直连，
失败不隐式回退。当前用户与管理员校验同步遵循此规则。历史原因和验收边界见
[平台接入基础](../../docs/guides/platform-integration.md)。

### 历史工具结果匹配

`agent/history.py` 在补齐缺失工具结果前，按同一助手消息段、调用 ID 和工具名识别用户消息之后的真实结果或重试。
Deferred 续跑可能把用户输入单独先入库，再写工具结果；不能仅因遇到用户消息就判定调用取消。
匹配不跨下一条助手响应，不按全局调用 ID 去重；原始事件不重写，正式 interrupted 响应继续由 SDK 处理。
日志 `history_tool_result_matched/synthesized` 关联当前读取 Run 和历史 Tool Call，正文不入日志。
回归入口为 `test_history_repair.py`、`test_history_resume_persistence.py`。

### v0.8.5 真实事件及时持久化

`agent/message_persistence.py`通过 SDK 公开节点和已接受工具事件插入真实消息内容，
采用短数据库事务，不能等待整个 Run 的逻辑锁；不新增运行快照、计数回执或正常结束事件。
完整助手阶段、已接受调用及每个已完成结果及时提交，以来源消息和部分身份去重并归组读取。
半段流式文本仅保留在当前 SSE；正常取消/异常时尽力保存 SDK 已返回的部分内容，强杀不承诺半段恢复。
异常终止只记一次有原因的 `execution/error`，工具可恢复失败只记录自身结果，主动停止不新增异常。
已发布旧 RUN 数据原样保留，不增加解析/恢复兼容；Deferred 仅正式待操作状态可交互。详见
[`真实事件保存说明`](../../docs/guides/message-event-persistence.md)。

`core/session_live.py`只在API进程内有界转发已有POST SSE，`GET /sessions/{id}/live`必须指定当前
`run_id`并校验会话归属。订阅只活到该轮结束；观察连接断开不触发CancellationToken，空闲不订阅。
先订阅缓冲再读History，以基线Sequence和Run归属去重；溢出退回该轮历史轮询，不拼接残缺回放。
不得为观察流恢复RUN落库、增加另一个模型执行或跨进程持久化系统。

```
HTTP (REST/SSE)
    → api/v1/chat.py
    → AgentRunner（agent/runner.py，与 FastAPI 解耦）
    → pydantic_ai.Agent
    → ToolPackageRegistry.toolsets()
        ├─ builtin:general  UTC 时间 FunctionToolset
        ├─ builtin:sandbox  无状态 Python FunctionToolset
        ├─ builtin:ptc  Harness CodeMode + Monty（程序编排，显式关联）
        ├─ builtin:web  WebSearch Capability + 请求级公开Web Toolset（显式关联）
        ├─ mcp          请求时 `capabilities/mcp/resolver.py`（DB + mcp_servers/ 兜底）
        └─ ui           代码内置或数据库工具包 → ExternalToolset
    → Agent.skills → Harness Skills（SKILL.md 渐进披露）
    → runtime system tools（文件、Skill、MCP发现与Resource，按请求条件注入）
    → SessionStore（`db/conversation.py` 协调器；表 Store 在 `db/stores/`）
```

### 启动生命周期（`main.py` lifespan → `AppContext.bootstrap()`）

1. `configure_logging()` → `AppContext.bootstrap()`（见 `core/context.py`）
2. `upgrade_database()` → `bootstrap_database()` → 表 Store + `SqlConversationCoordinator`
3. `build_registry(stores=)` — 通用与Sandbox工具包；Sandbox Backend 可用时注册独立 `builtin:sandbox`
4. `init_context(ctx)` — 路由通过 `Depends(AppContextDep)` 注入
5. 关闭：`ctx.aclose()` → `reset_context()`

未配置 `DEEP_AGENT_DBMS_*` 时默认使用 SQLite 文件 `data/deep-agent.db`（可用 `DEEP_AGENT_SQLITE_PATH` 覆盖）。配置 `DEEP_AGENT_DBMS_*` 则使用 PostgreSQL / MySQL。

全新空库首次升级至Head时，`migrations/env.py`调用`db/initial_configuration.py`设置当前默认配置：
DeepSeek Provider（官方默认地址、Key为空）和`deepseek-flash`模型，保留默认Agent及其固定关联ID。
模型初始上下文1000000、最大输出384000，输入模态text/image；能力标签attachment、reasoning、
tool_call、structured_output、temperature、open_weights、interleaved。模型配置界面本身不因此改变。
只在迁移前无Revision且目标为Head时执行；历史Revision回放、已有库升级/重启和用户删除默认项后
都不覆盖或补建配置。已发布001/002迁移及表结构不改写；001/002支持离线SQL；003需要在线检查实际约束和数据，不支持离线SQL。

MCP 在请求时由 `resolve_mcp_toolsets` 解析（DB 行 + `mcp_servers/` builtin 兜底）。

渠道网关是独立可选进程`PYTHONPATH=src python -m deep_agent.channels.gateway`，与API复用同一代码、数据库和
FileStore协议。`channels/gateway/runtime.py`负责Account轮询与Adapter生命周期；`channels/service.py`
负责同Peer队列、命令、消息处理和SSE投影；`channels/contracts.py/registry.py`提供公共接口与注册表。
只有`channels/adapters/wecom/`引用官方`wecom-aibot-python-sdk==1.0.2`；连接、认证、心跳、重连、
事件分发、媒体下载解密和回复协议均调用SDK，不写第二套协议。当前仅企微Adapter，不宣称飞书已实现。
加载通过公共`reply_loading`交给Adapter，并传入累计公开正文；首段正文之前企微使用`<think></think>`原生占位，正文和终态复用同一Stream ID。
占位不入历史、不外发思考/工具细节；停止和失败以非空文本及`finish=true`收尾，不用定时器反复发送假省略号。
渠道临时正文按阶段换行累加；thinking、tool_call和context_compaction_progress必须保留已有正文，
在下方显示“处理中…”，后续正文替换等待提示并继续累加（不累加思考、工具参数或结果）。
企微客户端实测：末尾附加`<think></think>`会隐藏此前所有正文，不能用“累计正文+末尾think”冒充保留进度；
前置think也不同时显示原生三个点，因此有正文后的等待提示使用文字，不承诺原生动画。
连续加载事件不重复发占位。正常结束必须以`final.output`覆盖全部预览，显式空正文不能回填过程；
只有缺少output字段的旧事件才回退末段文本。预览失败后本Run停止预览但继续执行并尝试最终投递，
处理状态与最终投递状态分离，不重跑Agent。企微不增加伪造引用条或“回复：原问题”文本前缀。
当前只允许一个API worker与一个Gateway副本。API负责Alembic迁移，Gateway不维护固定数据库版本白名单；健康接口的database_revision仅为启动时读取的诊断信息。
新增迁移必须回归真实Gateway lifespan及combined_runtime双进程启动，不能仅测绕过lifespan的HTTP路由。
Gateway入口必须显式使用`uvicorn.run(..., workers=1, reload=False)`，不继承容器的
`WEB_CONCURRENCY`；只改成应用导入字符串会放开多worker并造成渠道连接竞争，不是正确修复。
Jenkins网关启动版`Jenkinsfile.gateway`可由`combined_runtime.py`在同一容器托管两个独立进程：
先等待API完成迁移并健康，再启动回环地址上的Gateway；任一子进程退出即整体退出。
仅API版`Jenkinsfile.api-only`在同一次patch中更新镜像、恢复Gunicorn command/args并清空API网关地址，
可用于同环境停用网关；重新发布网关版会恢复双进程入口及子进程网关地址，两者均不降级数据库。
原始脚本按原字节保存于[`docs/archive/jenkins/Jenkinsfile.original`](../../docs/archive/jenkins/Jenkinsfile.original)，
仅供历史对照；活动入口为`deploy/jenkins/`下的`Jenkinsfile.gateway`、`Jenkinsfile.api-only`与
`Jenkinsfile.diagnose`，发布配置见[`部署指南`](../../deploy/jenkins/README.md)。
本地可传`--local-config ~/.config/deep-agent/sqlite.env --api-port 8000`，此时API子进程仍使用
`local_dev`，不会丢失回环限制、本地身份注入或本地FileStore；配置中的身份字段不复制到Gateway环境。

渠道配置中的`session_user_id`由企业管理员直接填写，Console标签“会话用户”；只决定新建Session
归属，不签发平台凭据。Gateway带内部Token和Account ID，API从当前配置解析用户；普通请求仍按
平台可信身份鉴权。企微Peer类型沿用原生`single/group`，ID分别取`from.userid/chatid`；Binding的
`peer_metadata` JSON保存来源会话的`user_id/chat_id/display_name`白名单，缺失名称不造值。
私聊身份直接从Binding读取；群发送者不作为群身份。仅Session改绑或来源信息确实变化时更新Binding。
私聊标题为 `User｜名称(原始 ID)`，无姓名时为 `User｜原始 ID`；群标题固定为 `Group｜Chat ID`，不附加群名。括号使用半角，保留原始定位 ID。
Run只保留created_at/finished_at，接收即认领再排队；调用前必须落running以防未收到SSE Run ID时误重跑。
处理终态与投递状态分别更新，不能因最后回复失败重跑Agent或`/new`。不按SSE片段/心跳写台账。

`channels/commands.py`统一定义`/help /new /stop /status /whoami`与帮助目录，只有渠道入口识别；
`/reset`不支持，Console与其他客户端不增加共享拦截。`/new /stop`绕过普通队列、暂停派发、取消此前
等待项并最多15秒确认真实停止；超时不换绑，不派发直到确认退出。`/new`将Session、来源标题事件、
Binding当前指针和命令完成状态放在同一事务。群共享Session，群成员控制命令影响整个群。
`GET /api/v1/channel-commands`供Console只读帮助；Session取消及`run-status`接口验证内部Token、
Account与实际Session归属，不以修改后的Agent配置取消错误任务。
企微Adapter将`body.quote`转换为公共`InboundQuote`，引用文本经`channel.message_quote` Context Reference
进入当前User Event，引用媒体仍由官方SDK下载后走附件链路；原始帧/媒体URL/密钥不得外传。
命令只识别独立纯文本，不剥离`@`；必须@才送达的企微群聊不支持快捷命令，引用/附件/句中命令不触发。
群聊每条消息、私聊每个Session首轮以`channel.message_source` Context Reference保存发送者与Peer白名单；
私聊用持久化`user/message`是否存在判定，不用内存标记或Session刚创建标志，重启不重复、`/new`后重带。
来源进入User Event和
模型历史；Runs仍记录sender_id，Binding不随群成员发言改身份。API只为内部Token认证的渠道请求在
`build_runner_for_agent`追加中文渠道说明，不依据用户正文或context_refs启用，也不改普通客户端的Prompt。

渠道Session创建时写入`source=channel`标题事件及`channel`来源快照，阻止首轮模型标题覆盖。

普通会话标题在`agent/title_generator.py`单独关闭思考，不能修改主聊天的Model或默认设置。
DeepSeek新别名可能尚无SDK Profile：Responses使用原生reasoning开关，Chat和Kimi使用原生thinking开关；
Agent及标题请求均不传采样温度，使用模型服务端默认值。标题失败查`title_generation_*`诊断事件。
SessionInfo可选`channel`字段来源于该Session事件，不依赖当前Binding；人工改名不删除来源。
旧本地数据修复用`maintenance.channel_origins`，默认dry-run、apply先备份，停止服务后执行；不升级002。
若已有002仍含`peer_display_name`物理列，先用`maintenance.channel_peer_metadata`完成备份后的
JSON列转换和自动标题修正，再使用ORM维护入口。该脚本仅接受显式本地SQLite路径，不访问共享Test。

### 对话请求流（`POST /api/v1/chat/stream`）

请求预算在 `agent/request_budget.py`：默认 40 次模型请求，同一 `turn_id` 的 Deferred 续跑
从原始事件恢复用量；每次 Run 的 Token/usage 仍独立统计。模型响应首次保存前写入
`metadata.deepagent_request_budget.run_requests`，按来源消息归组计数，不能按 Part 事件条数计数。
最后一次请求在压缩与工具发现之后移除全部工具并要求正常总结，保持原有 SSE/History 协议。
`MessagePersistenceCapability` 显式包住同为 outermost 的 `ToolSearch`，保证搜索反馈补全后再保存，
不能放宽不可变内容校验来兼容中间结果。回归见 `test_turn_request_budget.py`。

1. 解析 `StreamChatRequest`；`session_id` 缺省则生成 UUID
2. **无 `agent_id`**：`build_runner(model?, capabilities?)` → `OPTION` 默认 `AgentSpec`
3. **有 `agent_id`**：`build_runner_for_agent()` → `agents` 表 → `AgentSpec.from_row()` → 存储 `(agent_id, session_id)`
4. `AgentRunner.run_stream()`：Session锁 → Event Store恢复模型Surface → Pydantic AI运行/压缩 → 临时SSE → 完整Part拆成Event；Run/Compaction终态按同一Event ID更新
5. `capabilities`和`skills`都是非空显式列表；空列表表示本Agent不关联该类资源，不存在默认包继承

Provider运行可用性只从类型、地址、凭据等真实配置派生，不保存`enabled`。v0.6.4起Model恢复
非空、默认开启的`enabled`：只有模型启用且Provider有效时才可由Agent或请求级`model_id`选用；
既有Agent引用不自动改写，但实际运行必须失败关闭。Skill、MCP、UI工具包和UI工具是否参与Run仍只看
Agent显式关联，不恢复资源自身的启停字段。

### 正式取消（v0.3.10）

停止按钮调用 `POST /api/v1/sessions/{session_id}/cancel`，由进程级 `ActiveRunRegistry` 找到
Pydantic AI `CancellationToken`。Runner 捕获 `RunCancelled` 后只持久化
`RunCancelled.new_messages()` 并返回真实 `cancelled` SSE（含 `usage`）；不再探测其他 result、
拼接流式文本、手工补 `ToolDenied` 或生成前端假事件。若 Provider 返回的最后一个非空部分响应
仍标为 `complete`，持久化前统一规范化为 `state=interrupted`，Event投影映射为
`assistant.status=interrupted`；同一 Run 内没有结果的工具在 Conversation History 投影为
`interrupted`，下一轮模型历史中的悬空 Tool Call 仍由 Pydantic AI 自动补
`outcome=interrupted`。取消时，所有已实时发布但尚未返回结果的工具调用发送
`tool_call_removed(reason=cancelled)`，关闭直播临时卡片；原始 Event 与 History 的
`interrupted` 审计事实仍保留。

普通工具在参数校验后发布 `tool_call`；若业务执行返回 Pydantic AI `ModelRetry`，对应官方
`RetryPromptPart` 必须映射为 `tool_call_removed(reason=model_retry)`。客户端按
`tool_call_id` 删除临时卡片，修正后的新调用再按新的 ID 正常展示。
0.8.5 起已接受并实际执行的业务重试仍保留调用及脱敏结果事实；未通过参数校验的中间纠错不保存成正式调用或孤儿结果。历史归组不能跨轮合并复用的调用 ID。

工具错误需按可纠正性分类：参数、对象选择或工具选择错误使用 `ModelRetry`；同样参数重试也
不会成功的下载、文件损坏、解析、大小上限或明确上游失败使用 `ToolFailed`。两者都由
Pydantic AI 形成合法 Tool Result，但只有 `ModelRetry` 消耗工具重试预算并要求模型纠正。

内置工具封装上游失败时，通过 `agent/tool_errors.py::tool_error_message` 保留操作说明、原生
错误类型、真实 HTTP 状态、业务码与有界脱敏提示。平台客户端的 `upstream_*` 字段与本地
ExpectedError 的 HTTP 200 包装状态分开；不从附件格式清单推断环境导出白名单。
原生文本只进入授权会话的 Tool Result，日志只记录白名单事实。文件读取、上传、下载中心登记、
回滚使用 `observe_tool_operation` 记录真实步骤，并关联 Session/Run/Tool Call；日志失败不影响
业务。清理失败与原始失败分别报告，不能把隐式异常上下文误当同一操作的根因，也不能宣称已回滚。
新增工具应复用这条错误链，不能只写“发布失败”，也不能裸拼完整响应 data、凭据、文件正文或堆栈。

流式接口必须先把 Active Run 注册到 Registry，再发布首条 `session` SSE，并将同一个
`CancellationToken` 传入 Runner。否则客户端刚拿到 `session_id` 就停止时，会在用户消息尚未
持久化的窗口内误报 `404 Session not found`。

PTC 默认不用于普通 MCP 调用或工具发现；仅在中间二进制/批量数据无需模型阅读时推荐使用。
PTC 执行错误用官方 `ToolFailedError + ToolReturnPart(outcome=failed)` 保留脱敏嵌套调用审计，
不转为消耗重试预算的 `ModelRetry`。取消与系统异常仍传播给 Runner 收尾，不能吞掉。

页面关闭或网络断开触发 Token 清理无人消费的 Run，不是正常停止入口。API 与 Runner 必须显式
关闭内层异步流，SDK 上下文退出前后捕获官方消息，并保存已有部分历史为中断；不拼接假消息。
EventPersister 写入失败需保留有界失败回执，使后续 flush 明确报错。进程强杀或数据库不可写
不在历史完整性保证范围内。UI Deferred 已经
Pending 时没有活动 Run，其表单取消仍通过 `DeferredToolResults` 处理。当前注册表只在单进程
内有效。v0.3.14 起每个 Pod 固定一个异步 Gunicorn Worker；多 Pod 发布仍必须使用会话粘性，
或后续增加共享取消协调器。

### MySQL 空闲连接（v0.3.11）

- MySQL 引擎使用 `pool_pre_ping=True` 和 `pool_recycle=90`，在 Test 默认 120 秒空闲超时前主动换新连接。
- 仅 pre-ping 阶段的 asyncmy `TCPTransport closed=True` / `handler is closed` RuntimeError 会被标记为断链并交给 SQLAlchemy 重连。
- 不对已经开始执行的业务 SQL 做通用重试，避免写请求被重复执行；SQLite 和 PostgreSQL 不使用 MySQL 的 90 秒回收策略。
- 排障、环境取值和未来配置项约定见 [`../../docs/guides/mysql-idle-connection-timeout.md`](../../docs/guides/mysql-idle-connection-timeout.md)。

### 能力装配与模型输入

DeepAgent 自研 `capabilities.registry.ToolPackage` 是产品配置单位，包含分类、展示元数据、工具目录和恰好一个 Toolset；Pydantic AI 的 `AbstractCapability` 才是可介入模型请求、消息历史、工具准备和事件生命周期的原生扩展点。当前上下文压缩使用这一层；DeepSeek V4 已交由 Pydantic AI 官方 Provider/Profile，不再注册项目自定义重试能力。

2026-09-19 独立搜索改版本地已实现，尚未发布。Web 统一披露普通 `web_search(query)`，不再向主会话
注入原生搜索 Capability，不按主会话模型选择搜索实现，也不切换 DeepSeek 主会话协议。
DeepSeek 搜索在 `web/deepseek.py` 中异步直调 Anthropic Messages，默认 `deepseek-flash`，
声明 `web_search_20250305`，只解析结构化结果和引用片段，不创建辅助 Agent。
Kimi 在 `web/kimi.py` 直接调用 `/v1/tools/search`，无 model、无辅助 Chat、Formula 或 Responses 后备。
DuckDuckGo/Tavily 复用 Common Tools，Exa/You.com 复用 Harness。运行时不限制模型名称，不自动降级或换供应商。

`web/settings.py` 将空配置、无记录和旧 native 归一为 `unconfigured`，读取不写库；坏引用、缺 Key、
无效 Provider 地址携带 `configuration_error`。不在装配阶段因这些配置问题拒绝 Run；实际搜索时使用
`ToolFailed` 生成同 ID 的失败 Tool Result，模型可继续回答，SSE/History/事件协议不变。
管理保存仅接受 `mode=provider`；三个 Console 入口不增加存储层级。Kimi 旧 model 读取忽略、保存移除；
复用 Provider 只存引用不复制 Key。GET 回显 `configuration_error`；旧 `/test-model` 返回410。
新库最终无默认搜索行：已发布001不改，`initialize_current_defaults()` 仅在空库初始化时清掉native种子；
历史升级不删除记录，旧备份恢复仍按未配置解释。不得引入新的隐式搜索默认供应商。

2026-09-10的原生Tool Search重名400兼容仍保留：MCP发现通过普通函数回退，不能重新继承SDK的原生ToolSearch。
该限制针对工具协议，不限制模型型号；旧原生工具消息仍保持历史回读。协议历史探测结论见
`../../docs/design/21-web-tools/web-tools-research.md`，不能用旧单句探针声称完整主会话原生搜索已接通。
回归入口包括 `test_web_search_settings.py`、`test_global_settings.py`、`test_web_search_probe.py`、
`test_kimi_web_search.py`、`test_web_search_failure_events.py`；测试搜索必须验证真实来源，HTTP200或回答文本不算成功。
2026-09-19管理草稿沿用已有Provider Key实测：Kimi 1510 ms、DeepSeek Flash 3894 ms、Pro 15388 ms；
不保存配置，耗时为单次观测；DuckDuckGo真实草稿测试约11.36秒通过。Tavily/Exa/You.com真实Key联调仍待补。

模型每轮直接看到的是：Agent/能力 Instructions、`message_history`、当前用户输入/附件和本轮 Tool Definitions。Toolset 负责列出、准备和调用工具；MCP Prompt 不会自动进入模型工具列表。MCP Resource 由宿主侧动态代理工具按需读取。

URL 与 Builtin MCP 的运行态不同：URL MCP 在管理员检测时保存 Tool Definitions 和 Server Instructions，Run 只读快照，首次真实调用时才创建官方 `MCPToolset`；Builtin MCP 仍由 Run-scoped Client 按需启动。两者最终工具名都由 `PrefixedToolset` 统一加 Server 前缀。缺少或配置失效的 URL 快照不披露工具，也不会因远端不可达阻塞普通聊天。

v0.6.2起的 Hybrid Code Mode 实现位于 `ptc/`；v0.6.3增加可信返回Schema、
总量受限的嵌套审计和更明确的SDK作用域说明。产品配置单位仍是
`builtin:ptc` ToolPackage，运行时由 `HybridCodeMode` Capability 在最外层包装
ToolSearch。只有已授权 MCP Toolset 会被标记为 Hybrid；已披露工具保留原生 Schema，
又可在 Monty 中组合调用。`load_file_data` 是 code-only 文件原语，不出现在普通工具列表；
其 Run Token、Base64 和大中间结果必须在进入 Event、History、SSE、轨迹、检索或日志前脱敏。

v0.6.5起的公开Web实现位于`web/`。`builtin:web`提供`web_search/web_fetch`，v0.6.6另向
图片输入模型注入外层普通工具`web_image_view`；该工具返回Pydantic AI原生`ImageUrl`且不进入Monty。
`web_fetch`的HTML薄适配只负责过滤脚本/样式并在原位置保留绝对图片URL，不自动发送图片。全局唯一
`global_settings.web_search`选择独立搜索Provider，但不能替代Agent显式授权。v0.6.7起
Web工具不进入PTC。不得因此开放PTC内部Socket、平台凭据、任意Header或
Internal HTTP。显式HTTP CONNECT代理下，`web/proxy_compat.py`只修正fake-IP
公共解析与TLS SNI传递，并覆盖Pydantic AI组装下一次请求时的`ImageUrl(force_download=true)`延迟
下载；SSRF地址校验、固定IP、重定向复检和正文解析仍由Pydantic AI负责。完整边界见
`../../docs/design/21-web-tools/`。

Web图片在当前`agent.run()`内部保持原生视觉结果；写入Event Store时只允许URL、MIME、
`force_download`和无敏感审计，下一次独立Run必须投影成轻量URL说明，禁止重复携带旧图片或Base64。
直接`web_fetch`二进制图片仍走原有`BinaryContent`与ToolOutputLimits，本版本不得修改大工具治理。

流式Run在模型或工具链最终异常时，必须从Pydantic AI `AgentRunEvents`实时句柄保存已经产生的
`new_messages/all_messages/usage`；仅保存尚未入库的真实内容并标记必要失败状态，已完成内容不得回滚。
执行终止异常记录 `execution/error`，不依赖是否已有Assistant，不得把已完成的工具轨迹丢掉。

新增横切能力时遵循：

1. 历史整理优先复用Harness公开能力；v0.6.0不再使用批量`ClearToolResults`，只对摘要模型副本中的
   单条超大结果做DSH式头尾裁剪。动态预算、Compaction Event、来源Hash、Evidence、SSE和目标复检
   属于DeepAgent产品语义。
2. 按 App、Space、权限或模态过滤工具优先使用 `PrepareTools`。
3. 当前已授权 MCP 全局候选不超过 10 个时全部直接披露；超过 10 个时统一进入 `DeferredLoadingToolset`，由本地词法检索预选 Top 3、模型 `search_tools` 返回 Top 5。首版不使用 Embedding，不持久化索引。
4. 生命周期观测优先使用 Hooks/Instrumentation，并先完成 Token、Cookie、文件和工具结果脱敏。
5. v0.4.0 起，通用大工具结果使用 Harness `ToolOutputLimits` + `Spill`，Store适配现有平台文件服务，
   不使用Harness默认本地文件Store。平台File ID同时作为当前会话的工具结果Artifact句柄；权限只能
   从当前会话原始Tool Result Event恢复，不能从摘要Evidence或模型参数扩权。
   v0.4.18的`export_file_to_download_center`可以主动把该句柄登记到下载中心，但不改变Artifact Store、
   不建立第二层ID或Artifact表，也不把下载记录反向当作模型读取权限。
6. Harness Memory 只保存明确的跨会话笔记，不替代聊天历史；namespace 必须由认证用户、Agent、App 和 Space 生成。
7. Harness `StepPersistence` 不替代现有 SQL 会话、SSE 和 Deferred/Pending；只有副作用账本、子 Agent 或崩溃恢复成为真实需求时再接入。
8. Harness 仍为 0.x。引入时必须将 `pydantic-ai-slim` 与 `pydantic-ai-harness` 固定为经过联合回归的准确版本，禁止继续依赖无上限 `>=` 漂移。
9. 不要通过修改 `AgentRunner` 主循环重复实现 Pydantic AI 已提供的机制。
10. Harness 0.26 `Skills` 只负责延迟披露 `SKILL.md` 正文；第三方 Skill 的安装、版本、权限、素材读取和脚本隔离仍由 DeepAgent 产品层负责。v0.3.23 的系统工具 `read_file` 统一读取会话附件与 Skill 素材，脚本执行只复用 Sandbox Backend 契约，不能把它们误写成 Harness 能力。
11. `IncludeToolReturnSchemas` 发生在模型请求前的 Tool Definition 准备阶段，不是工具执行后给真实结果追加 Schema。首版只允许按最终稳定协议名的明确白名单启用，并要求存在可信、稳定的返回 Schema；不得通过名称包含 `query` 等模糊规则自动扩大范围。延迟 MCP 工具只有被 Tool Search 披露时才携带其返回 Schema，并必须观测新增 Schema Token。真实结果过大或空字段过多继续由分页、投影或 `ToolOutputLimits`/Artifact 治理。
12. Prompt Cache 不是跨 Provider 的统一 DeepAgent 缓存。DeepSeek 等 Provider 的自动缓存只读取其 Usage 命中/未命中数据，不改写请求；显式 `CachePoint`、cache key 和 TTL 只能在模型目录确认具体 Provider 支持后启用。统一观测可以归一化指标，不能伪造统一缓存协议。
13. v0.3.17 使用 Pydantic AI 官方 `conversation_id/run_id` 贯通运行：`conversation_id=deepagent:{agent_id}:{session_id}` 在同一 Session 内稳定，每次发送、Deferred 恢复、重试或主动压缩使用新的 `run_id`。第一阶段只输出脱敏结构化日志和当前页面 Run 指标，不把高频运行数据写入业务 MySQL；后续 OTLP/Instrumentation 不得建立第二套 Agent 生命周期。

普通History遇到同一ModelResponse内文字与原生工具交错时，按工具Call/Return边界拆分Assistant展示项，
保留原始part_offset，最后正文才标记final；不能将全部文字合并到首段位置。首段保留原Item ID，
后续段以part_offset派生稳定ID，响应Usage只放最后一个Assistant段，避免重复统计。
这是只读投影，不拆写原始Message Event、不改模型历史或轨迹；旧会话重新读取History即可恢复。

框架能力是否应采用、部分采用或明确关闭，统一先查
[`../../docs/design/14-framework-capabilities/framework-capabilities-baseline.md`](../../docs/design/14-framework-capabilities/framework-capabilities-baseline.md)。
升级 Pydantic AI/Harness 时只做相对该基线的增量审计，不从历史 TODO 重新发散；没有真实触发条件的
框架能力不得因为“官方已经提供”就进入开发计划。

当前上下文压缩在模型配置有效`context_window_tokens`时启用，否则停用，并固定使用当前Agent模型。
自动、主动和Provider Overflow调用同一安全前缀、
八段摘要、Evidence与目标复检流程；Compaction从Running原位更新到终态，失败不改变Surface。
摘要总预算300秒，每约10秒发送非持久化进度；客户端取消复用正式Run Cancellation Token。Timeout不重试，
仅满足时间与剩余预算条件的429/502/503/504快速失败可重试一次。Provider原生CompactionPart作为普通
Provider Part保存，不能替代DeepAgent跨Provider摘要。

摘要安全边界将带 `tool_call_id` 的 `RetryPromptPart` 视为对应调用的终结，不能把已拒绝的调用永久保留为等待。
DeepSeek 摘要请求使用 `max_tokens` 并关闭辅助请求的思考，主对话思考配置不受影响；保留 SDK 原有 Profile 能力。
摘要输出最多分配目标预算的 20%，受模型上限及 4096 tokens 封顶，仍须通过最终目标与持久化前复检。
截断或格式校验失败也应保留 `summary_call` 的 usage、finish_reason 和输出上限，便于真实归因。

压缩前的预算估算、安全边界选择和Evidence整理必须在线性复杂度内完成，并把CPU准备工作移出SSE事件
循环；准备耗时超过心跳周期时继续发送`preparing`。实时SSE不重复发送持久化Conversation Item；
客户端用流式事件展示当前Run，并在正常结束、重连或重新打开会话时通过`/history`读取最终投影。

v0.3.14 起部署入口固定每个 Pod 一个异步 Gunicorn Worker，使 session 取消请求与进程内
`ActiveRunRegistry` 一致命中。主动压缩的 `context_compaction_result` 不再等待二次
`context-status` 查询，客户端收到终态后分别刷新 history 与 context-status。多个 Pod 的正式
取消仍需要会话粘性或共享取消协调器，不能依赖随机网关路由。

v0.3.5 的普通与流式聊天支持结构化 `context_refs`。公共外壳在 Schema 层严格校验；原始
`payload/metadata` 随消息保存，模型、Token 估算、自动/主动压缩只使用排除 `metadata` 的
XML + JSON 投影。业务字段语义仍由具体 Agent Prompt 负责。

上下文压缩课题入口见
[`../../docs/design/03-context-compaction/README.md`](../../docs/design/03-context-compaction/README.md)；
当前数据结构和运行方案见
[`../../docs/design/03-context-compaction/context-compaction-design.md`](../../docs/design/03-context-compaction/context-compaction-design.md)。

---

## 模块索引

### `agent/`

| 文件 | 职责 |
| --- | --- |
| `spec.py` | `AgentSpec`：`from_row()`、model、instructions、capabilities、skills |
| `history.py` | 模型历史过滤、取消修复和运行时辅助；不生成前端 DTO |
| `../conversation/` | Event → `ConversationItem`纯投影；供`/history`与持久化SSE共用 |
| `factory.py` | `build_agent()` — model 字符串或 OpenAI 兼容 endpoint |
| `../models/` | API、配置服务与 Agent 共用的 Provider/模型适配及 models.dev 目录解析 |
| `attachment_extractors.py` | 文本、DOCX、XLSX 按需解析、分页与文件安全限制 |
| `attachments.py` | 会话附件引用、原生模态校验和平台下载 |
| `runner.py` | `run()` / `run_stream()` — 会话锁、history、事件映射 |
| `planning.py` | Harness `Planning`装配、每Run Store和Tool Result计划快照 |
| `deps.py` | `AgentDeps`：session_id、user、agent_id、request_headers |
| `headers.py` | HTTP → MCP 的 header 白名单提取 |
| `events.py` | Pydantic AI 事件 → SSE 字典（使用 `singledispatch` 分层映射） |

### `capabilities/`

| 路径 | 职责 |
| --- | --- |
| `registry.py` | `ToolInfo` / `ToolPackage` / `ToolPackageRegistry` |
| `general/builtin.py` | 内置“通用工具”，当前提供 `get_current_time` |
| `mcp/db_loader.py` | 从 `mcp_servers` 构建 MCP ToolPackage 与快照 Toolset |
| `mcp/builtin.py` | 发现 `mcp_servers/<name>/server.py` |
| `mcp/toolset_builder.py` | url / stdio / builtin + header 注入 |
| `mcp/inspection.py` | URL MCP initialize、Ping、能力清单检测与快照 |
| `mcp/snapshot_toolset.py` | URL MCP 纯内存工具目录、首次调用建连与 Run 内连接复用 |
| `mcp/resources.py` | 按检测快照开放、运行时实时读取的 Resource 代理工具 |
| `mcp/run_scoped.py` | builtin MCP：按 agent run 租约 HTTP client |
| `mcp/lease_manager.py` | worker 级 MCP lease + shared registry touch |
| `ui/registry.py` | builtin/manual UI 工具按包筛选、合并和 `prepare_ui_tool_packages_for_spec` |
| `../sandbox/toolset.py` | `builtin:sandbox`；代理 PythonExecutionBackend |

`../files/`不是 ToolPackage：它按当前会话附件和已授权 Skill资源自动注入统一 `read_file`，
并负责来源隔离、文本/Word/Excel分页、同一 Run 的字节/解析缓存和并发 single-flight。具备可信
user/App/Space上下文时，`export_file_to_download_center`始终直接披露，用于新建UTF-8文本文件或复用
当前会话已有平台文件并登记下载中心。图片不经过`read_file`，仅作为当前轮原生多模态附件送给支持
`image`的模型。

### `skills/`

| 文件 | 职责 |
| --- | --- |
| `catalog.py` | 合并代码内置与数据库上传 Skill 目录 |
| `package.py` | ZIP、Frontmatter、Manifest 与资源索引安全校验 |
| `runtime.py` | 平台 ZIP 下载、Hash 校验、当前目录物化和 Harness `Skills` 装配 |
| `resource_reader.py` · `tools.py` | Skill管理预览和模型侧资源目录；正文统一转 `read_file` |
| `script_tool.py` | Agent选择含脚本Skill和Sandbox时，组装已授权Skill脚本执行工具 |

### `api/v1/`

| 文件 | 路由 |
| --- | --- |
| `chat.py` | `POST /chat`, `POST /chat/stream` |
| `admin.py` | `GET /admin/access` 与统一企业管理员依赖；管理Router复用同一门禁 |
| `agents.py` | 管理员列表/增删改；`GET /agents/{id}`保持业务助手公开读取 |
| `mcp.py` | CRUD `/mcp`、统一检测 `/mcp/{name}/inspect` |
| `ui_tools.py` | CRUD `/ui-tools`；工具必须归属手工 UI 工具包 |
| `ui_tool_packages.py` | CRUD `/ui-tool-packages`；代码内置包只读 |
| `skills.py` | Skill 目录、安装、替换、资源和原包下载 |
| `sessions.py` | 会话列表、Conversation History、Event分页、上下文状态、删除与正式取消当前Run |
| `tools.py` | `GET /tools` |
| `deps.py` | `build_runner` / `build_runner_for_agent`，统一承接多入口的运行器组装 |

### `db/`

| 路径 | 职责 |
| --- | --- |
| `deps.py` | `get_*_store(ctx)` Depends |
| `conversation.py` | `SessionStore` 协议 + SQL/内存协调器（跨表写入、进程锁） |
| `stores/` | 表Store（含`event/`、`chat_session/`、`model_catalog/`、`skill_package/`、`ui_tool_package/`）+ `bundle.DataStores` |
| `factory.py` / `dialect.py` | `create_engine` / `close_db`、`bootstrap_database()`、方言解析 |
| `session_scope.py` / `session_list.py` | 会话作用域解析、列表过滤 DTO |

| 表 | 用途 |
| --- | --- |
| `agents` | DB Agent（UUID PK，name 唯一） |
| `llm_providers` | Provider类型、Base URL、凭据和协议配置；不保存独立启停状态 |
| `llm_models` | LLM 运行时唯一模型事实：Provider引用、模型启停、模态、上下文、最大输出与 tags |
| `model_catalog` | models.dev 的 Lab/Model/Provider 数据库快照，只用于浏览和预填 |
| `mcp_servers` | MCP transport、config JSON、最近一次 inspection 检测快照 |
| `skill_packages` | 上传 Skill 当前安装版本的元数据、平台 File ID、Manifest 与资源索引 |
| `ui_tool_packages` | 手工 UI 工具包；代码内置 `ui:builtin` 不写表 |
| `ui_tool_definitions` | 手工 UI 工具定义及所属包；builtin 由代码生成，不写表 |
| `chat_sessions` | 全局唯一会话、Agent/User归属、可空App/Space来源、标题与Sequence分配 |
| `chat_session_events` | Message、Title、Compaction、Tool、Plan和Context的唯一有序事实源 |

备份脚本的行数汇总与空表校验统一覆盖当前 Revision 实际存在的全部结构保留表（当前五张），
日志单独列出 `Structure only (no data copied)`；旧 Revision 不报告尚未创建的缓存表。
配置备份按源Alembic Revision生成结构，按统一表分类和逻辑依赖顺序复制配置。
`chat_sessions/chat_session_events`两张会话事实表、`channel_session_bindings/channel_message_runs`
两张Channel运行表及`wecom_user_profiles`缓存表只保留结构；`channel_accounts/wecom_applications`
作为配置复制。新增表必须同步维护分类和配置依赖顺序。上传Skill原ZIP属于
平台外部文件，不进入SQLite配置备份，完整留存需另外执行Skill“导出全部”。

无会话新环境可通过企业管理员接口`POST /api/v1/configuration/import`做同Revision初始化：默认
`apply=false`只预检，正式执行必须同时传`apply=true&confirm=true`。配置DB只使用请求级临时文件；
上传Skill原包进入目标平台File Store并回写新File ID。导入不得改写目标`alembic_version`，不得跨
Revision转换，并必须原样保留Agent、Provider、Model等业务主键。实现位于
`services/configuration_import.py`，契约见`docs/guides/configuration-import.md`。

## 扩展指南

### 内置工具包

`builtin:general` 只提供当前时间；`builtin:sandbox` 提供 Python 计算和条件式 Skill 脚本执行；
`builtin:ptc`仅在Agent显式关联时注入`run_code`，用于编排当前已授权MCP；旧
`builtin:code-mode`只作运行和配置导入兼容。
这些包都只在Agent的`capabilities`显式关联后进入Run。每个包严格包装一个 Toolset。内置包由代码随版本发布，
不在数据库注册；配置中心只读展示，Agent 仍按整包选择。

### 系统工具目录

`GET /api/v1/system-tools` 返回由代码维护的只读运行时目录，按任务规划、Skill、文件、工具结果、MCP Tool
Search和MCP Resource分组说明工具来源与自动注入条件。`read_tool_result`由Harness
`ToolOutputLimits`自动注入，用于回读当前会话已外置的大工具结果。该接口不读取数据库、不构造Agent Run；
`runtime:*` 仅是 Console 展示编码，不得注册到 ToolPackage Registry、`/tools` 或
`agents.capabilities`。

任务规划在Harness 0.26中只启用`write_plan`完整替换工具。成功Tool Result的metadata保存完整快照并
作为普通Event持久化；Deferred新Run沿用Turn并恢复最近成功快照，新用户Turn使用空Store。不得新增
Plan表、隐藏全局计划、Reminder用户消息或自研任务状态机。

### Skill

- Skill 不属于 ToolPackage，Agent 通过 `agents.skills` 独立选择。
- 代码内置只读包位于 `builtin_skills/<code>/SKILL.md`。
- 上传包由 `/api/v1/skills` 校验后保存到平台文件服务，`skill_packages` 只存 File ID 和索引。
- 当前 Pod 只保留 `runtime-skills/<code>/` 一份上传 Skill 运行副本；启动、安装、替换和
  `POST /api/v1/skills/{code}/reload` 负责同步，普通 Run 不访问平台文件服务。Hash 只用于完整性
  和过期判断，不代表产品版本，也不保留历史目录。
- 安装更新与普通运行、reload、资源读取通过配置屏障及 Skill 行锁协调；必须锁后重读最新记录，禁止旧请求在覆盖完成后重新发布旧 ZIP。启动 reconcile 持独占屏障，文件线程取消时等待结束后才释放锁。隔离实例用 `DEEP_AGENT_SKILL_RUNTIME_ROOT` 指定独立目录。
- 同 Hash包替换必须先验证平台原 ZIP：可下载时返回 `noop`；原 File ID失效时重新上传原包、
  原子更新记录并返回 `repaired`。不得只用请求字节恢复当前 Pod而让后续重启再次失败。
- Harness 0.26 `Skills` 负责 `load_capability` 渐进披露；DeepAgent通过 `list_skill_resources`
  提供授权目录，通过系统 `read_file`读取正文。
- 文本、DOCX 和 XLSX 根据索引中的路径、MIME 与后缀按行、逻辑块或物理行分页；图片和其他
  二进制只展示元数据，不经文件工具读取。历史安装包不需要为XLSX重新生成索引。
- `scripts/`、`tools/` 可以随包保存并列出路径，但不能通过资源工具读取。Agent选择该Skill并关联
  `builtin:sandbox`时，`execute_skill_script`与 `execute_python`共用当前Python沙箱Backend。
- 单个上传 Skill 文件缺失或同步失败时，本轮跳过该 Skill 并继续运行；不得让它阻塞其他 Skill、
  MCP 或普通回答。诊断进入 `/skills` 的派生状态与 `final.context.skills`，不写数据库状态字段。
- 完整边界见 [`../../docs/design/12-agent-skills/skill-system-design.md`](../../docs/design/12-agent-skills/skill-system-design.md)。

### MCP Server

开发内置 server → 见 [`../../mcp_servers/AGENTS.md`](../../mcp_servers/AGENTS.md)。

生产配置 → `POST /api/v1/mcp` 或 `/console/#/configuration`。用户新建只支持 URL MCP；Builtin 由仓库内 `mcp_servers/` 和程序配置管理，可查看、更新允许运行时调整的配置，但不能通过 Console 或删除 API 移除。历史 stdio 行只允许读取或删除，不能新建、修改或运行，需迁移为 URL MCP。

### DB Agent

`POST /api/v1/agents` 或 Alembic 种子；流式请求带 `agent_id`（UUID）。

### UI 工具

代码内置 `builtin__confirm`、`builtin__choice` 同属只读包 `ui:builtin`。手工包保存于
`ui_tool_packages`，工具通过 `package_code` 归属；Agent 的 `capabilities` 只保存包编码，
选中后包内所有 Schema 有效的工具进入同一个 ExternalToolset。内置包和工具不能修改
或删除；手工包包含工具或被 Agent 引用时也不能删除。

`builtin__choice` 始终支持候选外自由输入，结果固定为 `{selected: string[], custom_input: string | null}`；单选、多选和自由输入共用这一结构。

### 文件系统工具

`read_file`是按来源条件自动注入的系统工具，不属于 Agent能力配置。附件使用
`resource={type: attachment, file_id}`，Skill使用
`resource={type: skill, skill_code, path}`。工具首次通常只传来源，随后根据
`has_more/next_offset`渐进读取。合法附件ID范围必须从当前消息和当前会话完整
原始附件Event建立，不能从摘要Evidence建立，也不能仅依赖平台下载权限。
Skill路径必须属于当前 Agent已选择且运行目录就绪的资源索引。字节和解析结果只缓存到
本次用户消息对应的完整 Run终态，不跨Run、进程或数据库保存。

文件分页文本默认/最大1000/4000行，Office为200/1000项，正文上限仍50,000字符。
`has_more`时必须同时将`next_offset/next_char_offset`作为`offset/char_offset`续读；后者是项内
Unicode字符位置，部分项不向后跳号、不加省略标记。不能用returned_count推导游标。

图片、音频和视频不进入 `read_file`。当前轮媒体附件直接作为原生多模态内容交给Provider；
`llm_models.input_modalities`用于目录和管理员说明，不在DeepAgent本地建立第二层拒绝判断，所选模型
不接受时由Provider正常返回错误。历史媒体和Skill媒体不读取。PDF和PPT/PPTX若未来需要读取，
单独建设文档解析工具，不并入普通文件工具。新上传不抽文本，
历史 `extracted_text` 仅为回放兼容。完整设计见
[`../../docs/design/06-attachment-parsing/`](../../docs/design/06-attachment-parsing)。

`export_file_to_download_center`与读取工具方向相反：`source=content`把完整UTF-8文本上传为平台DL文件后
登记下载记录；`source=platform_file`只接受当前会话附件或大工具Artifact的File ID，并复用原文件。
登记失败时只补偿删除本次新上传的文件，不删除已有来源文件。它不是Output Tool，不改Final/SSE结构，
成功后模型只需在最终正文说明文件名并提示用户前往下载中心。

### 大工具结果

正文大小治理前不得使用审计摘要替换正式结果：PTC最终返回使用完整递归脱敏投影，
仅nested audit保留4K字符串/32项/4层和32,768字符总预览限制。Web Fetch取消50K字符提前截断，
保留5MiB下载安全上限，完整正文交给以下Token治理与回读，不创建第二套网页Store。
XLSX单元格不按4K字符截断；全工作簿解析正文最多8,000,000字符，超出明确失败，
防止sharedStrings重复展开；文件metadata仅为8K字符内的sheet/header预览，完整首行仍按游标读取。

普通Function、MCP、Sandbox和MCP Resource工具的返回在进入模型历史前经过Harness
`ToolOutputLimits`。阈值为模型配置窗口减去最大输出后的20%，并限制在4K～48K Token；没有有效窗口时使用
24K。这不是当前会话剩余空间；长文档仍应分页，压缩时的工具裁剪阈值不随之放大。达到阈值时，完整UTF-8结果上传为平台 `DL` 文件，历史只保存受限预览和
`ToolReturnPart.metadata.overflow_handle`。`read_file`、`read_tool_result`、Skill加载和Tool Search
不再次进入该治理链路，避免递归外置。
结构化返回使用Harness原生`json_lines` Serializer：顶层Sequence在Spill Artifact中每项一行，
用于`read_tool_result`按记录分页和关键词筛选；顶层Mapping使用缩进JSON，不猜测业务字段。
未达到Spill阈值的结果继续保持原对象。

回读通过DeepAgent的公开`get_toolset()`覆盖同名`read_tool_result`，不patch安装包。
默认/最大400/2000行，正文50,000字符；原handle授权、字面pattern和from_end保留。
结构化结果的`next_read`给出完整续读参数（0-based行offset、char_offset、end_offset），
尾窗截断先正向续读该窗口，随后用首次返回的`previous_read`选择更早窗口。文件压缩去重键
必须包含char_offset，工具结果还要包含end_offset，不能把不同片段误当重复读取。

平台File ID就是当前会话的`artifact_id`，不增加Artifact表。新Run只从当前会话完整原始Tool Result
Event索引允许的句柄；摘要Evidence不能作为授权事实源。同一Run回读复用`AgentDeps.file_bytes`并使用
single-flight。Spill失败时Event保存完整`inline_fallback`并标记失败，模型只接收受限投影；取消原样传播。

### Python Sandbox

结果传输上限仍512KiB，DataFrame/Series/ndarray保留前10行或项预览，但必须通过
`is_preview/returned_count/total_count`和`truncated.result`明确告知不完整。字节超限返回
`reason=byte_limit`的结构化预览，不再把repr伪装为完整结果。完整数据须由同次执行显式写入
`publish_path`，沿用成功后FileStore上传与会话文件授权；不自动重跑脚本、不自动保存大中间数据。

`execute_python` 属于 `builtin:sandbox`，与时间工具分开整包选择；新Agent不会自动关联。v0.3.22 的
`execute_skill_script` 复用同一 Backend，但只接受已索引的 UTF-8 `.py`、JSON inputs、显式参数和
最多16个同Skill授权资源；正式结果只从`outputs/result.json`读取。v0.6.7起`execute_python`
可挂入当前会话授权File ID，并通过沙箱预置`deepagent_files.input_path()`读取临时路径；
`publish_path()`登记的零到多个产物只在成功执行后上传FileStore，模型与Event只获得文件引用。
两者都通过当前`SubprocessBackend`启动全新的`.venv-sandbox`Python子进程；结束后销毁进程和
临时目录，不跨调用保留状态。脚本工具的额外权限来自Agent选择的Skill。

沙箱中文绘图使用随源码发布的完整 Noto Sans CJK SC 2.004 字体及 OFL 许可证。
`plotting.py` 仅在首次导入 Matplotlib 时注册并设置 Agg/默认中文 sans-serif，
普通 Python 调用不加载绘图库，脚本可自行覆盖字体。不能用宿主已有中文字体冒充部署可用；
回归用真实独立沙箱检查字形、PNG 和中文文件名，部署需保留 `sandbox/fonts/`。

沙箱预装常用网络、网页、图片、图表、PDF和Office库，默认允许公网访问；Worker连接层尽力阻断
回环、私网、链路本地与云元数据地址，重定向连接也重新校验。Landlock、资源限制、环境变量白名单
和无平台凭据边界仍保留；`security_level=none`必须如实说明这不是面向恶意代码的强隔离沙箱。
它不是MCP，也不等同于Provider托管的 `CodeExecutionTool`。完整边界见
[`../../docs/design/07-python-sandbox/`](../../docs/design/07-python-sandbox)。

---

## 配置

`.env` 在仓库根，经 `options.py` → `deepfos_platform.conf.option` 加载。

### 本地 Test 调试入口

生产环境继续启动 `deep_agent.main:app`，只使用平台网关注入的身份。需要从本地浏览器调试 Test 数据和服务时，使用独立入口：

```bash
PYTHONPATH=src python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

配置文件必须位于仓库外，且在 macOS/Linux 上执行 `chmod 600`。这个文件作为完整的本地运行 Profile，可同时包含 `DEEP_AGENT_DBMS_*`、`EXPORT_*` 和以下身份项：

```text
DEEP_AGENT_LOCAL_USER=<平台网关注入的用户 ID>
DEEP_AGENT_LOCAL_USERNAME=<可选；纯本地头像菜单模拟用户名>
DEEP_AGENT_LOCAL_TOKEN=<当前环境的有效 Token>
DEEP_AGENT_LOCAL_TOKEN_COOKIE=<可选，仅在目标环境要求 Cookie 时填写 Cookie 名>
```

本地入口只监听 `127.0.0.1`。它会覆盖浏览器提交的 `user / authorization / x-user-token / cookie`，保留控制台提交的调试 `app`。后端根据标准平台编码规则从 App 去掉末尾三位得到 Space，并在未显式提供语言时使用 `zh-cn`。身份和 Token 不得进入源码、仓库、浏览器存储、日志或测试夹具。

纯本地 SQLite、不使用平台 App 时，仓库外 Profile 可设置
`DEEP_AGENT_LOCAL_ADMIN_BYPASS=true`。此模式仍要求 `DEEP_AGENT_LOCAL_USER`，允许省略平台 Token，
并只通过 `local_dev` 写入的内部 ASGI scope 标记放行 Console 管理接口；生产入口和 HTTP Header
不能触发。Profile 是本地运行事实源，文件值必须覆盖启动 Shell 中同名的环境变量。

注意：应用启动会自动执行 Alembic `upgrade head`。本地 Profile 如果连接共享 Test 数据库，新增迁移会随本地服务启动直接应用；涉及删列等不兼容 DDL 时，必须先协调 Test 服务维护窗口，不能直接启动新代码。

### Console企业管理员鉴权

`security/console_admin.py`集中处理Console和管理API权限。当前只接受平台
`checkEnterpriseAdmin`返回`true`的企业管理员，内部角色为`enterprise_admin`；固定账号`admin`
必须明确拒绝。鉴权结果按用户和凭证摘要缓存30秒，原始Token/Cookie不得进入缓存键明文、日志或响应。
平台401映射为未认证，超时、网络或异常响应映射为503并失败关闭。

管理Router统一使用`CONSOLE_ADMIN_DEPENDENCIES`，不要在业务函数里复制账号或角色判断。新增管理接口
必须加入路由审计测试；Chat、Session、History、附件和固定Agent读取等运行接口不能误加管理员门禁。
完整边界见[`../../docs/guides/console-admin-access.md`](../../docs/guides/console-admin-access.md)。

### Agent（`DEEP_AGENT_` 前缀）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODEL` | `openai:gpt-4o` | `<provider>:<model>` |
| `MODEL_BASE_URL` / `MODEL_API_KEY` | — | OpenAI 兼容 API |
| `SYSTEM_PROMPT` | 内置 | 系统指令 |
| `MAX_REQUESTS` | `30` | 所有 Agent 共用的单轮模型请求上限 |
| `MAX_TOTAL_TOKENS` | 无限制 | Token 上限 |
| `SKILLS_DIR` | `skills` | 相对仓库根 |
| `MCP_FORWARD_REQUEST_HEADERS` | `authorization,x-tenant-id,x-user-token` | 转发到 MCP |
| `MCP_STDIO_SESSION_SCOPE` | `true` | stdio MCP 按 session 保活 |

### 数据库（`DEEP_AGENT_DBMS_` 前缀）

| 变量 | 说明 |
| --- | --- |
| `NAME` | `postgres` \| `mysql`（kingbase/dameng 需额外驱动） |
| `HOSTNAME` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | 连接信息 |
| 全部未设置 | 文件 SQLite，默认 `data/deep-agent.db`；可用 `DEEP_AGENT_SQLITE_PATH` 覆盖 |

Provider Key（`OPENAI_API_KEY` 等）由 Pydantic AI 直接读取。

### 迁移

```bash
# 仓库根
alembic upgrade head
```

迁移：v0.6.6 将旧结构折叠为 `migrations/versions/001_initial_schema.py` 生产基线；v0.7.0在其上增加
`002_wecom_channel_gateway.py`；v0.9.7新增`003_application_references.py`去除10条外键并由数据层维护引用。旧 `001` … `012` 历史迁移不再兼容。已有Revision 012数据库必须先用
`maintenance.rebaseline_database`校验最终物理结构和全表内容哈希，再执行`stamp --purge 001`，随后
由0.7.0升级到`002`；禁止仅手工修改版本号。涉及共享数据库时必须按维护窗口发布，并确保改标后不再
启动旧镜像。本地 Postgres：`docker compose --project-directory "$PWD" -f deploy/docker-compose.yml up -d`。

---

## API 速查

| 方法 | 路径 | 需 DB / 权限 |
| --- | --- | --- |
| GET | `/health`, `/info`, `/docs` | 否 |
| GET | `/api/v1/admin/access` | 否；当前平台身份，企业管理员返回`allowed=true` |
| POST | `/api/v1/chat`, `/api/v1/chat/stream` | 否（持久化需 DB） |
| GET | `/api/v1/agents/{agent_id}` | 是；业务助手公开读取固定Agent |
| CRUD | `/api/v1/agents` | 是；企业管理员，单个GET除外 |
| CRUD | `/api/v1/mcp` | 是；企业管理员 |
| CRUD | `/api/v1/ui-tools` | 是；企业管理员 |
| GET | `/api/v1/tools` | 企业管理员；无 DB 时跳过动态 UI 工具 |
| GET | `/api/v1/global-settings` | 企业管理员；返回安全白名单，只读 |
| GET/PUT | `/api/v1/global-settings/web-search` | 企业管理员；读取或保存唯一Web Search配置，密钥只返回掩码 |
| POST | `/api/v1/global-settings/web-search/test` | 企业管理员；测试未保存固定Provider草稿，不返回正文 |
| GET/POST | `/api/v1/llm-models/catalog`、`/api/v1/llm-models/catalog/refresh` | 是；企业管理员查询或刷新 |
| GET/POST | `/api/v1/context-test-scenarios` | 是；企业管理员按当前模型预算创建测试会话 |
| GET/POST | `/api/v1/uploads/capabilities`、`/api/v1/uploads` | 否/否；后端仍执行用户与文件限制校验 |
| GET | `/api/v1/request-context` | 否；返回当前请求上下文最终值 |
| GET | `/api/v1/current-user` | 否；返回当前用户ID与账号中心用户名，本地开发同契约模拟 |
| GET | `/api/v1/sessions/{id}/context-status` | 是；沿用会话所有权校验 |
| | `/api/v1/sessions/*` | 是 |
| | `/console/#/configuration` | 是 |

SSE 协议：[`../../docs/guides/sse-protocol.md`](../../docs/guides/sse-protocol.md)。前端对接：[`../../console/AGENTS.md`](../../console/AGENTS.md)。

---

## 约定与陷阱

0. **重构风格**：事件多态映射集中在 `agent/events.py`；多入口共享组装集中在 `api/v1/deps.py`，变化量留在调用入口
   - 事件多态映射 → `agent/events.py`（`singledispatch` 分层）
   - 多入口共享组装 → `api/v1/deps.py`（变化量留在入口，上下文在 `_build_*` 内统一解包）
1. 包名 **`deep_agent`**，启动 `deep_agent.main:app`（非 `app.main`）
2. 扩展优先走路径注册，避免改 `runner.py` 核心
3. 工具包命名：`builtin:general` \| `builtin:sandbox` \| `builtin:ptc` \| `builtin:web` \| `mcp:<name>` \| `ui:<package>`；旧`builtin:code-mode`仅兼容读取/导入。一个包严格包装一个Toolset，Agent按包选择，工具协议名不随包编码变化。Skill使用独立`agents.skills`，`runtime:*`系统工具不得塞回ToolPackage
4. 会话`session_id`全局唯一，新会话必须有明确`agent_id + user_id`；App/Space可空且不参与当前授权
5. 同Session进程内用`SessionStore.lock()`串行；Sequence由Session行和`next_sequence`在短事务分配
6. 配置保存先完成 Schema/引用校验，再进入写事务；一次 API 业务变更不要串联多个各自提交的 Store `_write()`。MCP 检测等外部 I/O 先基于候选配置执行，再把配置与检测快照一次性提交。模型目录联网、解析和完整校验必须在事务前完成，随后单事务全量替换；模型配置保存不得隐式联网。
7. `model_catalog` 不是运行时事实源，目录刷新不得修改已有 `llm_models`。模型名称和 Base URL 不再用于猜测模态；上下文缺失时当前自动压缩保持关闭。
8. 配置中心必填文本会去除首尾空白并拒绝空值，显式把必填更新字段传为 `null` 返回 422；重复名称返回 409。删除仍被 Agent 引用的 MCP、UI 工具包或包内工具返回 409，并在 `detail.referenced_by` 列出引用方。
9. 请求上下文以 App 为主键：标准 App 必须以三位数字结尾；Space 自动取 `app[:-3]`。App 与显式 Space 不一致时返回 422，Language 缺失时默认 `zh-cn`。
10. 上下文压缩只在八段Summary、Evidence、来源Hash和目标预算全部通过时把同一Compaction Event更新为
    Completed；失败终态保留审计但不改变模型Surface。
11. 自动压缩持久化必须验证“单个摘要锚点 + 完整保留尾部”；不能仅凭部分公共后缀推断覆盖边界。仅允许归一化 ModelRequest 顶层运行 ID、时间戳和原本缺失的 instructions；parts、工具调用 ID、内容及已有指令必须严格匹配。失败保留旧 checkpoint 与原始事件。
12. 附件读取授权从当前上传与原始会话历史恢复，不能从压缩后的模型历史建立；摘要提及的文件 ID 不构成授权。
13. Console压缩测试会话是管理员功能；后端按当前模型实时预算构造可往返的普通Event，不得直接伪造
    Completed Compaction或新增测试专用表。

| 现象 | 原因 |
| --- | --- |
| MCP 工具为空 | Agent 未选择对应 `mcp:<name>`，或URL MCP缺少有效工具快照 |
| 历史不在预期数据库 | 未配置 DBMS 时实际使用默认文件 SQLite；检查 `DEEP_AGENT_SQLITE_PATH` |

### 003 写入边界

新增引用必须在事务内锁定父记录，删除方持相同父记录锁后检查子引用；变更父ID时先锁旧/新父记录，再锁子记录并核对快照。公共入口为 `db/reference_integrity.py`。ORM Agent→Model 和 UI Tool→Package 使用显式 join，不能重新增加 ForeignKey。
配置替换用 Revision 行独占锁协调所有正常写入的共享锁；数据库异常不可笼统当作重复消息。数据库逻辑引用审计见 `db/reference_schema.py`，部署与验收见 `docs/guides/application-references.md`。

### MCP 工具发现范围

MCP 工具合计不超过 10 个时全部直接披露，超过 10 个时预选最多 3 个，其余通过 `search_tools`（显示名“搜索工具”）按需搜索，每次最多返回 5 个。已知准确协议名时用最多 3 个查询项批量加载；未知名称才按能力描述搜索，不混填名称与中英文描述，不要求凑满查询项。预选、搜索及异常回退不按用户提问、上下文数据或工具名推断读写权限，也不因关键词排除写入/删除工具；候选范围始终受 Agent 显式关联的 MCP 限定。保留相关性排序、已披露工具去重和重复查询去重。
