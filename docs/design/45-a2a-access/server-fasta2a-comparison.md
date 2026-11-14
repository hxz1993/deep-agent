# FastA2A 与官方 SDK 服务端对照

当前实施继续使用官方 `a2a-sdk[http-server]==1.3.0`。本文记录 FastA2A 的历史对比与实验结果。

基线是 `codex/a2a-server` 已提交的 `13ff4e0d`，使用 `a2a-sdk[http-server]==1.3.0` 的 JSON-RPC 分发器。对照版在同一分支的本地工作区，改用 `fasta2a==2.1.1`，保留官方 SDK 1.3.0 的 Protobuf 类型、错误码和版本校验。实验期间 8002 运行对照版，产品版本为 0.17.0a1，SQLite Revision 016。

## 会话与补材料

`contextId` 映射到 DeepAgent 的一条会话。`taskId` 标识调用方可查询的一项工作，`run_id` 标识 DeepAgent 的一次执行。补材料时，客户端把原 Task ID 和 Context ID 一起带回来，同一任务继续执行，之后查原 Task 就能拿到最终结果。只带 Context ID 会在同一会话启动一个新 Task，继承消息历史。两个 ID 都省略时创建新会话。

8002 的 `8ceae4bf` 从待补材料转为完成。之后的 `aee4ed3b` 是另一 Task，Console 任务详情返回相同的 Context 和 Session `18b36116cd964ec68cd74922c23022e6`。Langflow 也实测了同样的两种行为。

## 适配与维护成本

| 能力 | 官方 SDK 基线 | FastA2A 对照版 |
| --- | --- | --- |
| HTTP 和协议 | SDK 分发、参数转换、错误信封和 SSE；DeepAgent 提供业务 handler | FastA2A 生成 Card、校验 JSON-RPC 信封、编码 SSE；DeepAgent 自行分发六种方法，沿用 SDK 处理 1.0 参数和错误 |
| 默认执行 | DeepAgent 创建执行任务，用 Runner 和现有取消机制执行 | FastA2A 的 InMemoryBroker 分发给自定义消费者，DeepAgent 继续运行 Runner。保留 4 个并发执行槽、原任务续跑和独立取消 |
| 查询与补材料 | DeepAgent 按调用身份、发布、App/Space 查任务，管理 Task 与 Session 的映射 | 相同。FastA2A 2.1.1 默认 TaskManager 会创建新 Task，原始 ListTasks 返回 UnsupportedOperation，需接 DeepAgent 的任务服务 |
| 生命周期和文件 | 有界任务存储、幂等、消息历史、状态更新、FileStore 授权下载 | 相同。FastA2A 的内存存储和默认事件总线未承载业务状态，避免两套状态相互覆盖 |
| 扩展 Worker | 可把当前执行入口抽出，换调度实现 | 提供可替换的 Broker、Storage、Worker 和 EventBus 接口。默认 Worker 串行处理命令，执行期间的取消会排在当前工作之后；对照版把执行分发和取消分开 |
| 改动量 | 已提交 | 两个适配模块约 270 行，Runner、权限、数据库和 Console 业务代码沿用现有实现 |

对照版先尝试直接采用 FastA2A 的 1.0 参数适配。它会删去 `returnImmediately` 和 `tenant`，对空值、未知状态枚举和暂缓的推送配置也有不同的解析结果。最后只用 FastA2A 校验信封，方法参数只做一次官方 SDK 解码。Card 补齐既有鉴权字段和不支持扩展 Card 的声明。SSE 保留心跳和断连清理。实验实现依赖 FastA2A 2.1.1 的 Card 与 SSE 序列化入口，升级时须重跑协议契约用例。

FastA2A 的 Worker 接口有助于以后替换调度，但本次接入的是同进程内存 Broker。队列里只传执行标识，凭据和执行上下文留在本进程。若要跨进程执行，仍要实现可恢复的任务状态、身份引用、取消信号、结果交付和故障恢复。这些能力可以从 DeepAgent 的执行层抽取，供 A2A、MCP、CLI 和 SDK 共用。现阶段建议保留官方 SDK 的协议分发，等确有跨进程调度需求时再评估接 FastA2A Broker 或其他调度实现。

来源：[FastA2A 2.1.1](https://pypi.org/project/fasta2a/2.1.1/)、[官方 SDK 1.3.0](https://pypi.org/project/a2a-sdk/1.3.0/)、[FastA2A Worker](https://github.com/datalayer/fasta2a/blob/d94dc2fa644cec0a4b1371738652b36b7f9a0e78/fasta2a/worker.py)。

## 本地效率

同一台 Mac、同一个 Python 3.12.13 环境、同一套 SQLite 与鉴权替身，使用真实回环 HTTP。交替运行两版各 3 轮。表中数值为各轮 p50 和 p95 的中位数，单位 ms。执行替身固定等待 3 ms 后生成同一 Artifact。真实 Runner 行使用 FunctionModel，包含实际的 Runner 和会话持久化。各轮启动独立进程，RSS 为进程峰值。

| 操作 | 每轮请求 | 官方 SDK p50 / p95 | FastA2A p50 / p95 |
| --- | ---: | ---: | ---: |
| Card | 120 | 1.626 / 1.853 | 1.716 / 2.075 |
| GetTask | 120 | 1.322 / 1.561 | 1.311 / 1.554 |
| ListTasks，20 条 | 120 | 1.587 / 1.748 | 1.605 / 1.893 |
| SendMessage，短消息 | 60 | 5.957 / 6.550 | 6.226 / 7.488 |
| SendMessage，64 KiB | 48 | 7.488 / 8.709 | 7.688 / 8.574 |
| SendMessage，并发 4 | 96 | 9.555 / 10.372 | 9.093 / 9.952 |
| SSE 首帧 | 48 | 2.333 / 2.637 | 2.267 / 2.578 |
| SSE 完整响应 | 48 | 6.261 / 6.606 | 6.406 / 6.839 |
| SendMessage，真实 Runner | 12 | 65.579 / 72.269 | 66.626 / 71.890 |

并发 4 吞吐分别为 421 和 441 请求/秒。两版进程峰值 RSS 的轮次中位数分别为 382.9 和 383.6 MiB。单轮和多轮差值较小，本次没有观察到稳定的整体性能收益。样本用于比较同机适配开销，实际模型时延和分布式运行表现不在这个数字内。

## 回归

| 项目 | 结果 |
| --- | --- |
| 官方 SDK 基线 | 9 项集成用例通过。8002 原进程实测图片、补附件、同 Context 追问和 XLSX。Langflow 同组场景通过 |
| FastA2A 定向 | 11 项通过。覆盖六种协议操作、重复消息、分页、同任务补答、图片输入、受权下载、发布启停及范围、凭据与 App 校验、错误码和四并发外排队取消 |
| 全量回归 | 官方 SDK 的一期发版记录为 Python 3,603 项、66 跳过、Console 1,182 项通过。FastA2A 最终工作区重跑 `scripts/run_tests.py --suite all`，Python 3,605 项、66 跳过、Console 1,182 项通过。两次均零失败、零错误 |
| 断连和重订阅 | 两版用真实回环 SSE 测试，首帧后关闭连接，观察者均清零，随后可订阅同一 Task，第二次断开后任务仍执行并可查到结果 |
| 官方 Python 客户端 | 真实平台 Token、两个有权 App，同步 `71cd7c74` 和流式 `67dd86a6` 都返回 20。实测查询、列表、订阅和取消，取消样例 `b0dc5e04` |
| 直接调用与 Langflow | 8002 识别图片 `Q9M4`，在原任务补文本附件后回读 `TEXT-731`，同 Context 的新 Task 延续历史。两条链路均下载并打开 XLSX，C2 为 20 |
| CodeBuddy Code | 走已有回环目录适配器，原生 GetAgentCard、SendMessage 和 GetTask 得到差额 20，8002 读回同一 Task `810af7ca`。在同一 CodeBuddy 会话补交图片，原 Task `29022012` 完成并返回 `Q9M4`。用原生工具提交并查询 XLSX 任务 `4d7c2042`，从适配器下载后打开确认 C2 = 20 |
| 本地官方 Inspector | 读取 8002 的 Card，普通 `71db9a15` 和流式 `07f60eb6` 均返回 20，响应校验无错误 |
| 在线 A²D Playground | 通过临时 HTTPS 接入隔离样例，读取 Card，普通和流式均完成并返回 20。验收后关闭浏览器、入口及隔离服务 |
| Console 与数据库 | 本地 8002 浏览器核对发布列表、Card 和近期任务。SQLite 016，`quick_check=ok`。8000 健康检查通过 |

MCP、DeepAgent CLI 和 Python SDK 仍处于后续阶段。当前比较中的官方 Python SDK 是 A2A 协议客户端。

## 实验环境的依赖差异

实验新增 `fasta2a==2.1.1`，`a2a-sdk[http-server]==1.3.0` 改为 `a2a-sdk==1.3.0`。原 extra 的 Starlette 和 sse-starlette 仍在运行依赖中。FastA2A 需要 `opentelemetry-api>=1.28.0`、`pydantic>=2.10`、`starlette>0.29.0`，本地已满足，`uv pip check` 通过。Pydantic AI、Harness、Python 和独立沙箱依赖沿用基线。若采用实验版发布，需重建并核验运行基础镜像，确保代码与镜像一起发布。官方 SDK 基线可继续使用现有配套镜像。
