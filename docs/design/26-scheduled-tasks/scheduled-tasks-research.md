# 定时任务上游调研

核验日期：2026-09-14，Asia/Shanghai。研究范围是「定时创建一次 Agent 工作」，同时区分执行中的崩溃恢复。

## 1. 版本与证据范围

本地参考库不等于产品安装版本，也不一定等于上游最新发布。本次没有更新任何参考仓库。

| 项目 | 本次本地源码基线 | 核验说明 |
| --- | --- | --- |
| DeepAgent | `4fd0bd72dcbc`，v0.10.0 | 工作区另有主线程 UI 改动；未改动这些文件 |
| 产品 Pydantic AI / Harness | `requirements.txt` 固定 `2.35.1` / `0.26.0` | 没有安装 Prefect、Temporal 等调度依赖的本次变更 |
| Pydantic AI 参考库 | `00bc47eebf7c`，v2.41.0 | 另核验最新发布 v2.43.0 的说明、目录树和 durable execution 文档 |
| Pydantic AI Harness 参考库 | `c897c4e8bcb7`，v0.29.1 | 另核验最新发布 v0.31.0 的说明、目录树、能力目录、导出和 StepPersistence 文档 |
| DeepSeek Harness / DSH | `aa8262ec0916` | schedule 包有明确实现、文档和重启测试 |
| Codex 开源参考库 | `7769bccbb2b4` | CLI / Harness 与桌面 Scheduled 产品层分开判断 |
| Dify | `d5a8ace1c4c1` | Schedule Trigger 节点、计划表、Celery 轮询与执行器 |
| OpenClaw | `79f0df31c8eb` | 当前已采用 SQLite，不能照搬旧 jobs.json 说明 |
| Hermes Agent | `3c27eb6234bf`，v2026.8.3 | cron 配置、ticker、执行会话及持久化代码 |

上游最新发布信息：[Pydantic AI v2.43.0](https://github.com/pydantic/pydantic-ai/releases/tag/v2.43.0)、[Harness v0.31.0](https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.31.0)，均于 2026-09-12 发布。该日期是本次查询结果，不是长期有效的“最新版本”声明。

## 2. 先区分四种关闭

| 情况 | 定时任务的影响 |
| --- | --- |
| 关闭浏览器页面，后台服务还在 | 后台调度应照常工作；不应依赖页面 setTimeout |
| 目标 Agent 会话没有打开 | 取决于实现：DSH 等待原会话恢复；独立调度器可以主动创建或加载会话 |
| 调度服务或整台电脑关闭 | 本机无法准时执行；重启后根据持久状态与补跑策略处理 |
| 已经开始执行，进程在工具调用中崩溃 | 是运行恢复问题；不能用“补一次定时任务”证明外部操作不会重复 |

服务器部署时，用户电脑关机通常不影响服务器上的调度。若 DeepAgent 只运行在用户电脑上，电脑关机就属于服务停机。

## 3. 横向比较

| 项目 | 配置与存储 | 谁触发、发到哪里 | 停机后行为 | 对我们的价值 |
| --- | --- | --- | --- | --- |
| Pydantic AI | 普通 Agent 不管理 Cron 计划；持久执行委托外部引擎 | Prefect Flow 等调用 Agent | 由外部引擎及部署配置决定 | 可复用执行框架；不能直接获得完整定时产品 |
| Pydantic AI Harness | StepPersistence 保存步骤事件、可续接快照、工具效果记录 | 需要宿主启动 Agent.run | 不负责扫描到期计划或自动唤醒进程 | 可研究运行恢复，不替代调度表 |
| DSH | 会话日志中的 `schedule/change` 事件 | live 根 Agent 空闲时 follow-up 原会话 | 冷会话保持逾期；恢复后处理；循环仅最新一次 | 借鉴空闲交付、补最近一次；不适合作为跨冷会话调度器直接搬用 |
| Codex 桌面产品 | 官方文档提供 Scheduled 管理与 RRULE；工具契约暴露本地 automation.toml | 独立任务新会话；会话内任务复用上下文 | 本地文件任务需电脑与 App 运行；公开资料未明确重启补跑承诺 | 借鉴独立入口、会话模式、运行收件箱；不猜闭源调度细节 |
| Dify | `workflow_schedule_plans` 等数据库记录，Cron、时区、next_run_at | Celery Beat → poller → executor → Workflow | 扫描已逾期计划，派发一次，下一次从当前时间算；不逐个补所有周期 | 最接近“配置表＋后台轮询＋执行记录”的结构 |
| OpenClaw | 共享 SQLite：任务定义、运行状态、运行历史 | Gateway 调度；main、isolated、current、指定 session | 默认启动补跑；支持跳过循环任务；分批、错峰、延迟 isolated 启动 | 成熟度高，可借鉴补跑、运行归属与防重复机制 |
| Hermes | 每 profile 的 `cron/jobs.json`；输出文件和会话记录 | Gateway ticker / cron 调度入口；每次构造 cron 会话 | 长期逾期循环任务仍运行一次，再推进未来时间；不重放积压周期 | 提示词＋计划＋结果的产品形式直接；不建议照搬文件存储 |

## 4. Pydantic AI：有持久执行集成，没有独立定时管理模块

最新发布的 [durable execution 概览](https://github.com/pydantic/pydantic-ai/blob/v2.43.0/docs/durable_execution/overview.md)列出 Temporal、DBOS、Prefect、Restate、AWS Lambda durable functions 五类官方协作集成，并列出外部 Kitaru、Airflow。这里的“支持”是集成执行引擎，不是每种都由 Pydantic 提供相同的 Cron CRUD、停机补跑和控制台。

[Prefect 文档的 Deployments and Scheduling](https://github.com/pydantic/pydantic-ai/blob/v2.43.0/docs/durable_execution/prefect.md#deployments-and-scheduling)给出官方路径：在 Flow 内创建带 `PrefectDurability` 的 Agent，调用 Agent.run，再用 Flow 的 serve/deploy 配置 cron、interval 或 rrule。计划保存、生成运行实例、监控和 worker 部署属于 Prefect。

这说明可以用现成编排系统完成定时，不代表把一个 capability 加入当前 Agent 配置就能完成本项目需求。还需要：后台身份与权限、会话归属、运行记录关联、工具授权、停止与恢复、无人值守的交互工具处理、产品配置 API 和 UI。

### Prefect 的停机也需要细分

Context7 对官方源码和文档的查询确认：

- `flow.serve()` 默认在正常关闭时暂停部署计划；`pause_on_shutdown=False` 可改变该行为。强杀进程不能等同于正常执行关闭回调。
- 若服务端仍在生成计划、只有执行端离线，已生成的运行可以积压为待执行/迟到任务；是否继续处理还取决于暂停、取消、并发与 runner 配置。
- 若调度服务端也停了，其普通 scheduler 从恢复时的当前时间往后生成；**没有提前生成的历史时间点，不会自动全部回填**。
- 显式历史回填可通过 deployment 的 schedule API 指定过去的起止时间。

因此不应写成“采用 Prefect 就会把停机期间所有任务补完”。提前生成且已持久化的运行，与尚未生成的历史时间点，是不同对象。

来源：[serve 的关闭行为](https://github.com/PrefectHQ/prefect/blob/main/docs/v3/how-to-guides/deployment_infra/run-flows-in-local-processes.mdx)、[Scheduler `_collect_flow_runs`](https://github.com/PrefectHQ/prefect/blob/main/src/prefect/server/services/scheduler.py)、[历史 schedule API](https://github.com/PrefectHQ/prefect/blob/main/src/prefect/server/api/deployments.py)。这些是查询时的 main 文档/源码，未在本机部署 Prefect 验证。

### 是否直接使用

| 路线 | 判断 |
| --- | --- |
| 继续当前 AgentRunner，由我们的调度模块调用 | 首选；不要求为了定时升级 Pydantic |
| 引入 Prefect 执行部署 | 有可用的官方路径；适合以后同时需要工作流部署、集中运行治理的场景 |
| 引入 Temporal / DBOS 等持久执行引擎 | 长任务恢复、跨进程步骤治理有价值；需专项评估，不能仅为一个 Cron 入口默认引入 |
| 仅升级 Pydantic / Harness | 不会自动增加定时任务入口、存储和补跑规则；Harness 0.31.0 已将 Pydantic AI 最低依赖提高到 2.40.0，不能单独替换到当前 2.35.1 环境 |

## 5. Pydantic AI Harness：StepPersistence 不是调度器

本地 v0.29.1 与最新 v0.31.0 的能力目录、公开导出及目录树中均未发现完整 Cron/Schedule 管理模块。此结论针对已检查的发布版本，不排除未来新增或外部扩展。

[StepPersistence 文档](https://github.com/pydantic/pydantic-ai-harness/blob/v0.31.0/pydantic_ai_harness/step_persistence/README.md)明确区分：步骤记录与能否安全恢复。它提供追加事件、可续接消息快照、工具调用效果账本、conversation/run/parent 关联；不提供完整 graph-state checkpoint，也不负责定时唤醒。

工具记录如果只有 started、没有 completed/failed，崩溃后只能判断为“结果未知”，不能假设操作没发生。把快照重新交给 Agent.run 也不等于外部写操作恰好执行一次。

DeepAgent 已有 Event Store，首版不应再建立一套与之竞争的消息真相源。可以借鉴工具效果账本；如果未来引入 StepPersistence，需要明确它与现有 Event Store 的职责以及事件去重，单独进入长任务恢复课题。

## 6. DSH：持久的会话提醒

实证入口：[schedule README](../../../../deepseek-harness/packages/schedule/schedule/README.zh.md)、`packages/schedule/schedule/src/{index,domain,runtime,types}.ts`、`tests/jsonl-restart.spec.ts`。

- 一次性支持相对延时或绝对时间；重复支持固定间隔，最低五分钟，**没有 Cron 日历规则**。
- 计划事件写入所属会话日志；普通会话折叠完整记录，fork 不继承父会话的活动提醒。
- 插件为加载后创建/恢复的 live 根 Agent 安装 owner，不扫描所有历史会话，也不主动唤醒冷会话。
- 会话忙时等待 idle，不打断正在运行的轮次；正常服务重启后还需恢复目标会话。
- 一次性逾期后补交付；重复提醒错过多个周期只选最近一个，多条重复提醒可以合成一个 follow-up 批次。
- `dispatch` 表示已将 follow-up 入队并记录，**不是模型成功或用户已读**。入队之后、dispatch 持久化之前崩溃有重复窗口。

还有一个不能忽略的产品差别：DSH 的提醒 framing 要求模型展示提醒内容，并把其中内容视作不可信提醒文本，不把它当新的执行指令。它适合“提醒我检查”，不能把该模板原样复制成“无人值守执行分析、写报告”的任务指令。

## 7. Codex：借鉴产品结构，不推测未公开部分

本地开源 Codex 的 core/state/docs 针对 cron、rrule、automation.toml、scheduled task 的定向搜索没有找到完整桌面调度实现。这不足以证明所有产品均无实现，说明不能从开源 Harness 推导桌面补跑算法。

[官方 Scheduled 文档](https://learn.chatgpt.com/docs/automations?surface=app)确认：独立任务每次新开会话，已有会话任务保留上下文；有独立管理和运行结果入口；高级规则使用 RFC 5545 RRULE。本地项目任务要求电脑开机且桌面 App 运行。当前页面由原 Codex automations 地址重定向，包含 ChatGPT 与 Codex 的产品范围，不能混称 CLI 内置功能。

当前桌面工具契约另暴露 `$CODEX_HOME/automations/*/automation.toml`、RRULE、提示词、项目、状态等配置概念。这只是可见契约，不证明底层队列、数据库与提交原子性。本次没有读取用户真实任务或运行记录。**关机错过之后是否补、补多久、补几次，公开资料未确定，保留未知。**

## 8. Dify：计划持久化，轮询派发工作流

源码入口：

- `api/models/trigger.py:WorkflowSchedulePlan`：id、tenant_id、app_id、node_id、cron_expression、timezone、next_run_at，并建立到期查询索引。
- `api/events/event_handlers/sync_workflow_schedule_when_app_published.py`：工作流发布时同步计划。
- `api/extensions/ext_celery.py`：受配置开关控制的周期 poller。
- `api/schedule/workflow_schedule_task.py:_fetch_due_schedules`：查 enabled 且 next_run_at <= now 的记录，按到期时间排序，批量查询并使用 skip_locked。
- 同文件 `_process_schedules`：下一次从当前时间计算，每条计划派发一次 Celery executor，支持每轮派发上限。
- `api/tasks/workflow_schedule_tasks.py:run_schedule_trigger`：检查配额、确定执行用户，调用 AsyncWorkflowService，运行结果由 WorkflowTriggerLog 跟踪。

例：每天 09:00 的计划在停机期间保留旧 next_run_at，09:20 恢复 poller 后符合逾期条件，于是派发一次，再将 next_run_at 推进到下一天 09:00。即使停了三天，这条计划通常也是一次派发，而不是三天逐次运行。

这依赖计划仍启用且记录未被重新发布等操作改写。额度限制、任务队列和 executor 失败也会影响实际运行。源码说明不为 schedule executor 做同一次失败的自动重试；下一周期是新的触发，不应称作成功重试。

实现把消息队列派发与数据库提交分开，不是一个跨系统原子事务，不能由 skip_locked 推导“绝不重复、绝不丢失”。该模块是 **Workflow Schedule Trigger**，并非向任意已有聊天会话注入提示词的现成 API。

## 9. OpenClaw 与 Hermes：两种独立调度宿主

### OpenClaw

来源：`docs/automation/cron-jobs.md`、`src/cron/store/schema.ts`、`src/cron/service/timer-catchup.ts`、`timer-execution-timeout.ts`、`src/cron/types.ts`。

当前版本任务与运行历史已经在共享 SQLite 中。Gateway 运行调度；规则包括 at、every、cron，目标支持 main、isolated、current 和指定 session。旧版文件存储说明已不适用。

默认启动处理错过的任务；`cron.skipMissedJobs=true` 可以让循环任务推进到未来时点，一次性任务不受此开关影响。恢复不是无上限立即启动：当前源码默认首轮最多五项、错峰五秒，isolated agent-turn 启动补跑默认延迟两分钟。它还有运行收据、存活 owner 判断和崩溃恢复分支。这些是版本实现细节，不建议变成我们的硬编码产品承诺。

它适合作为防重、恢复、会话隔离的参考，但已有的任务交付、Gateway 和多渠道能力远超首版需求，整套搬用成本高。

### Hermes

来源：`cron/jobs.py`、`cron/scheduler.py`、`cron/scheduler_provider.py`。

每个 profile 的 jobs.json 保存计划，写入有锁和原子替换，输出落在 cron/output，ticker 默认每 60 秒检查。`get_due_jobs()` 对超出宽限期的循环任务不是简单丢掉：先推进未来时间，仍放入本次 due 集合执行一次，避免无限推迟；不会把积压的周期全部执行。注意邻近旧注释写“跳过过时运行”，实际分支和日志明确是“现在运行一次”。

`scheduler.py` 为运行构造 `cron_<job_id>_<timestamp>` 会话 ID，再执行 Agent。其隔离会话与 profile 权限边界值得借鉴；DeepAgent 已有数据库，不应为定时任务另建 jobs.json 作为配置真相源。

## 10. 证据索引与未验证项

本地参考库根：`/Users/wuzhiruge/PycharmProjects/AI-hub/agent-labs/`；Pydantic 与 Hermes 在 `upstream-agent-references/projects/`；DSH 在 `deepfos-AI/deepseek-harness/`。以上路径与版本表共同定位源码，不依赖未来 main 分支行为。

没有在本次执行真实停机、重启或发送定时消息测试。Codex 私有调度算法未知；Prefect/Temporal/DBOS 的具体生产部署行为需选型后按指定版本验收；所有系统都不能仅凭“有持久化”宣称外部工具副作用 exactly-once。

## 11. 数据流组件参考：定制 Dagster

2026-09-14 补充只读核验。本地 `deepfos-repos/deeppipeline/deep-pipeline-server` HEAD：`403bf97e7e86`。这是 DeepFOS 数据流定制实现，不能直接用当前上游 Dagster 文档替代本地行为，也不代表已验证线上部署参数。

### 配置与执行

- 前端 `element-deeppipeline/src/pages/main/layout/triggerBarWrap/Cron.tsx`：配置 Cron、启停和流程发起人；展示最近十次执行时间预览，并说明按启动参数默认值执行。
- 后端 `python_modules/dagster-webserver/deep_pipeline/routers/cron.py`：使用 `CroniterShim` 校验五段表达式、计算未来时间，使用 `cron_descriptor` 生成可读描述。
- `routers/impl/deploy.py:update_schedules`：发布状态为 APPROVED 时，将启用的规则写入计划存储；其他状态移除对应规则。
- `python_modules/dagster/dagster/_core/storage/schedules/schema.py`：自定义 `scheduled_element` 保存 App、Space、元素、版本、crontab、processing、last_time、next_time；另有 instigators/job_ticks 保存调度状态与触发记录。
- `_canvas/__init__.py:convert_to_defs`：把规则转换为绑定 Job 的 Dagster ScheduleDefinition。
- `_scheduler/scheduler.py`：后台加载到期规则与已发布图，组装资源、运行上下文并提交执行。具备定时后台入口，不依赖前端页面保持打开。

### 补跑与恢复

`_scheduler/scheduler.py` 约839行：对于没有 partition set 的普通计划，多个错过时点取 `tick_times[-1:]`；分区计划则受 max_catchup_runs 限制。这与“普通 Agent 任务错过再多只补一次”的方向一致。

`_core/storage/schedules/sql_schedule_storage.py:get_flow_schedule` 查询 next_time 到期记录并标记 processing；对于未被当前批次选中且已过期三分钟的 processing 记录，有重置分支。`_get_existing_run_for_request` 根据计划和计划执行时间等标签查找已有运行。不能仅凭处理标记与查重就认定多副本强一致或外部工具副作用 exactly-once，本次未做故障测试。

### 对 DeepAgent 最有价值的参考

流程发起人是显式配置。调度器读取指定用户或发布者，并组装 user、App、Space、from_server 上下文。这说明无人值守执行需要后台可信身份和业务作用域，不需要浏览器在线。**不能照抄其缺省用户回退，也不能将“会话归属用户ID”直接当作授权凭据**；DeepAgent 接入方式仍需核对平台可信调用链路。

可借鉴五段 Cron＋可读预览、计划与运行状态分开存储、后台扫描、普通任务只补最近一次，以及显式执行用户。没有必要仅为了定时调用 Agent，引入整个 Dagster 执行引擎或将 Agent 包装成数据流元素。
