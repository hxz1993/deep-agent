# 44 · 智能输出与 Agent 对外调用

> 2026-11-09 整理。记录两类接入需求和接口建议，待产品确认。

| 需求 | Python 组件调用示意 | 归属 |
| --- | --- | --- |
| 智能输出 | `deepagent.generate(messages=…, model=…, output_schema=…)` | 业务系统在 Python 脚本中维护系统提示词、用户输入和需要的输出格式。DeepAgent 统一接入模型、校验输出、记录用量。 |
| 运行已配置的 Agent | `deepagent.invoke("approval_review", inputs=…)` | DeepAgent 维护 Agent 的模型、提示词、Skill 和工具。Python 只提交单据或参数，接收业务结果。 |
| 发布正式工具给内网 Agent | 通过 MCP 调用 `approval_review`，Python 可继续使用同一能力的 `invoke()` | 管理员发布稳定工具名，绑定 Agent、入参与出参 Schema，以及调用范围。HTTP 与 MCP 共用同一服务端执行和授权逻辑。 |

## 现有接口与首版边界

现有 `POST /api/v1/chat` 可按 `agent_id` 执行已配置的 Agent，返回 `output`、`session_id` 和 `run_id`。请求的 `message` 是用户消息，系统提示词及结构化输出配置来自 Agent。可以先用它联调固定 Agent。完整请求级系统提示词、面向调用方的稳定能力编码以及 MCP 发布需要新增设计。见 [ChatRequest](../../../src/deep_agent/schemas/chat.py)、[非流式入口](../../../src/deep_agent/api/v1/chat.py#L256)、[结构化输出](../../guides/structured-output.md)。

- 智能输出默认按单次生成处理。模型来自服务端已授权的配置，Python 传本次完整提示词和可选输出定义。确有现有 OpenAI SDK 仅换地址的需求时，为生成入口增加兼容适配，并单独明确兼容字段及流式范围。
- 已配置 Agent 的正式能力由服务端按发布编码绑定。输入可以先是问题文本，也可以是按业务定义的参数对象。返回使用已接受的结构化输出和追踪 ID，文件沿用授权引用。请求端无需提交内部 Agent ID、系统提示词或工具权限。
- 内网调用者身份与业务用户身份分别处理。服务权限按发布能力授予。若要按员工权限查询数据，通过可信网关或委托机制传递身份。调用者身份校验与 [42 · 企业集成](../42-enterprise-auth-adapters/README.md) 中的下游账号映射和取票各自承担不同职责。
- 首版以直接返回最终结果为主，先验证只读且不依赖前端工具的 Agent。Python 组件现有异步脚本可在后台等待 DeepAgent，调用方按脚本任务 ID 查询。DeepAgent 自己的持久任务、补材料、取消与重试语义在有独立需求时再评审。

## 对外发布形式

Dify 的已发布应用可分别提供服务 API 与 MCP 入口。DeepAgent 可采用同样的分层思路，在 Agent 详情提供“发布为工具”，先定义工具用途、参数、结果和授权。接入方通过 MCP 发现和执行工具。只有调用时机、缺材料时怎么追问、如何消费结果等业务约定需要额外指导时，再提供调用方 Skill。见 [Dify 服务 API](https://docs.dify.ai/en/api-reference/guides/get-started)、[Dify MCP 发布](https://docs.dify.ai/en/cloud/use-dify/publish/publish-mcp)、[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[Agent Skills](https://agentskills.io/specification)。

如果接入方需要将任务移交给远程 Agent 后再查询、订阅、补充材料或领取产物，可以按需求评估 [A2A](https://a2a-protocol.org/latest/specification/)。一个业务函数只等待最终结果时，HTTP 与 MCP 已能验证首版契约。

建议先用审批意见生成验证 Python 传提示词、拿结构化结果，再用一个只读分析 Agent 验证 HTTP 和 MCP 同输入、同校验、同权限。业务分析工作台如何复用该调用层，见 [43 · 中间产物管理与报告流水线](../43-budget-report-pipeline/README.md)。
