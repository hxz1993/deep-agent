# 发布 Agent 下一轮开发 · 最终产物与 MCP 回复

2026-11-20 · 待评审。下一轮优先确认两项对外契约，再决定实现和兼容方式。范围是被调用的发布 Agent。

## A2A 产物交付

**现状**：执行层扫描本轮受支持工具的 `tool/results`，把其中登记为 `generated_file` 的文件去重后放入 A2A `artifacts`。假设工具生成五份合格文件，只有第五份是最终报告，现有收集仍会把五份都交给调用方。MCP 共用这批产物。A2A 规范定义产物怎么表达，选择交付哪份文件由 DeepAgent 决定。 [文件收集入口](../../../src/deep_agent/agent_service/execution.py#L89) · [本轮结果组装](../../../src/deep_agent/agent_service/execution.py#L211) · [A2A 规范](https://a2a-protocol.org/v1.0.1/specification/)

**目标**：模型明确选中要交付的文件，服务端核验会话归属、可用性和下载权限，再将经确认的文件映射到 A2A Artifact。中间文件留在工具结果及历史中供后续步骤使用。有候选但没有交付声明时，文件列表为空。

**交付声明**：普通聊天固定注入 `present(file_ids)`。模型在完成准备和检查后、最终答复前，一次选出本会话中要交付的附件或工具文件。单次 Run 累计最多 10 个不同 ID。声明保存于现有工具结果，成功完成时才发布这份清单。UI 工具等待及其续跑按不同 Run 处理，续跑从新的清单开始。

普通聊天继续使用原有 Markdown 流和业务输出，成功的最终回包额外提供可选的 `output_files`，History 从同一 Run 的工具结果恢复。Console 把文件卡放在回复底部，轨迹只在对应的助手详情里显示卡片。发布 Agent 的 A2A 和 MCP 映射另行接入。 [文件卡片初版布局原型](<Prototype [DeepAgent 回复产物卡片]/index.html>)

## MCP 追问与返回格式

**现状**：A2A 和 MCP 共用执行与任务服务。发布 Agent 通过专用输出提出追问后，MCP 会将任务置为 `input_required`。调用方须用 `reply_task` 携带原 `task_id` 和问题对应字段补答。服务端会校验任务状态及问题归属，所以它现在是一条实际的续跑约束。 [追问状态写入](../../../src/deep_agent/agent_service/execution.py#L204) · [补答校验](../../../src/deep_agent/agent_service/tasks.py#L237) · [MCP 工具定义](../../../src/deep_agent/api/v1/agent_service_mcp.py#L111)

**方向**：MCP 发布 Agent 可以把「你要查哪个月份？」作为本次工具结果中的普通文字交给主控 Agent。主控已有答案时可直接追加一轮，缺信息时由主控问用户。继续对话时评估复用现有 `context_id`，由普通提交进入下一轮。需要在 Console 区分追问时，相关标记仅用于展示。A2A 的补答和任务状态继续按 A2A 协议表达。

### 三层返回约定

| 层 | 规则 |
| --- | --- |
| JSON-RPC 2.0 | MCP 两端传输和关联请求的外层报文规范。 |
| MCP 工具结果 | `content` 可承载自然语言。需要机器读取任务、文件或业务字段时，可以提供 `structuredContent`。工具如果声明 `outputSchema`，结构化结果须遵守该 schema。 |
| Agent 业务结果 | 保留配置好的业务 schema。普通追问可直接写为自然语言。严格结构化模式下如何表达澄清，以及现有客户端如何兼容，需要单独确认。 |

当前 MCP 工具会把同一个结果对象写进 `structuredContent`，也会把序列化 JSON 放进 `content` 的文本块。下一轮确认哪些调用继续依赖控制字段，哪些回复应该直接给主控模型读。先核对 `submit_task`、`get_task` 和 `reply_task` 的现有使用者，再决定如何调整公开契约。 [现有返回组装](../../../src/deep_agent/api/v1/agent_service_mcp.py#L144) · [MCP 基础协议](https://modelcontextprotocol.io/specification/latest/basic) · [MCP 工具结果规范](https://modelcontextprotocol.io/specification/latest/server/tools#structured-content)

## 下一轮验收

- 生成五个文件并明确交付一份。A2A 与 MCP 都应只交该份。补测未选、重复引用及无权限文件。
- MCP 一次返回「你要查哪个月份？」后，分别测试主控已有月份和需要询问用户。验证两种情况下都能用普通下一轮继续，以及原有异步任务查询、取消和取件的兼容性。
- 在一个实际 MCP 客户端检查普通正文、已配置的业务 JSON 和附件下载。核对现有 JSON 消费方是否依赖旧字段及状态。
- 普通聊天回归核对实时流、刷新后的历史和 Console 回复底部的文件卡一致。旧回包缺附件字段时按空列表展示。
