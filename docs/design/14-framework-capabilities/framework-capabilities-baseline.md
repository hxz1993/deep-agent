# Pydantic AI 2.31 与 Harness 0.21 能力采用台账

## 1. 目的与审计范围

本文是 DeepAgent 的框架采用事实源，避免每次需求讨论都重新回答“框架是否已经实现”。审计范围固定为：

- `pydantic-ai-slim[openai,anthropic,google,mcp]==2.31.0`
- `pydantic-ai-harness[skills]==0.21.0`
- DeepAgent `v0.4.13` 当前实现

证据顺序为：固定版本源码与公开 API、官方文档、DeepAgent 源码和测试。Harness 仍是 `0.x`，
本文不把尚未固定依赖的新版本能力当成当前事实。

## 2. 采用原则

### 2.1 框架优先

新增能力时依次判断：

```text
Pydantic AI Core 是否已有等价原语？
  ├─ 是：直接使用 Core
  └─ 否：Harness 是否有公开且语义等价的能力？
       ├─ 是：复用 Harness，并只补产品边界
       └─ 否：DeepAgent 才实现最小产品逻辑
```

不得因为“当前调用起来不方便”就在 `AgentRunner` 中复制一套模型循环、工具状态机、取消、
Tool Call/Return 配对或 Deferred 协议。

### 2.2 产品层不能错误下沉

以下责任不会因为框架提供了类似机制就自动消失：

- user、Agent、App、Space 的授权和数据隔离。
- SQL 会话、原始有序历史、分支、标题和所有权。
- Context Reference 原始快照及其模型投影。
- 自动/主动压缩 SSE、Checkpoint Hash/CAS 和历史边界。
- MCP 数据库配置、Header 转发、共享连接租约和 ToolPackage 授权。
- UI Deferred 的 Pending、刷新恢复和业务表单协议。
- DeepAgent 对外 REST、SSE、History 和 Console 契约。

框架原语解决“Agent 如何运行”，DeepAgent 解决“线上产品如何配置、授权、持久化和展示”。

### 2.3 TODO 准入规则

一个事项只有同时满足以下条件才进入“待实现”：

1. 已有真实需求或故障证据。
2. 产品边界和负责层明确。
3. 已确认优先使用 Core/Harness 的公开 API。
4. 验收方式明确。
5. 不与现有能力重复。

只有框架存在某个类或示例，不构成开发理由。

## 3. 当前层级与所有权

| 层级 | 当前职责 | 首选实现 |
| --- | --- | --- |
| Agent Loop | 模型请求、工具循环、输出校验、中断消息 | Pydantic AI Core |
| Runtime Capability | Tool Search、Thinking、Instrumentation、Hook、History Processor | Pydantic AI Core |
| Reusable Capability | Skill、确定性压缩 tier、会话检索、Guardrail、Tool Output Limit | Harness |
| Product Configuration | Agent、模型、ToolPackage、MCP、UI Tool、Skill 安装信息 | DeepAgent |
| Product State | Session、History、Deferred、Checkpoint、Context Reference | DeepAgent SQL Store |
| Product Protocol | REST、SSE、History、Console 和跨前端契约 | DeepAgent |

## 4. Pydantic AI 2.31 能力审计

### 4.1 已经采用

| 官方能力 | DeepAgent 当前用法 | 结论 |
| --- | --- | --- |
| `Agent`、官方 Provider/Profile | 统一模型运行；DeepSeek 使用官方 `DeepSeekProvider` | 保持，不再引入 Provider 专用模型循环 |
| `ModelSettings` | 温度和 `max_tokens` 进入普通与流式请求 | 已采用；摘要保留独立动态上限 |
| `UsageLimits` | Run 级请求数和可选累计 Token 限制 | 已采用；窗口预算仍由 DeepAgent 前置估算 |
| Toolset 体系 | Function、MCP、External、Dynamic、Prepared、Combined 等运行装配 | 已采用；ToolPackage 只是产品元数据包装 |
| `ToolSearch` + `DeferredLoadingToolset` | MCP Top 3 预选、Top 5 搜索和渐进披露 | 已采用；检索算法由 DeepAgent 提供 |
| Deferred/Capability可用性判断 | Core根据历史中的披露与加载证据决定工具能否执行 | v0.4.8明确以Core为唯一事实源；猜测隐藏工具名返回`ModelRetry`，不增加DeepAgent执行拦截器 |
| `PrepareTools`/Prepared Toolset | 按附件、Skill资源、任务和授权条件准备本轮工具 | 已采用；系统 `read_file`等只在有来源时注入，权限仍在执行时复检 |
| `DeferredToolRequests/Results` | UI Tool Pending 与恢复 | 已采用；数据库恢复和前端协议由 DeepAgent 提供 |
| `CancellationToken`、`RunCancelled` | 正式停止 Run，持久化官方 interrupted 历史 | 已采用；多 Pod 路由仍是部署责任 |
| `ModelRetry`、`ToolFailed` | 区分模型可纠正错误和终止性工具错误 | 已采用 |
| `BaseToolCallPart/ReturnPart` | Local、Tool Search、Provider Native 历史配对 | 已采用 |
| `conversation_id` / `run_id` / `RunUsage` | 同一 Session 的多次发送关联、每次 Run 独立标识和 Provider Usage | v0.3.17 已贯通 SSE、History、脱敏日志和 Console 当前 Run；不另建运行表 |
| `IncludeToolReturnSchemas` | 只为两个复杂查询 MCP 工具披露可信返回结构 | v0.3.17 已按 MCP 原始工具名白名单启用；只在工具直接披露或搜索加载后增加 Token |
| Harness `Skills` | 已授权 Skill 只先披露名称/简介，按需加载 `SKILL.md` | v0.3.18 已采用；安装、授权、素材与脚本隔离仍由 DeepAgent 负责 |
| Output API | 已完成 `ToolOutput` 等黑盒研究 | 尚未启用，归入有潜力而非重新造 Output 状态机 |

### 4.2 下一步优先采用

| 官方能力 | 推荐用途 | DeepAgent 只需补充 |
| --- | --- | --- |
| `Instrumentation` + OpenTelemetry | 在 v0.3.17 运行标识和结构化日志之上增加跨实例 Trace | 先选定 OTLP 后端、保留周期和访问控制；复用现有字段，不建立第二套生命周期 |
| `Hooks` | 后续减少产品埋点对 Runner 的侵入 | 只在官方 Hook 能等价覆盖当前 Tool Search、Deferred、压缩和取消阶段时迁移 |

Instrumentation 与 Hooks 应合并成一条“运行观测”后续主线，不分别建立两套埋点。
v0.3.17 已先使用官方 Run 信息、消息和 Usage 输出稳定脱敏日志；缓存只归一化 Provider 已返回的
cache hit/miss Token，没有数据时保持 unknown。它不等于启用缓存，也不修改模型请求。

### 4.3 有潜力，按需求触发

| 官方能力 | 触发条件 | 采用边界 |
| --- | --- | --- |
| `Thinking` | 多 Provider 推理开关成为产品配置 | 统一模型目录、SSE、History 和 Console，不再增加 DeepSeek 特例 |
| `FallbackModel`/模型选择 | Agent 明确提出 SLA 或候选模型策略 | 必须显式配置成本、地域和能力差异，禁止平台静默全局回退 |
| `ApprovalRequiredToolset` | 出现真实写入、删除、外发工具 | 只提供本轮审批原语；审计、TTL、幂等和权限复检仍由 DeepAgent 提供 |
| `HandleDeferredToolCalls` | 有可信后端可自动处理某类 Deferred | 不用于绕过现有用户 UI Pending，也不改变 External Tool 语义 |
| Provider Native Tools | 某个托管 Provider 的能力、模型和执行环境成为明确产品功能 | 作为 Provider 增强并显式启用，不替代公共 MCP、Sandbox、附件、RAG 或当前页面 UI Tool；Computer Use 见 [15 课题](../15-browser-and-page-actions/browser-and-page-actions-design.md#23-provider-原生-computer-use) |
| Durable Execution | 长任务需要进程崩溃后从外部编排器恢复 | 需要 Temporal/DBOS/Prefect/Restate 等明确基础设施；不替换 SQL 会话 |

### 4.4 明确不采用或不默认采用

| 能力 | 当前结论 |
| --- | --- |
| `RaiseContentFilterError` | 无真实误判证据，不为潜在 Provider 行为增加一整套错误协议分支 |
| `RenamedToolset` 作为 UI 美化 | 会改变模型协议名和历史兼容；MCP 工具原样展示最终稳定协议名 |
| Provider 原生压缩与 DeepAgent Checkpoint 同时启用 | 两层都改写模型历史，边界和事实源不清；当前互斥 |
| 静默全局 `FallbackModel` | 可能改变成本、地域、数据出境和工具能力，禁止默认开启 |
| 全量 `IncludeToolReturnSchemas` | Schema Token 成本不可控，只能逐工具验证 |
| Core Durable Execution 替换 SessionStore | 编排步骤持久化与产品聊天历史不是同一事实源 |

当前暂缓而非关闭的能力是显式 Provider Prompt Cache。DeepSeek 等自动缓存只需观测现有 Usage；只有具体模型明确支持时，才评估 `CachePoint`、Provider cache key、TTL 和 Harness `WarnOnCacheBusts`，不得提供一个假装适配所有 Provider 的全局开关。

## 5. Harness 0.21 能力审计

### 5.1 已经采用

| 官方能力 | DeepAgent 当前用法 | 结论 |
| --- | --- | --- |
| `ClampOversizedMessages` | 确定性限制异常大消息 | 保持公开 API |
| `ClearToolResults` | 压缩旧工具结果 | 保持，Checkpoint 前仍由 DeepAgent 复检 |
| `DeduplicateFileReads` | 去除重复文件读取内容 | 保持，不改变原始历史；运行时字节/解析复用由 `read_file`的Run级缓存负责 |
| `estimate_token_count` | 作为估算实现的一部分 | 保持统一 `TokenEstimator` 封装，不让 Console复制算法 |
| `ToolOutputLimits` / `Spill` | 超大工具结果在进入History前外置 | v0.4.0适配平台文件服务；保留Harness原生 `read_tool_result`，不使用本地Store或自研读取状态机 |
| `Planning` | 模型在复杂任务中维护单轮结构化计划 | v0.4.16保留`write_plan/read_plan/update_task_statuses`与`InMemoryPlanStore`；公开Guidance负责ID、Deferred和收尾约束；Console在正常Final或UI Pending隐藏浮层，取消/失败保留；不新增任务引擎或计划表 |

Planning工具收敛使用Test近30天真实History作为依据：4312条消息中有14个会话、21个Run使用过
Planning；成功记录中`write_plan/read_plan/update_task_statuses/update_task_status`分别调用
24/14/35/17次，`add_task/remove_task`均为0次。单项状态更新统一并入批量工具后，模型只需掌握一套
稳定ID更新协议；旧History名称继续识别，不迁移原始消息。

2026-08-19使用隔离SQLite与`deepseek-v4-flash`执行12个真实Run：2个简单回答均未规划，6个
多步骤任务均在最终正文前把最后快照收口为全部完成，1个普通任务进入UI Choice Pending，1组
Confirm Pending/新Run恢复正常完成，另1个计划Run正式取消并产生`cancelled`。共观察到36次规划
调用，只出现当前三个工具，被删除工具为0次；模型在猜错任务ID后能够通过`read_plan`取得稳定ID并
继续更新。该结果证明Guidance改善常见收尾路径，但不把模型行为升级为后端强制完成保证。

### 5.2 v0.4.8升级采用边界

- Planning使用官方实现但只披露三个实际高频工具，继续获得0.21修正后的CachePoint顺序；DeepAgent
  仍只补充计划快照、SSE/History映射和Console浮层，不改框架Store。
- Harness确定性压缩tier可处理`ToolAvailabilityDeltaPart`；DeepAgent仍负责完整请求预算、主动/自动
  压缩、Checkpoint和Hash/CAS，不切换到`SummarizingCompaction`。
- `estimate_context_tokens`新增的Provider Usage锚点只作为后续校准参考；当前工具Schema与输出预留
  仍由统一`TokenEstimator`计算，没有偏差证据时不更换事实源。
- MCP底层获得新协议兼容，但当前依赖解析继续使用稳定FastMCP 3，不主动安装FastMCP 4预发布版。
- Realtime、Coder、Researcher、AG-UI、Provider原生Compaction和新的持久化体系均不在本次采用范围。

Harness `Skills` 不读取 references/assets，也不执行 scripts。v0.3.18 直接使用它作为线上 Skill 的
渐进披露内核；DeepAgent 负责安装、权限、References/Assets 读取和脚本隔离。

### 5.3 有潜力，按需求触发

| 官方能力 | 触发条件 | 采用边界 |
| --- | --- | --- |
| `ToolGuardrail` | 工具风险元数据和真实副作用工具就绪 | 执行前权限复检、执行后脱敏；不替代业务服务权限 |
| `SpendLimits` | 出现用户/App/Space 配额或收费需求 | 需要共享计数 Store 和价格口径；私有模型可按 Token/调用量而非美元 |
| `Memory` | 产品明确需要跨会话、可查看和可删除的长期笔记 | namespace 由应用计算，但授权、审计、删除和 Store 仍由 DeepAgent 负责 |
| `StepPersistence` | 长工具或副作用流程需要崩溃恢复与 effect ledger | 不替代聊天历史；只记录可恢复步骤和副作用幂等信息 |
| Planning跨Run持久化/计划模式 | 用户需要跨消息继续计划或人工只做计划 | 当前单轮计划不升级为第二套会话事实源；需另行定义恢复、编辑和权限边界 |
| `SubAgents`/`DynamicWorkflow` | 出现稳定的专业分工或并行长任务 | 子 Run 隔离、用量、工具继承和结果展示必须显式设计 |
| `SystemReminders` | 长工具循环出现真实 instruction fade | 只注入少量稳定规则，不作为修复差 Prompt 的常规手段 |
| `MediaStore` | Artifact/附件需要内容寻址、去重和共享对象存储 | user/App/Space 授权和对象生命周期仍由产品层负责 |
| `WarnOnCacheBusts` | 已启用且可观测 Provider Prompt Cache | 与 CachePoint 同步试点，不单独实施 |
| `Advisor` | 明确需要辅助模型复核且已有预算策略 | 应接现有辅助模型配置，不让模型自由选择任意外部模型 |

### 5.3.1 暂缓采用

| 官方能力 | 暂缓原因 | 重新启动条件 |
| --- | --- | --- |
| `ConversationSearch` | 当前没有真实长程任务证明摘要后无法恢复关键事实，中文历史检索的召回和排序也缺少可靠样本 | 出现可稳定复现的摘要缺失且继续任务确实需要原文；届时用自定义 `HistorySource` 读取现有 `chat_messages`，不启用第二套历史 |

### 5.4 明确不采用或不作为平台默认能力

| 能力 | 当前结论 |
| --- | --- |
| `SummarizingCompaction` 整体替换现有压缩器 | 当前缺少工具 Schema 完整预算、主动 SSE、Checkpoint Hash/CAS 和目标复检；只吸收公开的确定性 tiers 和摘要方法 |
| `StepPersistence` 替换 SQL Session/History | 两者解决的问题不同，禁止建立第二个聊天事实源 |
| `CapabilityCreation`/Runtime Authoring | 允许模型向主服务写入并导入 Python 能力，不符合线上安全边界 |
| `CodeMode` 作为默认工具入口 | DeepAgent 已有 Tool Search 和受控 Sandbox；不为缩减工具 Schema 引入模型生成代码执行面 |
| `FileSystem`/`Shell` 直接开放主服务文件系统 | 与附件、Sandbox 和多租户隔离冲突，只能在专用隔离 Backend 中作为新产品能力设计 |
| `ManagedPrompt` 替换 Agent 数据库 Prompt | 会形成第二个 Prompt 事实源；除非未来整体迁移配置治理，否则不采用 |
| `RepoContext`、`Macroscope`、`PydanticAIDocs`、`LocalStack` | 面向 Coding Agent 或专用研发场景，不进入通用业务 Agent 基线 |
| `BrowserUse` | 属于独立浏览器子 Agent；外部/未知网站需求出现时按 [15 课题](../15-browser-and-page-actions/browser-and-page-actions-design.md#52-外部网站独立-browseruse-能力) 接入，不替代当前页面 UI Tool，也不默认启用 |
| `Exa`、`StackOne` | 属于具体外部能力；有产品需求时作为独立 ToolPackage/MCP 接入，不作为框架基础设施 |

## 6. 现有 DeepAgent 能力是否应被替换

| DeepAgent 能力 | 框架近似能力 | 当前决策 |
| --- | --- | --- |
| SQL Session/History | Harness `StepPersistence` | 不替换；前者是产品事实，后者是执行步骤快照 |
| Checkpoint 压缩 | Harness `SummarizingCompaction` | 不整体替换；继续复用确定性 tiers，保留产品事务语义 |
| MCP 渐进披露 | Core `ToolSearch`/Deferred Toolset | 已迁移为 Core 原语；DeepAgent 仅保留检索算法和观测 |
| UI Pending | Core Deferred | 已使用 Core；DeepAgent 保留持久化和页面恢复 |
| 正式取消 | Core `CancellationToken`/`RunCancelled` | 已使用 Core；不再通过断 SSE 模拟停止 |
| 工具生命周期埋点 | Core Run 信息/Usage，后续 Instrumentation/Hooks | v0.3.17 已建立最小脱敏日志；等官方 Hook 能覆盖产品阶段后再迁移，不重复保留两套埋点 |
| Skill Instructions | Harness `Skills` | 下一步采用；安装、版本、素材和执行权限仍由 DeepAgent 管理 |
| 大工具结果 | Harness `ToolOutputLimits` | v0.4.0已采用；平台File ID即会话句柄，权限从原始History恢复，不新增Artifact表 |
| 工具审批/脱敏 | Core Approval + Harness `ToolGuardrail` | 风险元数据成熟后采用，不自行写通用拦截状态机 |
| 跨会话记忆 | Harness `Memory` | 产品需求出现后采用 Store 接口，不把聊天历史伪装成 Memory |

## 7. 五类决策状态

以后所有能力只能属于以下一类：

| 状态 | 含义 | 是否允许直接排期 |
| --- | --- | --- |
| 待实现 | 有真实需求、边界和验收，框架采用路径已锁定 | 是 |
| 有潜力 | 技术可行且未来可能有价值，但触发条件尚未发生 | 否，需求触发后先复核 |
| 暂缓 | 价值已确认，但当前产品时机、Provider 条件或投入产出不足 | 否，触发条件满足后复核 |
| 已关闭 | 已决定不做、被其他能力取代或合并，不再反复调研 | 否，除非关闭条件发生变化 |
| 已实现 | 已交付并有版本/测试证据 | 否，只做维护和回归 |

## 8. 后续版本升级审计流程

升级 Pydantic AI 或 Harness 时执行：

1. 记录旧版与目标版，不直接审查 `main` 未发布能力。
2. 阅读官方变更和目标版本公开 API。
3. 对照本文逐项标记：兼容、可替换、新增、废弃。
4. 先用隔离环境验证现有 DeepAgent 契约，再决定是否采用新能力。
5. 更新本文和 TODO；没有真实采用价值的新增类只记在本文，不进入 TODO。
6. 固定两个包的精确联合版本并运行目标回归，禁止无上限版本漂移。

下次完整复审触发条件：

- 升级任一固定版本。
- 框架新增能力可以明显删除 DeepAgent 自研主流程。
- 真实需求命中“有潜力”条目的触发条件。
- 当前已采用的公开 API 被弃用或语义变化。

## 9. 官方证据入口

- [Pydantic AI Capabilities](https://ai.pydantic.dev/capabilities/)
- [Pydantic AI Toolsets](https://ai.pydantic.dev/toolsets/)
- [Pydantic AI Message History](https://ai.pydantic.dev/message-history/)
- [Pydantic AI Durable Execution](https://ai.pydantic.dev/durable_execution/)
- [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)
- [Harness Skills](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/skills.md)
- [Harness Compaction](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/compaction.md)
- [Harness Conversation Search](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/conversation-search.md)

DeepAgent 专题边界继续分别记录在：

- [`../03-context-compaction/context-compaction-research.md`](../03-context-compaction/context-compaction-research.md)
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)
- [`../09-structured-output/structured-output-design.md`](../09-structured-output/structured-output-design.md)
- [`../12-agent-skills/skill-system-design.md`](../12-agent-skills/skill-system-design.md)
- [`../13-tool-discovery/tool-discovery-design.md`](../13-tool-discovery/tool-discovery-design.md)
- [`../15-browser-and-page-actions/browser-and-page-actions-design.md`](../15-browser-and-page-actions/browser-and-page-actions-design.md)
