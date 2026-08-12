# DeepAgent 设计与研发台账

本文件是跨课题事项的唯一状态入口。它不再使用 P0～P3 堆积候选功能，只保留五类决策：

1. **待实现**：已有需求、边界、框架采用路径和验收，可以进入近期计划。
2. **有潜力**：未来可能有价值，但当前不排期；必须满足触发条件后再打开。
3. **暂缓**：价值存在，但当前缺少产品时机、Provider 条件或投入产出依据；不进入近期计划。
4. **已关闭**：明确不做、已被替代或已合并，不再重复调研。
5. **已实现**：已经交付并有版本或测试依据。

框架采用的完整依据见
[`14-framework-capabilities/framework-capabilities-baseline.md`](14-framework-capabilities/framework-capabilities-baseline.md)。
以后升级 Pydantic AI 或 Harness 时，先增量更新该台账，再决定是否调整本文件。

## 维护规则

- “框架有这个类”不等于需要开发。
- “待实现”必须写清首版范围和验收，不能顺带扩成平台级重构。
- “有潜力”必须写明触发条件，未触发前不得直接排期或预埋半成品。
- “暂缓”保留已确认价值和重新启动条件，避免下一轮重复调研。
- “已关闭”只有关闭条件发生变化才能重新打开。
- “已实现”不重复写成待办；后续缺陷使用新的、可验收的问题描述。
- 新能力优先复用 Pydantic AI Core，其次复用 Harness 公开 API，最后才由 DeepAgent 实现最小产品逻辑。
- 涉及 user、Agent、App、Space、SQL 历史、SSE、Checkpoint、MCP 配置和 UI Pending 的部分始终需要产品层边界。

## 待实现

当前没有可以脱离外部条件直接进入开发的主线。诊断中心、OTLP 和 Evaluation均已移入“暂缓”，
必须先满足各自的重新启动条件。

### 近期动作（非功能开发）

1. 下次 Test Pod真正重启或滚动发布后，确认三个上传 Skill无需人工 `reload`即为 `ready`，且原
   ZIP仍可下载；这是对 v0.3.23恢复链路的部署复核，不创建新版本需求。
2. 在真实 DeepCube/DeepTable业务中继续观察 `read_file`、Skill渐进披露和MCP快照；只有出现可稳定
   复现的语义、成本或可靠性问题，才新建可验收缺陷，不再为边缘猜测预埋兼容分支。

## 有潜力

以下事项不是近期承诺。只有“触发条件”真实发生后，才重新检查固定框架版本和产品边界。

### 工具治理与输出

| ID | 潜力方向 | 优先复用 | 触发条件 | 当前边界 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-003` / `TOOL-106` / `TOOL-110` | 工具风险元数据、执行审批和参数/结果 Guardrail | Core `ApprovalRequiredToolset`、Harness `ToolGuardrail`、Core Deferred | 出现真实写入、删除、外发或高风险代码执行工具 | 业务服务权限、审批记录、TTL、操作人、幂等和审计仍由 DeepAgent/业务系统负责 | [工具治理](05-tool-optimization/tool-optimization-design.md#94-guardrails) |
| `TOOL-109` / `ART-001` / `ART-001b` / `ART-101` / `ART-201` | 大工具结果外置与 Artifact | Harness `ToolOutputLimits`、`OverflowStore`、`MediaStore` | 大结果稳定重复占用上下文，且跨 Pod 共享 Store 已选定 | 句柄不可猜、user/App/Space 授权、TTL、清理、History 查看与 Console 提示必须完整 | [大工具结果治理](05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits-overflowingtooloutput) |
| `TOOL-105` | 可选结构化最终输出 | Pydantic AI `ToolOutput`/`NativeOutput`/`PromptedOutput` | 产品锁定普通文本与多输出组合、混合工具调用和严格模式规则 | Output 不是 ToolPackage；权威 MCP 事实不由模型重写 | [结构化最终输出](09-structured-output/structured-output-design.md) |
| `TOOL-108` | Agent 包内逐工具授权 | Core `FilteredToolset` 或 Prepared Toolset | 出现明确的“同包内部分工具禁止使用”权限需求 | 授权主键不能直接依赖可重命名的协议名 | [工具包后续演进](11-tool-packages/tool-packages-design.md#12-后续演进) |
| `TOOL-301` | Provider Native Code/File/Web/Computer 等工具 | Core Provider Native Capability | 某托管 Provider 能力、模型和执行环境成为明确产品功能 | 必须显式启用并按 Provider 治理；不替代公共 MCP、Sandbox、附件、RAG 或当前页面 UI Tool | [浏览器与页面操作](15-browser-and-page-actions/browser-and-page-actions-design.md#23-provider-原生-computer-use) |
| `SKILL-101` | Skill 修订、签名、Marketplace 与便携导出 | Harness `Skills` 继续负责渐进披露；产品层独立治理安装、来源与迁移 | 出现回滚、跨环境迁移、第三方签名或 Marketplace 的明确需求 | 不预埋半套 Revision；便携导出需包含平台原包而非仅 File ID | [Skill 后续边界](12-agent-skills/skill-system-design.md#11-后续边界) |

### 上下文、记忆和执行恢复

| ID | 潜力方向 | 优先复用 | 触发条件 | 当前边界 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `CTX-102` | 压缩后搜索原始会话 | Harness `ConversationSearch` + 自定义 `HistorySource` | 摘要后确实出现路径、ID、决定无法恢复的问题 | 只读现有 `chat_messages`；不启用第二套 `StepPersistence` 历史 | [Harness 后续采用项](03-context-compaction/context-compaction-research.md#harness-018-后续采用项) |
| `CTX-201` | 跨会话长期记忆 | Harness `Memory` + 产品 Store | 产品明确需要可查看、可编辑、可删除的长期笔记 | namespace 不是授权；必须补 user/Agent/App/Space、审计和删除 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |
| `RUN-201` | 长任务崩溃恢复与副作用账本 | Harness `StepPersistence` 或 Core Durable Execution | 长工具/工作流需要进程崩溃后继续且业务幂等已定义 | 不替代 SQL Session/History、SSE 或 Deferred Pending | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |
| `RUN-202` | 结构化计划、子 Agent 与动态工作流 | Harness `Planning`、`SubAgents`、`DynamicWorkflow` | 出现稳定长任务分工、计划 UI 或并行执行需求 | 子 Run 隔离、用量、工具继承、权限和结果展示必须先设计 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |
| `RUN-203` | 长 Run 指令淡化修复 | Harness `SystemReminders` | 有测试证据表明长工具循环稳定遗忘关键系统约束 | 只重申少量规则，不用于掩盖 Prompt 或工具设计问题 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |

### 模型、用量和平台能力

| ID | 潜力方向 | 优先复用 | 触发条件 | 当前边界 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `USAGE-101` | 用户/App/Space 周期配额与收费 | Core `UsageLimits` + Harness `SpendLimits` + 共享计数 Store | 出现正式配额、套餐或计费需求 | 当前 Run 请求数/Token 上限继续保留；不加 80% 预警；私有模型可按 Token/调用量而非美元 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |
| `MODEL-201` | Agent 显式模型回退 | Core `FallbackModel`/模型选择 | 有明确 SLA、候选模型和数据出境策略 | 禁止平台静默全局回退 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#43-有潜力按需求触发) |
| `MODEL-202` | 跨 Provider Thinking 统一配置 | Core `Thinking` + `ModelProfile` | 至少两个 Provider 需要统一可配置推理等级 | 同步模型目录、SSE、History 和 Console；不重建 DeepSeek 特例 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#43-有潜力按需求触发) |
| `MODEL-203` | 辅助模型复核 | Harness `Advisor` + 现有辅助模型配置 | 出现明确复核流程、预算和候选模型 | 不允许模型自由选择未授权外部模型 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |

### 附件、Console 和业务 UI

| ID | 潜力方向 | 触发条件 | 当前边界 | 来源 |
| --- | --- | --- | --- | --- |
| `ATT-201` / `ATT-202` | PDF/PPT/PPTX专用文档解析、异步解析、结构索引和缓存 | 出现需要理解页面、幻灯片、图片与复杂版面的真实业务需求 | 单独建设文档解析工具和结果协议；不塞进普通 `read_file`，也不伪装成模型原生模态；需要资源隔离、租户配额和大结果外置 | [附件解析后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `UI-201` | 当前 DeepFOS 页面动态业务工具 | 至少两个业务页面需要按请求声明工具，且静态 UI 包无法合理配置 | 复用 `ExternalToolset` + Deferred；只允许 `web__` 白名单业务动作，不开放 CSS Selector、任意脚本或第二套聊天事实源 | [浏览器与页面操作](15-browser-and-page-actions/browser-and-page-actions-design.md#51-deepfos-页面操作沿用-ui-tool) |
| `WEB-101` | 外部或未知网站自主浏览 | 出现明确外部网站任务，并锁定域名、登录态、资源和审批边界 | 优先复用 Harness `BrowserUse`；默认关闭、首版只读、隔离 Chromium，不连接用户当前浏览器 | [浏览器与页面操作](15-browser-and-page-actions/browser-and-page-actions-design.md#52-外部网站独立-browseruse-能力) |

## 暂缓

这些事项已有价值判断和技术结论，但当前不值得进入近期开发。重新启动时只复核触发条件，不再从头调研。

| ID | 暂缓事项 | 已确认结论 | 重新启动条件 | 来源 |
| --- | --- | --- | --- | --- |
| `CONSOLE-201` | 只读运行检查器剩余能力 | Console 已能查看 Context Reference 安全 JSON；Markdown 原文、原始 SSE、History 对照和工具详情主要服务 UI/协议调试 | 调试渲染或 SSE/History 一致性成为高频工作，并能锁定最小只读范围 | [运行检查器](02-operations-console/operations-console-design.md#运行检查器) |
| `MODEL-101` | 显式 Provider Prompt Cache | DeepSeek 自动缓存无需改请求，命中观测并入 `OBS-101`；`CachePoint`、cache key、TTL 均是 Provider 专属配置，不存在跨 Provider 通用开关 | 模型目录中的某个 Provider 明确支持显式缓存，且能观测成本或首 Token 延迟收益 | [Pydantic AI 采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `OBS-201` / `CONSOLE-301` | OTLP Trace 与 Console 诊断中心 | v0.3.17 已有统一 conversation/run ID 和脱敏结构化日志；在线诊断需要公司观测平台，不把高频 Span 塞入业务 MySQL | 公司确认 OTLP 后端、保留周期、权限和跨实例查询接口 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#42-下一步优先采用) |
| `EVAL-001` | Agent 回复与工具调用质量评估 | 评估应建立在可查询的真实 Trace 上，分开回答质量、工具选择、参数、结果利用、副作用和成本，不同步阻塞用户请求 | OTLP/诊断数据源确定，并明确首批离线评估集、评分责任和抽样策略 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#42-下一步优先采用) |

## 已关闭

这些结论不再作为开发候选。重新打开时必须先说明哪个关闭条件已经改变。

| ID/主题 | 关闭结论 | 重新打开条件 | 来源 |
| --- | --- | --- | --- |
| `TOOL-002` | 不在公共层强制校验 UI Deferred `result_schema` | 业务出现必须统一强约束且无法在恢复层校验的真实需求 | [UI 工具边界](01-ui-tools/ui-tools-history.md#结果-schema-边界) |
| `TOOL-101` | 不为美化名称重写 MCP 工具命名空间 | 上游确有重复/非法协议名且能提供完整迁移方案 | [MCP 命名](05-tool-optimization/tool-optimization-design.md#5-mcp-工具命名优化) |
| `TOOL-102` | 不把通用 `PrepareTools` 当独立产品项目 | 具体过滤需求出现时直接在该功能内使用 Core/Prepared Toolset | [请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-104` | 不给 MCP 虚构 `display_name`，模型、SSE、History、Console 原样展示最终协议名 | MCP 标准新增可靠展示字段且产品明确要展示 | [Toolset 命名](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |
| `TOOL-202` | 不把 `CombinedToolset` 做成产品概念 | 无；它继续作为运行时组合实现细节按需使用 | [Toolset 组合](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |
| `MODEL-102` | 不接入 `RaiseContentFilterError` | 真实 Provider 出现 `content_filter` 被误保存为正常完成 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#44-明确不采用或不默认采用) |
| 图片文件工具 | 图片只走当前轮Provider原生多模态附件，不进入 `read_file`；历史和Skill图片不读取 | 无；PDF/PPT/PPTX专用解析已单列为潜力方向 | [附件解析](06-attachment-parsing/attachment-parsing-design.md) |
| `CTX-103` | Harness `SummarizingCompaction` 不整体替换 DeepAgent 压缩器 | 公开 API 能覆盖工具 Schema 预算、摘要上限、主动 SSE、目标复检和 Checkpoint 事务 | [压缩复用边界](03-context-compaction/context-compaction-research.md#harness-018-原生压缩复用边界) |
| `ART-102` | 已并入 Artifact/大工具结果主题，不保留重复任务 | 无 | [Artifact 设计](08-artifact-export/artifact-export-design.md) |
| `UI-MCP-DISCOVERY` | 不从 MCP 自动生成 DeepAgent UI Tool | MCP 出现稳定、可验证且满足产品权限的 UI 标准 | [UI 工具历史](01-ui-tools/ui-tools-history.md) |
| `WEB-LEGACY` | 不整体合并远端 `feat/web-capabilities` | 不重新打开；只允许按真实需求提取请求级声明、命名空间、审批等单个思想 | [旧 Web 分支审查](15-browser-and-page-actions/browser-and-page-actions-design.md#4-远端-featweb-capabilities-审查) |
| Provider 原生压缩叠加 Checkpoint | 两层历史改写禁止同时启用 | 未来选择完全替换且通过黑盒等价验证 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#44-明确不采用或不默认采用) |
| Harness `StepPersistence` 替换 SQL History | 禁止建立第二个聊天事实源 | 无；只允许未来用于执行恢复/副作用账本 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#54-明确不采用或不作为平台默认能力) |
| Harness `CapabilityCreation` | 不允许模型在主服务写入并导入 Python 能力 | 无；第三方扩展必须走受审安装和隔离执行 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#54-明确不采用或不作为平台默认能力) |
| Harness `CodeMode`、`FileSystem`、`Shell` 作为默认能力 | 不扩大主服务代码/文件/命令执行面 | 新建独立隔离 Backend 并形成明确产品需求 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#54-明确不采用或不作为平台默认能力) |
| Harness `ManagedPrompt` 替换 Agent Prompt | 不建立数据库以外的第二个 Prompt 事实源 | 未来整体迁移 Prompt 配置治理 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#54-明确不采用或不作为平台默认能力) |
| 静默全局模型回退 | 禁止自动跨模型、跨地域和跨成本回退 | 只允许重新设计为 Agent 显式配置 | [框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#44-明确不采用或不默认采用) |
| Run Token 80% 告警 | 不增加缺少明确用户动作的阈值告警 | 若未来配额产品定义了明确通知和处置动作 | [用量治理](14-framework-capabilities/framework-capabilities-baseline.md#53-有潜力按需求触发) |

## 已实现

这里只记录稳定能力和关键版本，不再把它们混入未完成清单。

| ID/能力 | 已实现结论 | 版本/依据 |
| --- | --- | --- |
| `MODEL-CATALOG` | 数据库模型目录、刷新、上下文窗口和最大输出配置 | v0.3.0+，[模型目录](04-model-catalog/model-catalog-design.md) |
| `TOOL-107` | ToolPackage 目录、UI 工具包、运行 Toolset 和 Console 概念统一 | v0.3.2，[工具包设计](11-tool-packages/tool-packages-design.md) |
| `CTXREF-001` | `context_refs` 协议、持久化、模型投影、历史和压缩协同 | v0.3.5，[上下文引用](10-context-reference/context-reference-design.md) |
| `CTXREF-101` | DeepCube `deepcube.editor_context@1` 与 DeepTable Page/Range Context 已迁移结构化 `context_refs`，历史恢复和业务 Prompt 已适配 | 2026-08 跨仓代码与测试核验，[上下文引用实施阶段](10-context-reference/context-reference-design.md#18-建议实施阶段) |
| `CTX-COMPACTION` / `CTX-101` | 自动/主动单次摘要、Checkpoint、Hash/CAS、取消、Local/Native 工具配对和六段摘要 | v0.3.16，[上下文压缩](03-context-compaction/context-compaction-design.md) |
| `ATT-V1` / `ATT-001` | TXT/DOCX/XLSX 分页读取、会话附件限制、历史和压缩回归 | v0.3.15，[附件解析](06-attachment-parsing/attachment-parsing-design.md) |
| `TOOL-103` | 请求级文件工具可见性已演进为统一 `read_file`：有合法会话附件或已选可读 Skill资源时自动注入，无来源时不注入 | v0.3.12首次过滤、v0.3.23统一入口，[请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-201` | Pydantic AI Tool Search、MCP Top 3/Top 5 渐进披露、只读过滤和搜索去重 | v0.3.12～v0.3.14，[工具检索](13-tool-discovery/tool-discovery-design.md) |
| `RUNTIME-CANCEL` | Core `CancellationToken`/`RunCancelled` 正式停止、interrupted History 和 SSE 一致性 | v0.3.10～v0.3.14，[后端运行说明](../../deep_agent/AGENTS.md#正式取消v0310) |
| `MODEL-001` | 模型目录 `max_output_tokens` 进入普通与流式 `ModelSettings.max_tokens` | v0.3.15，[模型目录](04-model-catalog/model-catalog-design.md) |
| `TOOL-004` | 附件和 MCP Resource 的 `ModelRetry`/`ToolFailed` 语义收口 | v0.3.15，[工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `CONSOLE-101` | 单个用户轮次聚合连续 Assistant/工具循环，过程默认折叠、最终答案常显 | v0.3.16，[运维控制台](02-operations-console/operations-console-design.md) |
| `OBS-101` / `TOOL-001` | 官方 Conversation/Run 标识、脱敏结构化生命周期日志、Usage/缓存 Token 和 Console 当前 Run 指标 | v0.3.17，[框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#41-已经采用) |
| `TOOL-203` | `cube_query_data` 与 `dimension_query_members` 选择性披露可信 Return Schema；延迟工具加载前零额外成本 | v0.3.17，[工具返回 Schema](05-tool-optimization/tool-optimization-design.md#84-tool-return-schema) |
| `SKILL-001` | Skill 与 ToolPackage 分离；上传安装、Agent 授权、Harness `load_capability` 渐进披露、References/Assets 分页读取和脚本隔离；每个 Pod仅保留按 code组织的一份当前运行目录；同 Hash替换可恢复丢失的平台原包；管理操作同步、普通 Run本地读取、单 Skill故障隔离 | v0.3.18～v0.3.23；2026-08-12 Test 004真实验收，[Skill 体系](12-agent-skills/skill-system-design.md) |
| `MCP-SNAPSHOT` | URL MCP 检测保存完整工具快照，聊天按快照披露并在首次真实调用时建连；缺快照不阻塞 Run，单 Server 连接失败转为 `ToolFailed` | v0.3.19，[工具检索](13-tool-discovery/tool-discovery-design.md) |
| `CONSOLE-202` | 独立只读系统工具目录按 Skill、文件、MCP工具发现和MCP Resource展示来源、自动注入条件与说明；`runtime:*`只作展示，不进入Agent能力或数据库 | v0.3.21～v0.3.23，[工具包 Console](11-tool-packages/tool-packages-design.md#9-console) |
| `SKILL-102A` | Skill脚本执行：只允许已授权 `.py`、显式JSON inputs/arguments/同Skill资源，固定读取 `outputs/result.json`；选择含脚本Skill并启用 `builtin:sandbox`后，与 `execute_python`共用当前Sandbox | v0.3.22～v0.3.24，[Skill 脚本边界](12-agent-skills/skill-system-design.md#10-skill脚本执行) |
| `FILE-001` / `SKILL-103` | 系统工具 `read_file`统一读取会话附件与Skill文件；只处理文本、DOCX和XLSX，同一Run字节与解析single-flight缓存；图片严格走当前轮原生多模态附件 | v0.3.23～v0.3.24，[附件解析](06-attachment-parsing/attachment-parsing-design.md)、[Skill体系](12-agent-skills/skill-system-design.md) |
| Harness 确定性压缩 tiers | `ClampOversizedMessages`、`ClearToolResults`、`DeduplicateFileReads` 已复用 | 当前基线，[压缩研究](03-context-compaction/context-compaction-research.md#harness-实验结论) |
| 官方 DeepSeek Provider | 使用 Pydantic AI 官方 Provider/Profile，不保留自定义模型循环 | v0.3.9+，[框架能力台账](14-framework-capabilities/framework-capabilities-baseline.md#41-已经采用) |

## 当前明确不排期

“有潜力”和“暂缓”都不是积压任务。当前“待实现”为空，因此本文件没有可脱离外部条件直接进入
开发的下一项主线；下一轮应先由真实需求或重新启动条件选题，不因框架存在能力而继续堆功能。
