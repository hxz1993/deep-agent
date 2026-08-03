# DeepAgent 设计待办

本文件是设计课题的唯一跨主题待办入口。已实现能力保留在对应设计文档中，不再以未完成
清单形式重复出现。

状态说明：

- `可实施`：边界已明确，可进入开发计划。
- `待设计`：方向明确，仍需补接口或数据方案。
- `待决策`：依赖产品或平台选择。
- `暂缓`：当前明确不进入近期版本。
- `已关闭`：已实现或已确认不做。

## P0

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-001` | 工具优化 | 增加工具定义数量、Schema 字节、调用/重试/失败、耗时和结果大小观测 | 可实施 | 先统一指标口径，不新增计费账本 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `TOOL-002` | UI 工具 | Deferred UI 续跑前，后端按 `result_schema` 校验用户实际提交结果 | 可实施 | Schema 定义校验已存在；需补实际结果校验和错误响应 | [UI 工具历史与边界](01-ui-tools/ui-tools-history.md#仍需补齐的边界) |
| `TOOL-003` | 工具优化 | 建立风险、外部副作用和权限要求的统一元数据契约 | 待设计 | 需同时覆盖 Function、MCP、UI 和附件工具 | [工具质量契约](05-tool-optimization/tool-optimization-design.md#10-工具质量统一契约) |
| `TOOL-103` | 工具优化 | 无合法会话附件时隐藏附件读取工具 | 可实施 | `TOOL-102` 的首个落地子场景；从原始会话和当前请求判断，读取时仍执行文件权限校验 | [附件请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-104` | 工具优化 | 分离 MCP 工具协议名与聊天展示名 | 可实施 | 保留稳定命名空间和历史兼容；Console 使用独立 `display_name` 隐藏冗长前缀 | [Toolset 展示命名](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |
| `ATT-001` | 附件解析 | 固化真实附件读取、上下文压缩和 Checkpoint 更新的端到端测试 | 可实施 | 覆盖上传、分页读取、SSE、历史恢复与压缩 | [验证边界](06-attachment-parsing/attachment-parsing-design.md#10-验证边界) |
| `ART-001` | Artifact 交互 | Artifact 最小语义（登记/元数据/TTL/权限；可不接外部 sink） | 待设计 | 合法 id 不得从 Checkpoint 猜造；存储落点待决 | [08 分期 A0](08-artifact-export/README.md#建议分期先语义后-sink) |
| `ART-001b` | Artifact 交互 | 可选 sink：下载中心 publish（upload + business.save） | 待设计 | 依赖 ART-001；非唯一成功标准 | [设计纲要 S0](08-artifact-export/artifact-export-design.md#32-可选-sink下载中心s0) |
| `CTXREF-001` | 上下文引用 | 实施 `context_refs` 公共协议、持久化、历史返回和模型 Prompt 格式化 | 可实施 | 不新增数据库表；公共外壳严格、payload 宽松、metadata 不发模型 | [上下文引用设计](10-context-reference-and-commands/context-reference-and-commands-design.md) |

## P1

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-101` | 工具优化 | 收敛 MCP 单层命名空间并兼容重复前缀 | 待设计 | 不能破坏已有 Agent capability 与历史工具事件 | [MCP 命名](05-tool-optimization/tool-optimization-design.md#5-mcp-工具命名优化) |
| `TOOL-102` | 工具优化 | 使用 `PrepareTools` 做请求级工具过滤 | 待设计 | 先完成工具风险元数据和可观测性 | [请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `TOOL-105` | 结构化最终输出 | 增加 Agent 可选结构化最终输出 | 已完成设计 | 默认 `ToolOutput`；兼容 Deferred；动态 Schema 需服务端校验和有限重试 | [结构化最终输出设计](09-structured-output/structured-output-design.md) |
| `TOOL-106` | 工具优化 | 为高风险后端工具增加执行前审批 | 待设计 | 依赖风险元数据；覆盖持久化、TTL、权限复检、幂等与审计 | [执行审批](05-tool-optimization/tool-optimization-design.md#第三批高风险工具审批) |
| `TOOL-107` | 工具包 | 统一 Tool Package 目录并支持 UI 工具包 | 实施中 | v0.3.2 整包选择；旧编码仅由 Alembic 002 一次迁移，运行时不保留兼容分支 | [工具包与工具目录](11-tool-packages/tool-packages-design.md) |
| `TOOL-108` | 工具包 | 支持 Agent 在工具包内逐工具授权 | 暂缓 | 先完成整包语义和目录统一；未来扩展选择结构，不复用稳定工具协议名作为授权主键 | [后续演进](11-tool-packages/tool-packages-design.md#12-后续演进) |
| `ATT-101` | 附件解析 | 设计统一文档解析服务，覆盖 PDF/OCR/PPTX 和复杂 Office | 待设计 | 需要评估独立服务、开源解析器和资源隔离 | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `ART-101` | Artifact 交互 | 沙箱大结果晋升为 Artifact（与 07 workdir 合同） | 待设计 | 依赖 ART-001 与 07 阶段 A | [交叉合同](08-artifact-export/README.md#与-07-的交叉合同必须对齐) |
| `ART-102` | Artifact 交互 | （已并入 ART-101）沙箱 `result` 产出 artifact_id | 待设计 | 同 ART-101 | [设计纲要](08-artifact-export/artifact-export-design.md#31-语义主干a0a1) |
| `CTXREF-101` | 上下文引用 | 迁移 DeepCube 与 DeepTable 前端，并补齐对应 Agent 业务 Prompt | 可实施 | 先交付 CTXREF-001；DeepTable 不再使用 DeepCube 助手做业务验收 | [实施阶段](10-context-reference-and-commands/context-reference-and-commands-design.md#18-建议实施阶段) |

## P2

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `ATT-201` | 附件解析 | 大文件异步解析、隔离执行和租户级配额 | 待设计 | 依赖统一文档解析服务 | [安全与资源](06-attachment-parsing/attachment-parsing-design.md#12-安全与资源边界) |
| `ATT-202` | 附件解析 | 文件结构索引、跨 Run 缓存和超大结果外置 | 暂缓 | 第一版继续使用单 Run 缓存与上下文压缩；**出站大结果见 `ART-*`** | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `ART-201` | Artifact 交互 | 配额、审计、共享存储与 Harness Spill 对照 | 暂缓 | 多实例与运维就绪后 | [分期 G0](08-artifact-export/README.md#建议分期先语义后-sink) |
| `UI-201` | UI 工具 | 增加前端表单操作工具 | 待决策 | 需先明确宿主页面、动作协议和权限边界 | [表单操作](05-tool-optimization/tool-optimization-design.md#6-前端表单工具设计) |
| `TOOL-201` | 工具优化 | 评估 `ToolSearch`、`DeferredLoadingToolset` 和 `OverflowingToolOutput` | 暂缓 | 有潜力；达到真实工具数量、Schema Token 和结果溢出阈值后试点 | [ToolSearch](05-tool-optimization/tool-optimization-design.md#82-toolsearch) |
| `TOOL-202` | 工具优化 | 评估以 `CombinedToolset` 统一包装跨来源工具 | 暂缓 | 仅在多个工具包需要共享过滤、审批或延迟加载策略时采用 | [Toolset 组合](05-tool-optimization/tool-optimization-design.md#83-toolset-组合与展示命名) |

## P3

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-301` | 工具优化 | 评估 Provider Native Code Execution 与 File Search | 暂缓 | 仅作为明确支持的托管 Provider 增强，不替代公共 Sandbox/RAG 能力 | [Provider 能力](05-tool-optimization/tool-optimization-design.md#7-code-execution-的接入边界) |
| `CTX-301` | 上下文压缩 | 评估 Memory、Usage Ledger 与 StepPersistence | 暂缓 | 仅在出现跨会话记忆、计费或断点恢复产品需求后启动 | [研究结论](03-context-compaction/context-compaction-research.md#暂不引入的能力) |

## 已关闭

| ID | 课题 | 结论 | 状态 | 来源 |
| --- | --- | --- | --- | --- |
| `MODEL-CATALOG` | 模型目录 | 数据库目录、手工全量刷新和模型运行属性已落地 | 已关闭 | [模型目录设计](04-model-catalog/model-catalog-design.md) |
| `CTX-COMPACTION` | 上下文压缩 | 主模型摘要、确定性裁剪、Checkpoint 与测试会话已落地 | 已关闭 | [上下文压缩设计](03-context-compaction/context-compaction-design.md) |
| `ATT-V1` | 附件解析 | TXT/DOCX/XLSX 按需分页读取与会话附件限制已落地 | 已关闭 | [附件解析设计](06-attachment-parsing/attachment-parsing-design.md) |
| `UI-MCP-DISCOVERY` | UI 工具 | 不采用 MCP UI 自动发现和同步；保留 builtin/manual 两类 | 已关闭 | [UI 工具历史](01-ui-tools/ui-tools-history.md) |
