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
| `ATT-001` | 附件解析 | 固化真实附件读取、上下文压缩和 Checkpoint 更新的端到端测试 | 可实施 | 覆盖上传、分页读取、SSE、历史恢复与压缩 | [验证边界](06-attachment-parsing/attachment-parsing-design.md#10-验证边界) |

## P1

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-101` | 工具优化 | 收敛 MCP 单层命名空间并兼容重复前缀 | 待设计 | 不能破坏已有 Agent capability 与历史工具事件 | [MCP 命名](05-tool-optimization/tool-optimization-design.md#5-mcp-工具命名优化) |
| `TOOL-102` | 工具优化 | 使用 `PrepareTools` 做请求级工具过滤 | 待设计 | 先完成工具风险元数据和可观测性 | [请求级过滤](05-tool-optimization/tool-optimization-design.md#81-preparetools) |
| `ATT-101` | 附件解析 | 设计统一文档解析服务，覆盖 PDF/OCR/PPTX 和复杂 Office | 待设计 | 需要评估独立服务、开源解析器和资源隔离 | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |

## P2

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `ATT-201` | 附件解析 | 大文件异步解析、隔离执行和租户级配额 | 待设计 | 依赖统一文档解析服务 | [安全与资源](06-attachment-parsing/attachment-parsing-design.md#12-安全与资源边界) |
| `ATT-202` | 附件解析 | 文件结构索引、跨 Run 缓存和超大结果外置 | 暂缓 | 第一版继续使用单 Run 缓存与上下文压缩 | [后续方向](06-attachment-parsing/attachment-parsing-design.md#13-后续方向) |
| `UI-201` | UI 工具 | 增加前端表单操作工具 | 待决策 | 需先明确宿主页面、动作协议和权限边界 | [表单操作](05-tool-optimization/tool-optimization-design.md#6-前端表单工具设计) |

## P3

| ID | 课题 | 事项 | 状态 | 依赖与验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `TOOL-301` | 工具优化 | 评估结构化输出和 Provider Native Code Execution | 暂缓 | 仅在出现明确业务场景后接入 | [Provider 能力](05-tool-optimization/tool-optimization-design.md#7-code-execution-的接入边界) |
| `TOOL-302` | 工具优化 | 评估 ToolSearch 启用阈值和 `OverflowingToolOutput` | 暂缓 | 以真实工具数量和结果溢出数据为依据 | [Harness 治理](05-tool-optimization/tool-optimization-design.md#9-pydantic-ai-harness-与工具治理) |
| `CTX-301` | 上下文压缩 | 评估 Memory、Usage Ledger 与 StepPersistence | 暂缓 | 仅在出现跨会话记忆、计费或断点恢复产品需求后启动 | [研究结论](03-context-compaction/context-compaction-research.md#暂不引入的能力) |

## 已关闭

| ID | 课题 | 结论 | 状态 | 来源 |
| --- | --- | --- | --- | --- |
| `MODEL-022` | 模型目录 | 数据库目录、手工全量刷新和模型运行属性随 Alembic 022 落地 | 已关闭 | [模型目录设计](04-model-catalog/model-catalog-design.md) |
| `CTX-023` | 上下文压缩 | 主模型摘要、确定性裁剪、Checkpoint 与测试会话随 Alembic 023 落地 | 已关闭 | [上下文压缩设计](03-context-compaction/context-compaction-design.md) |
| `ATT-V1` | 附件解析 | TXT/DOCX/XLSX 按需分页读取与会话附件限制已落地 | 已关闭 | [附件解析设计](06-attachment-parsing/attachment-parsing-design.md) |
| `UI-MCP-DISCOVERY` | UI 工具 | 不采用 MCP UI 自动发现和同步；保留 builtin/manual 两类 | 已关闭 | [UI 工具历史](01-ui-tools/ui-tools-history.md) |
