# A2A 调研验证与开发验收矩阵

[总案](server-implementation-plan.md) · 2026-11-12。以下保留原调研的基线和夹具记录。最新实施验证见本页首节。

## 0.17.0a2 提交前复验

- 用 8002 的真实平台 Token 从通用地址发起 XLSX 任务 `ef6805b1`，用带应用的地址回查同一任务。各自按响应里的 URL 下载，得到相同字节，工作簿 C2 为 20。
- 官方 SDK 在两个有权应用上验证普通与流式调用，Inspector 通过带应用的 Card 完成流式调用。Langflow 使用现有回环适配器重测图片、同 Task 补材料、同 Context 追问与 XLSX 下载。CodeBuddy 原生工具对同一发布调用并查询到完成任务 `52765188`。
- Console 和 HTML 原型验证全部应用说明、范围切换、两条地址复制及 Header JSON。正式页面验证 1536px 双列与 480px 单列。8002 下载的开发 Skill ZIP 版本与运行进程一致，SQLite 016 完整性检查通过。
- 全量回归通过。Python 共 3,608 项，66 项跳过，零失败和错误。Console 共 1,182 项全部通过。在线 A²D Playground 的实测记录见下一节。

## 2026-11-14 双路由与应用范围复验

| 验收入口 | 数据与结果 |
| --- | --- |
| 官方 Python SDK 1.3.0 | 8002 使用真实平台 Token 和两个有权应用。普通任务 `903d3b05`、流式任务 `be098816` 均返回差额 20，GetTask、ListTasks 都能回查。任务 `db2717c3` 的订阅依次收到任务快照和取消终态。带 App 地址再通过官方 SDK 复跑六个方法，普通 `14fa2902`、流式 `aac60187`、订阅及取消 `5d321ac9`。 |
| 带 App 地址 · 直接 JSON-RPC | PNG 识别 `dec7026a`，原 Task 补交文本材料 `da77ee7a`，同一 Context 追问 `95bb0b45`。生成 XLSX 的任务 `875f545f` 从两个地址回查结果一致，下载返回相同的文件内容，C2 = 20。 |
| 官方 Inspector | 原生读取带 App 的 Card，只配置平台 Bearer Token。普通任务 `30357606`、流式任务 `50c104ba` 都返回差额 20。 |
| 在线 A²D Playground | 独立合成样例经临时 HTTPS 入口读取 Card，普通和流式请求都返回完成状态及差额 20，流式任务 `224aae72`。该客户端从 RPC 基址请求相对的 well-known 地址。验收后关闭了临时入口。 |
| Langflow | 原 Playground 通过回环适配器调用 8002 的带 App 地址。图片 `392ca7a5`、补交文本 `f2582bf2`、追问 `4c2eccae`、文件交付 `8a8485e7` 均成功，下载核对 C2 = 20。Playground 界面新聊天也通过，8002 对应任务 `0ca5a06f`。 |
| CodeBuddy Code 2.183.0 | 原生工具通过回环目录发现目标。任务 `4eff0a43` 等待补交图片后返回 Q9M4。原生读取 Card、发起及查询 `fc70b84f` 后取得 XLSX，下载核对 C2 = 20。 |
| 权限 | 8002 验证两张 Card 仅调用 URL 不同。路径中的 App 优先于冲突 Header，缺少凭据为 401，超出发布范围为 403。自动化另覆盖 API Key、撤权、跨 App 和跨用户查询与取件。 |
| Console 与数据 | 浏览器验证两条地址复制、全量／指定切换、指定清单回显、联调工具页签、分页、App 筛选、Task ID 复制、分隔线键盘调整、常用窗口宽度和深色主题。通过 Console 创建全部应用发布，确认指定清单为空时禁止保存，再从平台目录选择两个 App 保存、停用并验证删除确认流程。仅清理 8002 的旧发布配置并通过管理 API 重建两个样例。SQLite `016` 完整性通过，隔离环境验证了空库、`015 → 016` 及重复升级。 |

修正使用退休 `fixed` 样例的配置导入测试后，重新执行 `scripts/run_tests.py --suite all`。Python 共 3,607 项，66 项跳过，零失败和错误。Console 共 1,182 项全部通过。开发 Skill ZIP 由 8002 实际下载并校验 SHA-256。

## 2026-11-14 一期提交前复验

- 隔离 PostgreSQL 14 验证空库升级、数据库摘要、结构化输出及配置备份恢复。SQLite 另验证 015 → 016、重复启动和有数据时阻止降级。MySQL 实机用例仍需独立实例。
- 本地 8002 从独立 SQLite 启动 `0.17.0a1`，数据库为 `016`，完整性检查通过，重启后保留两条发布配置。8000 同时可用。
- 官方 Python SDK 1.3.0 使用平台凭据，普通 Task `1575be34` 和流式 Task `d3369133` 分别在两个有权应用返回差额 20，并核对 GetTask 和 ListTasks。Task `3f7a5365` 的订阅先收到快照，再收到取消终态。
- 直接 JSON-RPC 使用真实模型识别图片 `a57d0e76`，在 Task `a81ec1e3` 请求并接收文本附件，随后以同一 Context 在新 Task `661ee11b` 回答追问。Task `96650a54` 生成并下载 `result.xlsx`，工作表第 2 行为 100、120、20。
- Langflow 通过回环验收适配器调用 8002，覆盖图片识别 `9748de2f`、同一 Task 补材料 `ce67ec17`、同一 Context 追问 `79a26299`、XLSX 交付 `e53f3316`。下载后确认 C2 = 20。
- CodeBuddy Code 2.183.0 通过回环验收目录发现发布，依次调用原生 `A2AGetAgentCard`、`A2ASendMessage` 和 `A2AGetTask`。远端返回 20，8002 发布任务列表读回 Task `54825e17`。
- 浏览器对照 HTML 原型检查发布、协议与联调、任务列表和详情。使用真实发布接口验证新建、停用、编辑、取消删除和确认删除。以任务夹具验证分页、复制完整 ID、发布切换时清空旧任务、窄屏和深色布局。
- `scripts/run_tests.py --suite all` 通过。Python 执行 3,603 项，跳过 66 项，零失败和错误。Console 通过 1,182 项。重新构建的开发资料另外通过了 API 下载及摘要校验。

## 2026-11-13 一期本地验证

| 入口 | 数据与结果 |
| --- | --- |
| 官方 A2A Python SDK 1.3.0 | 8002、真实平台 Token、两个有权 App、真实模型。普通及流式完成预算 100、实际 120 的差异计算，返回 20。GetTask、ListTasks 可读回，SubscribeToTask 先发快照，CancelTask 实际停止运行，订阅收到取消终态。 |
| A²D Playground | 隔离样例实例，通过临时 HTTPS 入口读取 A2A 1.0 Card，普通与流式均返回预期结果。验收后关闭对外入口。 |
| 官方 Inspector | 隔离样例通过普通与流式调用。另在 8002 配置真实平台凭据和 App/Space，读到 1.0 Card，流式取得差额 20。 |
| 产物 | 隔离模型替身调用真实 Python 沙箱，FileStore 生成并通过 A2A HTTP 下载 4,895 字节的 XLSX，打开确认 C2 = 20。自动化另覆盖 MIME、文件名、撤权和换 App。8002 的真实模型取件对照在首个模型请求等待超时，已取消。 |
| 回归 | 全量 Python 3,596 项，66 跳过，1 项为 `test` 工作树同样复现的文档适配截断标签问题。前端 1,181 项全部通过。最后一轮 A2A、发布备份恢复和迁移专项 13 项通过。 |
| CodeBuddy Code 2.183.0 | 原生 `A2AGetAgentCard`、`A2ASendMessage`、`A2AGetTask` 分别通过隔离样例和本地 8002 验收。8002 使用已有本地 Profile 的平台 Token 和 App `gddzei019`，Task `64eca2b2f9d64e8db0c6e4feddee5c38` 从 submitted、working 到 completed，真实模型返回 20，服务端和 Console 均能读回同一 Task。App/Space 和凭据由仅在验收期间启动的回环适配器补齐。 |

运行手册见 [A2A 服务端指南](../../guides/agent-service.md)。发布配置持久化到 016，近期任务留在 API 进程内存。

## 已核验的基线

- DeepAgent `test`，HEAD `4462260e`，产品 0.16.6、最新迁移 015；工作区已有其他未提交改动，本轮不包含。依赖文件为 `requirements.txt`，固定 Pydantic AI Slim 2.46.0、Harness 0.32.0、Monty 1.5.0、FastAPI 0.111.x。实查当前本地 FastAPI 0.111.1、Starlette 0.37.2、Pydantic 2.13.4、sse-starlette 3.0.3。
- 使用 Context7 查 FastA2A 和 A2A 规范，并回到 [官方规范](https://a2a-protocol.org/latest/specification/)、[FastA2A 发行物](https://pypi.org/project/fasta2a/2.1.1/)和[官方 Python SDK](https://github.com/a2aproject/a2a-python)核对。Dot 交接原文的 FastA2A v2.0.0 结论保留为历史，不直接套到 2.1.1。
- AI Labs 本地对照 Agno `cbc10df`、ADK `d68ca85`、Hermes `345cd2b0` 的 task 查找、存储选项及归属；本轮为源码核查，未宣称这几个项目完成互通。需要的运行验证由下方独立的 FastA2A 固定 Worker 完成，不启用真实模型、企业账号或平台文件。
- 现有 DeepAgent 定向：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/integration/test_chat_stream_cancel_and_history.py -k 'deferred or cancel_endpoint_uses_official'`，**7 通过、11 未选**。覆盖 pending 落库和续跑、旧回执、新消息替代、并行确认、恢复后取消。普通 Chat 回归不能证明新 A2A 已可用。

## FastA2A 2.1.1 隔离实验

临时 venv：Python 3.12.13，`fasta2a==2.1.1`、`a2a-sdk[http-server]==1.3.0`、`httpx==0.28.1`，并对齐 FastAPI 0.111.1、Starlette 0.37.2、Pydantic 2.13.4、sse-starlette 3.0.3。先在父 FastAPI 挂载 FastA2A 子应用并显式接管生命周期，用确定性 Worker 通过 ASGITransport 验报文，再在随机本地回环端口启动 Uvicorn 验真实 SSE 断连和取消。没有改线上配置或项目依赖。FastA2A 公布 wheel SHA-256 `98931d242b80bd3c15539a4b146d3712794d0472a4cfc944441ccf4d2b3759d0`；从 PyPI 下载核对 wheel SHA-256，并确认 5 个已安装源模块逐字节一致。版本适配源文件 SHA-256 `706d654f9d6d70457bfde4fb56f8bb28802a5f2c06ebc5ea4dd0206119f882a3`。

| 验证项 | 观测 | 对实施的影响 |
| --- | --- | --- |
| Card 和版本 | 官方 SDK 能解析 Card，`supportedInterfaces` 的 JSONRPC 版本为 1.0；六种 1.0 名称和大写枚举有适配；旧 `tasks/get` 返回小写状态 | 更新之前“只有旧方法”的结论。1.0 包装仍不足以保证实际语义。 |
| 非流式等待 | 默认 `SendMessage` 立即返回 SUBMITTED；随后 `GetTask` 才看到 INPUT_REQUIRED | 执行项目自己的 `returnImmediately` 规则。 |
| 同 Task 续跑和重试 | 带原 taskId/contextId 的补答仍生成新 Task；同 messageId 重试也生成新 Task | 必须由项目 Task 服务保证 ID、上下文匹配、幂等和补答。 |
| 严格解码 | 官方 SDK 发真正 `GetTask` RPC，拒绝默认存储返回的无时区 timestamp；只将夹具时间换为带 Z，官方 SDK 能解析暂停任务 | 统一 UTC RFC 3339，标准字段用官方类型校验。 |
| 查询、订阅与权限 | `ListTasks` 返回 -32004；终态订阅发快照而非标准错误；带不匹配 tenant 的原样调用仍返回任务（没有业务鉴权的夹具） | 由项目层实现列表、终态错误与作用域鉴权。不能启用默认“透传即可”的入口。 |
| SSE 与断连 | 真回环 HTTP：收到首个 Task 后关闭客户端流，随后查状态仍 WORKING | 证明此 FastA2A 夹具解耦了观察；DeepAgent 仍须接独立 Worker 实现同样语义。 |
| 真取消与并发 | 固定 450 ms 工作用同一个默认 Worker 消费；Cancel RPC 等约 453 ms，实际工作先完成，夹具取消回调再把状态改成 canceled | 默认 Worker 串行消费不是可靠的在途取消方案。需单独的取消信号及终态比较更新；夹具取消回调只是验证派发顺序，不代表所有自定义 Worker 会覆盖终态。 |

复现要点：创建临时 Python 3.12 环境安装上表版本。用 FastA2A 的默认 `InMemoryStorage` / `InMemoryBroker`，挂载一个继承 `Worker` 的无模型实现；`run_task` 等 30 ms 后按输入写 `input-required` 或 `completed`，`cancel_task` 写 `canceled`。分别用原始 JSON-RPC 和官方 `JsonRpcTransport.get_task` 发上述调用；固定将夹具的时间格式改为 UTC 再比一次。真实断连用临时回环 HTTP 与 450 ms Worker；读第一帧即关闭，立刻查状态及发取消，记录实际完成标记和回执。实验完退出生命周期、关闭回环服务并清理临时 venv。实施时把语义验证改为正式测试，不把原有包的缺口写成预期业务行为。

方案文档中的 9 组 A2A 载荷（Card、发起、暂停、补答、查询、列表、订阅、取消、Artifact SSE）已使用官方 SDK 的 Protobuf 类型严格解码。其余为计划示例，未把样例可解析当成服务实装。

HTML 原型通过浏览器验证：四个入口和复制、发布配置、待补材料筛选与详情、产物详情和窄窗口。它使用模拟数据；验证浏览器和临时服务均已关闭。

## 阶段 1 完整验收

| 组 | 必测用例与可复核证据 |
| --- | --- |
| 协议合规 | Card 路径/尾斜杠/子路径、字段/版本/鉴权声明；六种核心方法、默认阻塞与立即返回、JSON-RPC id 与错误；时间戳 UTC、Task/Status/Artifact 单选信封、标准客户端解码。List 按授权、排序、过滤和分页，分页最后为空 token，includeArtifacts 与 historyLength=0 形状正确；未启用扩展给规范错误。 |
| 上下文与补答 | 新 Context、原 Context 新 Task、只给 Task ID、错配双 ID、终态拒绝、未知／失权统一结果。同 Context 并发只能接收一项；独立 Context 并行。两轮暂停、短码／结构化补答、无效格式、超时、回执过期、重复补答和不同内容同 ID；同 Task 对应多个不同内部 Run。验证原 UI 工具不会出现在 A2A schema，普通 Console UI 仍可恢复。 |
| 运行与重启 | 同进程覆盖提交、取消、超时、终态竞速和清理。进程中断后发布仍可查（使用正式配置存储时），近期任务为空、旧 Task/Context 不可用，已有历史和文件按现有规则留存。两个实例的隔离试验用来确认边界，无证据的执行不重跑。 |
| 观察与停止 | 真实 TCP 断开仍运行；重复订阅、首帧快照水位、慢客户端、暂停收束、终态订阅拒绝；断线后查状态及重订阅。取消覆盖排队、暂停、运行中、超时与完成竞速；只有实际停止确认才 canceled；结果不能被晚到取消覆盖。 |
| 身份与入口隔离 | 三个发布的 Card、地址与内部 Agent 一一匹配。验证无凭据、错 scope、跨发布、跨主体、跨企业、错 App、权限撤销、伪造内部 Header、同主体刷新、已发布与未发布 Card、管理员诊断权限；所有读写、下载、订阅、游标拒绝。用原普通 Chat/History/附件导出证明 A2A 专用会话不会串入。 |
| 产物 | 用一份脱敏 XLSX 提交→缺期间→断线→查问题→同 Task 补答→两项交付；文件可打开、尺寸/摘要匹配。同一发布另验只返回文本的任务，确认没有虚构的文件交付。另验超限、禁止格式、只读权限、上传引用过期、发布失败、孤立清理、文件过期、取消后无未授权交付和并发重复下载。 |
| 数据库和升级 | 未启用时普通服务保持工作。Task/Context 无迁移。独立发布配置若获批，另用隔离库测试目标方言、唯一约束、备份与回退。实际 MySQL/PG 和基础镜像未验的项单列。 |
| Console | 新建默认启用、编辑保留原启停状态、列表启停、保留编码并编辑 Agent 和多应用范围，验证空范围无法保存、接入示例与应用一致。覆盖启用预检、作用域筛选、失败和空态、换页取消请求、复制、等待补答和过期产物。正常 Console Chat SSE、渠道、配置备份、导入导出和 UI 工具历史回归，测试期间不改共享环境。 |
| 真实接入 | 固定 [官方 A2A Python SDK](https://github.com/a2aproject/a2a-python)、[A2A Inspector](https://github.com/a2aproject/a2a-inspector) 和 [A2A TCK](https://github.com/a2aproject/a2a-tck) 的版本/提交。TCK 独立环境，先核对其 Python 要求、适用传输及声明能力。先用官方工具复核实际报文，再用候选 CodeBuddy CLI 验发现、提交、补答和取件；需要登录时走官方客户端。没实测的客户端版本不能写进支持范围。 |

阶段 2 增加 [SDK/CLI/MCP 的包装与混合调用验收](server-clients.md#验收和发行)，以阶段 1 的任务接口为基准。
