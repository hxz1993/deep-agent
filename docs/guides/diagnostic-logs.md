# 诊断日志与控制台查询

诊断中心提供当前部署实例的 API、企微网关结构化日志，只对企业管理员开放。
它与会话 Event Store、普通 History 和原有运维日志独立，不生成会话事件，不执行工具或自动重试。
管理员可以跨用户按 Session ID 排查；普通会话接口仍按原有所有者权限校验。

标题生成使用 `title_generation_started/completed/failed/cancelled`，关联 Session、Run、Agent 和模型。
失败中的 `reason=output_token_limit/timeout/empty_title/model_error` 区分预算不足、超时、空标题与其他模型错误；
`phase=image_download/title_update` 定位图片读取或标题更新失败。成功日志仅说明标题已生成，人工改名保护可能阻止覆盖。
日志不含输入、标题正文或异常原文。渠道的来源固定标题不调用模型，因此没有标题生成日志。

历史恢复补齐使用 `history_tool_result_matched` 和 `history_tool_result_synthesized`：

- `matched / reason=result_after_user_message`：调用与结果之间存在独立用户消息，已在同一助手消息段内匹配后续结果，不再补重复取消结果。
- `synthesized / reason=missing_result_cancelled` 或 `missing_result_failed`：调用确实缺少结果，分别补取消或异常结束结果。
- `phase=history_prepare/terminal_persistence` 区分模型历史准备与终态持久化；可按 Session、Run、工具名和 `tool_call_id` 关联。只记录固定原因与标识，不保存正文。匹配日志描述本次历史读取，不代表工具又执行了一次。

## 使用

进入控制台「诊断 → 日志」（`/console/#/diagnostics/logs`），选择时间、来源、级别或关键词，
右上角工具栏第一行放时间、来源、等级、会话 ID；第二行放日志内容搜索、查询和文字重置，两行等宽且左右对齐。
不设高级展开或 Run ID 输入；选择自定义时间后，在筛选区下方显示起止时间。小于760px时第一行按两列排布，不挤出页面。
“查询”同时承担刷新功能：相对范围重新取当前时间，自定义范围保持不变；不另设刷新按钮。“重置”为无边框文字按钮。
筛选控件使用 placeholder 和无障碍名称，不在上方再堆字段标题。旧 `#/diagnostics` 地址仍进入日志页。
页面用紧凑内容行展示时间、级别胶囊、来源胶囊、摘要，行末仅保留 Session ID；没有表头网格、状态圆点和逐行分割线。
点击内容行在独立右栏查看完整摘要及安全字段，左标签、右值，标签列固定宽度；优先展示会话及执行结果，
服务信息、实例、进程等字段也直接平铺，不折叠；缺省字段不占空行。仅会话 ID 提供复制。
日志与会话查询页沿用 Console 的字体及标题层级：页面标题16px、分组14px、正文12–13px；
日志详情标签12px、值12–13px，字段紧凑左右排布；长值在右列换行，不挤乱标签。
不重复展示「规范字段」或 JSON 区，不渲染 Markdown；关键词高亮先转义文本。

Session ID 旁可复制或打开关联会话。Run ID 只保留原值展示，没有复制、筛选、跳转和轮次联动；旧日志URL中的 Run ID 被忽略，
日志跳会话仅携带完整 Session ID。会话详情不增加跳日志入口或轮次日志栏，严格复用对话测试；
从日志进入会话后，返回恢复查询与选中位置。
会话概览、列表、详情及完整联动表见[诊断会话指南](diagnostic-sessions.md)。

只在查询、重置、加载更多时请求后端；没有下载、后台轮询或实时订阅。改条件、重新查询或离开页面会中止旧读取，
忽略迟到结果；新结果不再包含旧选中记录时关闭其详情。页面最多保留 5,000 条，达到上限需缩小范围。

Agent 可复用管理员身份调用同一只读接口：

- `GET /api/v1/diagnostics/logs/status`
- `GET /api/v1/diagnostics/logs?session_id=...&run_id=...`

查询参数为 `start` / `end`（UTC ISO 时间）、`source`（`api` / `gateway`）、`level`、`session_id`、`run_id`、普通文本 `q`、`limit`、`cursor`。
默认最近 24 小时、每页 200 条，最多 500 条。结果按时间倒序，含 `items`、`next_cursor`、`partial`、`scanned_bytes`、`range`、`instance_id`。
`partial=true` 不能解读为完整结果；有游标时继续查询，无游标时缩小来源或检查目录状态。
页面分别提示无匹配、未启用 / 目录不可用、写入丢弃 / 数据可能不完整、部分扫描、游标失效、无权限和网络错误，
不把不可用或扫描不完整伪装成空结果或完整总数。`range` 是本次查询 / 扫描范围，不是精确的历史保留边界；
状态接口提供配置的 `retention_days`，不保证这些天内每时段均有日志。目标范围早于保留期时仍保留原条件，
无结果可调整条件；历史会话存在而日志已过期不视为会话异常。
游标固定原查询范围和文件快照，刷新会看到新增日志。前端只传短随机标识，快照在 API 进程内暂存，最多 128 条 / 16 MiB，每个游标创建后 15 分钟过期；不写入数据库或文件。
游标到期、容量淘汰、轮转清理、文件替换或 API 重启使旧游标失效时返回 409，重新查询即可。
关键词不会执行正则、Shell 或 SQL。接口不接受文件路径，也不返回原始文件、堆栈或第三方输出。
两个日志接口均保持原有响应形状和后端管理员校验；删除页面 JSON 区不改变 Agent 的结构化只读查询能力。

## 文件与配置

无需修改 Jenkins 或联合启动命令。配置优先级：

1. `DEEP_AGENT_DIAGNOSTICS_LOG_DIR`：专用目录。
2. 未指定时，从 `DEEP_AGENT_LOG_FILE_PATH` 的父目录派生 `diagnostics/`。
3. 均未设置时使用项目 `data/logs/diagnostics/`。

`DEEP_AGENT_DIAGNOSTICS_ENABLED=false` 可关闭专用诊断文件。默认启用；安全结构化事实仍通过后台队列输出到原运维日志，两个出口的失败互不阻断。
路径来自可信部署配置，不来自查询参数；允许操作系统父路径别名（如 macOS `/var`），诊断目录本身和文件禁止符号链接，文件禁止硬链接或非普通文件。

文件名含主机实例摘要、服务、PID、启动标识和分片编号；每个进程独立写入。
默认单文件 20 MiB、保留 7 天、诊断数据文件总预算 512 MiB；每个进程队列最多 2,048 条。
清理优先删除关闭的过期/旧分片，不删除其他进程持锁写入的文件。元数据状态文件随旧启动记录清理，不压缩归档。
使用 Linux/macOS 文件锁，不支持缺少该锁能力的平台。多副本/多主机不聚合日志；当前实例外的数据不返回。
若要跨重启或 Pod 重建保留文件，需要运维持久卷；首版不会跨实例检索。

业务协程只提交有界安全字段，磁盘操作在后台线程完成。磁盘不可写、队列满、预算不足时丢弃诊断记录并限频告警，状态接口提示日志可能不完整。
可读的旧记录仍可查询，但不能据此认为当前写入正常。强制退出可能丢失队列中尚未写入的日志；诊断不是审计账本或消息可靠投递系统。
单次查询最多扫描 32 MiB / 2 秒，最多两个并发查询、4,096 个文件；元数据超预算同样明确报告不完整。

## 首批记录与安全边界

- 服务启动、停止、初始化失败；Agent 执行开始、结束、取消、失败或正式等待人工操作。
- 模型与工具请求开始、完成、失败、已有参数纠错/重试；记录调用标识、阶段和耗时。
- 模型/工具等待超过 30 秒，记录「仍在等待」，之后最多每 60 秒一次；这不是卡死判定，不改变超时、取消或重试策略。
- 渠道接收、排队、分发、Agent 关联、进度投递失败和最终投递结果。
- 已有压缩、结果转存与持久化失败事实。

`channel_run_id` 是渠道处理标识；`run_id` 是 Agent 执行标识；`channel_agent_linked` 记录已有映射，不把两者混用。
日志不包含正文、思考、工具参数/结果、凭据、认证 URL、异常原文或局部变量。摘要由固定事件注册表生成，错误只保存类型/分类。
进程或事件循环彻底失去调度时，等待日志本身也可能不再更新；首版不增加外部 watchdog，不能仅凭没有新日志判断已卡死。

## 后续增加日志点

### 内置工具文件处理与原生错误

文件相关工具增加 `builtin_operation_started/completed/failed/cancelled`，用 `phase` 区分
`download`、`upload`、`register_download`、`rollback_upload`，文件阅读器另有 `file_read/file_parse`，
程序编排另有文件与公网下载阶段。结合 `session_id/run_id/tool_call_id/tool`、耗时、字节数和
`error_type/error_code/http_status` 定位失败步骤；通用 `agent_tool_failed` 也追踪显式原生异常链。

格式限制随环境的平台配置生效，不能由会话附件支持类型推断。上传失败与下载中心登记失败是不同
阶段；登记失败仅清理本次新上传的文件，已有来源文件不删除。若清理也失败，保留两项失败，不能
把“尝试回滚”解读为已清理。取消仍沿原有取消路径传播，不因日志重试业务操作。

具体原生提示、业务码及 HTTP 状态经脱敏后进入工具返回，模型与会话 History/轨迹可读取；
日志不保存该提示正文。响应没有业务提示时，只保留有证据的类型/状态，不猜测“格式不允许”。
旧轨迹中已被抹去的原生原因无法由新版补出，新日志在部署新版后的执行中生效。

验证入口：`test_tool_errors.py`、`test_platform_api_errors.py`、`test_builtin_native_errors.py`、
`test_file_export_tool.py`、`test_sandbox_toolset.py` 和 `integration/test_chat_file_export.py`。

### 停止与 SSE 断连诊断

按同一 Session / Run 关联三类事实：`agent_cancel_api_received`（鉴权后的停止接口请求）、
`agent_cancel_observed`（活动执行首次观察到的取消来源）、`agent_stream_closed`（发送连接结束）。
已知来源同时写入流式 `agent_run_cancelled.reason`；更外层才观察到的来源可能晚于执行终态日志，
此时应结合连接结束记录，不能只看终态的 `task_cancelled_unknown`。

`reason` 含义：

- `cancel_api` / `channel_cancel_api`：普通客户端 / 渠道控制接口请求取消；不等于用户亲自点击。
- `http_disconnect`：ASGI 实际收到发送连接的断连通知；无法单独区分浏览器、代理或网络原因。
- `transport_send_error`：ASGI 发送抛出 OSError，保留原异常传播，不记录异常原文。
- `server_shutdown`：注册表关闭所有 Run，或 SSE 库已有关闭标志表明服务正在退出。
- `task_cancelled_unknown`：没有更明确证据的任务取消，禁止解释为客户端断开。
- `stream_generator_closed`：生成器被关闭，尚无更明确来源。
- `stream_completed` / `stream_error`：连接正常收尾 / 异常退出，不表示模型一定成功；
  业务可能已经返回 `error`、`cancelled` 或等待人工操作，需结合执行终态。

重复停止请求仍分别记录接口入口，活动 Run 只保留首次来源，后续断连清理不覆盖它。
接口入口 `status=not_matched` 仅表示首次注册表查询未匹配，可能随后从数据库解析 Agent 后成功取消，
不是最终拒绝或不存在判定；是否实际命中需结合 `agent_cancel_observed`。
日志只含稳定原因码、已有 Session/Run/Agent 标识和连接耗时，不采集 Header、正文、IP 或凭据。
不新增会话事件或数据库字段，不改变停止、断连、超时、SSE 心跳及持久化语义。
旧日志无法补出过去的取消来源；部署新版后发生的执行才有这些事实。

### 模型 API 请求诊断

OpenAI 兼容模型（包含 DeepSeek 的 Chat Completions / Responses）额外记录
`model_api_request`、`model_api_response`，Console 详情的「模型 API」分组展示：

- API 来源地址与规范路径、HTTP 方法和状态码；地址不包含认证信息、查询参数或自定义前缀。
- 顶层函数工具名、总数、去重数、重复项、相对上一请求新增项及清单 SHA256。
- Responses 搜索结果历史里的工具名，以及它们与顶层工具清单的交集，辅助排查重复声明。
- 每次 HTTP 请求的本地关联 ID；响应耗时只到收到响应头，不等于流式生成完成时间。

名单经过格式校验和共享长度预算，`tools_omitted` 明确标记省略的名单条目数；
`tool_manifest_status` 非 `complete` 时不能根据缺少重复项判定没有重复。
其他模型协议仍保留原有模型开始/完成/失败日志，不宣称已采集 HTTP 清单。
不保存请求/响应正文、工具 Schema、参数、图片内容、API Key、Cookie 或 Headers。

DeepSeek Responses 的重名错误需要同时看 `duplicate_tool_names` 和 `redeclared_tool_names`：
顶层工具名没有重复，不代表搜索历史没有再次声明同一工具。2026-09-10 已在模型装配时将
DeepSeek 原生能力限制为其支持的 Web Search，工具搜索使用 SDK 普通函数回退，避免双处声明；
其他 Provider 不受影响。核验的 [Pydantic AI 2.42.0 DeepSeek Profile](https://github.com/pydantic/pydantic-ai/blob/v2.42.0/pydantic_ai_slim/pydantic_ai/providers/deepseek.py)
尚未包含此限制；不需要修改数据库、工具名或清除历史。

采集使用现有 HTTP 客户端的事件钩子，不更换客户端、重试或超时策略。
SDK 流式请求延迟到消费者任务执行，因此通过透明 `WrapperModel` 在实际
`request` / `request_stream` 调用边界建立隔离上下文，不能只在 Capability 外层设置 ContextVar。
请求与流对象原样委托，日志失败不能改变业务结果；不会写入会话 Event Store，也不涉及迁移。

统一入口是 `deep_agent.diagnostics.emit`；Agent 代码可沿用 `agent.observability.log_event`。
先在 `diagnostics/schema.py` 注册稳定事件名与固定中文摘要，再提交白名单中的标识、数值、阶段和错误分类；未知字段直接丢弃。
不要传 `str(exc)`、`repr(...)`、完整模型/工具对象或业务正文；不要在诊断代码中修改执行状态。
读取时再次按同一白名单验证，原始运维日志不能通过这个接口直接开放。新增事件无需改查询页面。

验证入口：`tests/unit/test_diagnostics_runtime.py`、`test_diagnostics_query.py`、`test_runtime_diagnostics.py`、`test_channel_diagnostics.py`、`test_diagnostic_runner_equivalence.py` 及 Console 的 `diagnostics*.test.mjs`。
等价测试使用真实 Runner 和隔离 SQLite，对比诊断文件开启、关闭、写盘失败三种情况下的模型请求、工具参数和调用次数、SSE 与历史；仅归一化时间及随机标识。
