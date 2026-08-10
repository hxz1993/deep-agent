# DeepAgent 设计待办

本文件是设计课题的唯一跨主题待办入口。已实现能力保留在对应设计文档中，不再以未完成
清单形式重复出现。

状态说明：

- `可实施`：边界已明确，可进入开发计划。
- `进行中`：已经进入独立开发任务，尚未完成验收和合并。
- `待设计`：方向明确，仍需补接口或数据方案。
- `待决策`：依赖产品或平台选择。
- `暂缓`：当前明确不进入近期版本。
- `已关闭`：已实现或已确认不做。

## 近期主线（v0.3.16 后）

近期研发集中在以下方向：

1. **Console 对话呈现**：独立前端任务正在实施“一轮用户输入对应一轮 Assistant 展示”、过程总折叠、工具默认折叠和最终答案常显；不改变后端 SSE/History 契约。
2. **工具观测**：先完成 `TOOL-001`，为后续风险治理、检索优化和结果外置提供真实指标。
3. **Skill**：以 Harness 0.18 的 `Skills` 渐进披露作为 Instructions 基础，把当前“Instructions + FunctionToolset”的最小 Python Skill 扩展为适合线上安装、版本管理和素材治理的完整能力包。
4. **工具治理**：先建立跨 Function、MCP、UI 和附件工具的风险、副作用与权限元数据，再推进审批、Guardrail 和结果外置。
5. **Output Tool（暂缓）**：0805 实验结论已归档；待多输出组合、普通文本与混合工具调用规则完成产品决策后再重新实施。

工具检索首版已经落地。下一阶段先完成 Console 对话呈现验收和工具观测，再进入 Skill 协议与
渐进披露；Output Tool 保留完整设计和实验证据，但不进入近期代码计划。

## P0

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-001` | 工具优化 | 增加工具定义数量、Schema 字节、调用/重试/失败、耗时和结果大小观测 | 可实施 | 先统一指标口径，不新增计费账本 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `CONSOLE-101` | 运维控制台 | 将连续 Assistant 历史和单次流式工具循环聚合为一个可视回答，过程与工具默认折叠，最终答案常显 | 进行中 | 独立 Console UI 任务实施；保持 no-build 架构，不修改后端 SSE/History，UI Deferred 等待状态仍需可发现和展开操作 | [运维控制台](02-operations-console/operations-console-design.md) |
| `TOOL-002` | UI 工具 | Deferred UI 续跑前是否由公共层按 `result_schema` 强制校验结果 | 已关闭 | v0.3.15 决定不做公共层强校验；`result_schema` 用于前端渲染与协议说明，严格业务结果由业务恢复逻辑校验，副作用参数必须由真正后端工具复检 | [UI 工具历史与边界](01-ui-tools/ui-tools-history.md#结果-schema-边界) |
| `TOOL-003` | 工具优化 | 建立风险、外部副作用和权限要求的统一元数据契约 | 待设计 | 需同时覆盖 Function、MCP、UI 和附件工具，并作为 `ToolGuardrail` 参数复检、结果清理与审批策略的统一输入 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `TOOL-004` | 工具错误 | 按“模型可纠正”与“本次执行已终止”区分 `ModelRetry` 和 `ToolFailed` | 已关闭 | v0.3.15 已收口附件下载、损坏、解析、超限和 MCP Resource 上游失败；UI、Sandbox 与普通 MCP Tool 保持原语义 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `TOOL-103` | 工具优化 | 无合法会话附件时隐藏整个附件读取工具包 | 已关闭 | v0.3.12 已实施；有合法附件时整包披露三个读取工具，调用时仍执行文件权限校验 | [附件请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-104` | 工具优化 | 分离 MCP 工具协议名与聊天展示名 | 可实施 | 保留稳定命名空间和历史兼容；Console 使用独立 `display_name` 隐藏冗长前缀 | [Toolset 展示命名](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |
| `TOOL-105` | 结构化最终输出 | 实施 Agent 可选的多种 Output Tool | 暂缓 | 0805 实验已归档；重新实施前需锁定可选/严格模式、多输出组合和混合调用确定性重试规则 | [结构化最终输出设计](09-structured-output/structured-output-design.md#103-0805-实验实现的归档结论) |
| `TOOL-201` | 工具检索 | 实施 MCP 工具检索与渐进披露机制 | 已关闭 | v0.3.12 已实施；v0.3.14 补齐用户问题优先、只读副作用过滤、已披露排除和单 Run 重复搜索去重；跨 Run 发现状态继续遵循 Pydantic AI | [工具检索设计](13-tool-discovery/tool-discovery-design.md) |
| `SKILL-001` | Skill | 设计并实施线上 Skill 安装、版本管理与渐进披露 | 可实施 | Harness `Skills` 负责 SKILL.md 正文渐进披露；DeepAgent 负责授权、版本、References、Assets、工具与执行隔离 | [Skill 体系](12-agent-skills/skill-system-design.md) |
| `ATT-001` | 附件解析 | 固化真实附件读取、上下文压缩和 Checkpoint 更新的端到端测试 | 已关闭 | v0.3.15 目标回归覆盖 TXT/DOCX/XLSX 分页、条件披露、SSE、历史以及附件与 Checkpoint 原始历史边界 | [验证边界](06-attachment-parsing/attachment-parsing-design.md#10-验证边界) |
| `MODEL-001` | 模型请求 | 将模型目录的 `max_output_tokens` 通过 Pydantic AI `ModelSettings.max_tokens` 应用于普通和流式请求 | 已关闭 | v0.3.15 已实施；未配置时保持 Provider 默认，摘要继续使用独立动态上限 | [模型目录设计](04-model-catalog/model-catalog-design.md) |
| `ART-001` | Artifact 交互 | Artifact 最小语义（登记/元数据/TTL/权限；可不接外部 sink） | 待设计 | 合法 id 不得从 Checkpoint 猜造；存储落点待决 | [08 分期 A0](08-artifact-export/README.md#建议分期先语义后-sink) |
| `ART-001b` | Artifact 交互 | 可选 sink：下载中心 publish（upload + business.save） | 待设计 | 依赖 ART-001；非唯一成功标准 | [设计纲要 S0](08-artifact-export/artifact-export-design.md#32-可选-sink下载中心s0) |

## P1

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-101` | 工具优化 | 收敛 MCP 单层命名空间并兼容重复前缀 | 待设计 | 不能破坏已有 Agent capability 与历史工具事件 | [MCP 命名](05-tool-optimization/tool-optimization-design.md#5-mcp-工具命名优化) |
| `TOOL-102` | 工具优化 | 使用 `PrepareTools` 做请求级工具过滤 | 待设计 | 先完成工具风险元数据和可观测性 | [请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-106` | 工具优化 | 为高风险后端工具增加执行前审批 | 待设计 | 依赖 `TOOL-003`；优先复用 Harness `ToolGuardrail` / Pydantic AI Deferred 审批原语，同时补齐持久化、TTL、操作人、权限复检、幂等与执行审计 | [执行审批](05-tool-optimization/tool-optimization-design.md#第三批高风险工具审批) |
| `TOOL-107` | 工具包 | 统一 Tool Package 目录并支持 UI 工具包 | 已完成 | v0.3.2 整包选择；旧编码仅由 Alembic 002 一次迁移，运行时不保留兼容分支 | [工具包与工具目录](11-tool-packages/tool-packages-design.md) |
| `TOOL-108` | 工具包 | 支持 Agent 在工具包内逐工具授权 | 暂缓 | 先完成整包语义和目录统一；未来扩展选择结构，不复用稳定工具协议名作为授权主键 | [后续演进](11-tool-packages/tool-packages-design.md#12-后续演进) |
| `TOOL-109` | 工具结果 | 使用 Harness `ToolOutputLimits` 外置超长工具结果 | 待设计 | 排在 Skill 之后；先复用 `ART-*` 建立跨 Pod 共享 Store、不可猜句柄、user/App/Space 权限、TTL/清理和审计；History 可授权读取完整结果，Console 显示“内容已外置” | [大工具结果治理](05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits--overflowingtooloutput) |
| `TOOL-110` | 工具治理 | 使用 Harness `ToolGuardrail` 统一工具参数与结果策略 | 待设计 | 依赖 `TOOL-003`；执行前按可信 deps 复检 user/App/Space 与副作用策略，执行后先做敏感信息清理再进入模型、历史或 Spill；审批流程见 `TOOL-106` | [Guardrails](05-tool-optimization/tool-optimization-design.md#94-guardrails) |
| `USAGE-101` | 用量治理 | 增加 Run 与会话 Token 上限和 80% 预警 | 待设计 | Run 优先使用 Pydantic AI `UsageLimits` 的请求数、工具调用数和单请求输入上限；动态窗口预算与压缩仍是上下文事实源；私有模型暂不按美元阻断，用户/App/Space 日预算待 Redis 配额方案 | [Harness 0.18 后续采用项](03-context-compaction/context-compaction-research.md#harness-018-后续采用项) |
| `OBS-101` | 原生观测 | 接入 Pydantic AI `conversation_id`、`run_id` 和可选 Instrumentation | 待设计 | `conversation_id` 使用 Agent/Session 作用域化稳定标识，每次执行生成独立 `run_id`；`include_content=false`，先设计脱敏与导出端；只用于链路关联，不替代 SessionStore、SSE 或业务审计 | [Pydantic AI 2.26 原生能力采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `MODEL-101` | 模型请求 | 按 Provider 能力评估原生 Prompt Cache / `CachePoint` | 待设计 | 仅对明确支持且可观测命中率的模型启用；不得对 DeepSeek 或私有 OpenAI-compatible 端点全局假设支持 | [Pydantic AI 2.26 原生能力采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `MODEL-102` | 模型请求 | 使用原生内容过滤异常区分“被安全策略拦截”和“正常空响应” | 待设计 | 评估 `RaiseContentFilterError` 及各 Provider 映射，统一 REST/SSE 错误协议和 Console 提示，不把过滤结果持久化成正常完成 | [Pydantic AI 2.26 原生能力采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `ATT-101` | 附件解析 | 设计统一文档解析服务，覆盖 PDF/OCR/PPTX 和复杂 Office | 待设计 | 需要评估独立服务、开源解析器和资源隔离 | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `ART-101` | Artifact 交互 | 沙箱大结果晋升为 Artifact（与 07 workdir 合同） | 待设计 | 依赖 ART-001 与 07 阶段 A | [交叉合同](08-artifact-export/README.md#与-07-的交叉合同必须对齐) |
| `ART-102` | Artifact 交互 | （已并入 ART-101）沙箱 `result` 产出 artifact_id | 待设计 | 同 ART-101 | [设计纲要](08-artifact-export/artifact-export-design.md#31-语义主干a0a1) |
| `CTXREF-101` | 上下文引用 | 迁移 DeepCube 与 DeepTable 前端，并补齐对应 Agent 业务 Prompt | 可实施 | DeepAgent v0.3.5 公共协议已交付；DeepTable 不再使用 DeepCube 助手做业务验收 | [实施阶段](10-context-reference/context-reference-design.md#18-建议实施阶段) |
| `CTX-101` | 上下文压缩 | 基于 Pydantic AI 2.26 重新审查窗口预算、摘要请求保护、中断历史修复及自动/主动压缩一致性 | 已关闭 | v0.3.13 已让官方 CancellationToken 覆盖嵌套摘要，并用 Call/Return 基类统一 Local/Native 工具配对；Provider CompactionPart 防空壳、单次摘要与原始历史边界保持不变 | [上下文压缩设计](03-context-compaction/context-compaction-design.md) |

## P2

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `ATT-201` | 附件解析 | 大文件异步解析、隔离执行和租户级配额 | 待设计 | 依赖统一文档解析服务 | [安全与资源](06-attachment-parsing/attachment-parsing-design.md#12-安全与资源边界) |
| `ATT-202` | 附件解析 | 文件结构索引、跨 Run 缓存和超大结果外置 | 暂缓 | 第一版继续使用单 Run 缓存与上下文压缩；**出站大结果见 `ART-*`** | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `ART-201` | Artifact 交互 | 配额、审计、共享存储与 Harness Spill 对照 | 暂缓 | 多实例与运维就绪后 | [分期 G0](08-artifact-export/README.md#建议分期先语义后-sink) |
| `CONSOLE-201` | 运维控制台 | 增加工作台消息检查模式，在右侧按选中对象查看 Markdown 原文、历史结构、上下文引用、工具参数/结果和当前 Run 的 SSE 事件帧 | 待设计 | 历史只读数据与当前 Run 内存事件必须分开；第一版不把完整 SSE 流持久化到数据库，不暴露敏感 Provider 请求 | [运行检查器](02-operations-console/operations-console-design.md#运行检查器) |
| `CTX-102` | 历史检索 | 基于现有 SQL 会话接入 Harness `ConversationSearch` | 待设计 | 实现读取 `chat_messages` 的自定义 `HistorySource`，不启用第二套 `StepPersistence`；默认只检索当前会话，跨会话必须显式设计 user/App/Space 权限与审计 | [Harness 0.18 后续采用项](03-context-compaction/context-compaction-research.md#harness-018-后续采用项) |
| `CTX-103` | 上下文压缩 | 评估以 Harness `SummarizingCompaction` 作为摘要内核 | 已关闭 | v0.3.16 确认不整体迁移：继续复用公开确定性 tiers，自动/主动共用内部投影流程并采用六段增量摘要；Harness 当前缺少工具 Schema 预算、摘要输出上限和 DeepAgent Checkpoint 事务语义，待其接口成熟后重新评估 | [Harness 原生压缩复用边界](03-context-compaction/context-compaction-research.md#harness-018-原生压缩复用边界) |
| `MODEL-201` | 模型请求 | 评估 Agent 显式配置 Pydantic AI `FallbackModel` | 暂缓 | 只能由 Agent 明确选择候选模型，并先锁定成本、数据出境、能力差异和失败切换口径；禁止平台静默全局回退 | [Pydantic AI 2.26 原生能力采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `MODEL-202` | Thinking | 评估 Pydantic AI 原生 Thinking 配置统一跨 Provider 推理参数 | 待设计 | 需与模型目录能力、Console 配置、历史/SSE 展示一起设计；不得重新引入 DeepSeek 专用兼容模型 | [Pydantic AI 2.26 原生能力采用项](03-context-compaction/context-compaction-research.md#pydantic-ai-226-原生能力采用项) |
| `TOOL-203` | 工具结果 | 按工具选择性评估 `IncludeToolReturnSchemas` | 暂缓 | 只用于模型确实需要理解返回结构的少量工具；先测 Schema Token 增量，不对全部 MCP/Function Tool 开启 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `UI-201` | UI 工具 | 增加前端表单操作工具 | 待决策 | 需先明确宿主页面、动作协议和权限边界 | [表单操作](05-tool-optimization/tool-optimization-design.md#6-前端表单工具设计) |
| `TOOL-202` | 工具优化 | 评估以 `CombinedToolset` 统一包装跨来源工具 | 暂缓 | 仅在多个工具包需要共享过滤、审批或延迟加载策略时采用 | [Toolset 组合](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |

## P3

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-301` | 工具优化 | 评估 Provider Native Code Execution 与 File Search | 暂缓 | 仅作为明确支持的托管 Provider 增强，不替代公共 Sandbox/RAG 能力 | [Provider 能力](05-tool-optimization/tool-optimization-design.md#7-code-execution-的接入边界) |
| `CTX-301` | 上下文压缩 | 评估 Memory、Usage Ledger 与 StepPersistence | 暂缓 | 仅在出现跨会话记忆、计费或断点恢复产品需求后启动 | [研究结论](03-context-compaction/context-compaction-research.md#暂不引入的能力) |

## 已关闭

| ID | 课题 | 结论 | 状态 | 来源 |
| --- | --- | --- | --- | --- |
| `CTXREF-001` | 上下文引用 | v0.3.5 已完成 `context_refs` 协议、持久化、模型投影、历史返回和压缩协同 | 已关闭 | [上下文引用设计](10-context-reference/context-reference-design.md) |
| `MODEL-CATALOG` | 模型目录 | 数据库目录、手工全量刷新和模型运行属性已落地 | 已关闭 | [模型目录设计](04-model-catalog/model-catalog-design.md) |
| `CTX-COMPACTION` | 上下文压缩 | 自动/主动主模型摘要、可重复主动尝试、确定性裁剪、Checkpoint、历史标记与测试会话已落地 | 已关闭 | [上下文压缩设计](03-context-compaction/context-compaction-design.md) |
| `ATT-V1` | 附件解析 | TXT/DOCX/XLSX 按需分页读取与会话附件限制已落地 | 已关闭 | [附件解析设计](06-attachment-parsing/attachment-parsing-design.md) |
| `UI-MCP-DISCOVERY` | UI 工具 | 不采用 MCP UI 自动发现和同步；保留 builtin/manual 两类 | 已关闭 | [UI 工具历史](01-ui-tools/ui-tools-history.md) |
