# 发布 Agent 下一轮开发 · 输出、续接与产物

2026-11-20 · [下一轮开发清单](../TODO.md#下一轮开发--发布-agent2026-11-20)。优先实施发布 Agent 专用输出封装。

## 发布 Agent 输出封装

A2A 和 MCP 请求使用同一个发布专用 `output_type`。原来的 `deepagent_a2a_request_input` 从模型输出选项移除，由统一 Schema 表达完成或待补充。普通 Console、普通客户端、企微和飞书继续走普通聊天输出。将来发布 Agent 的 Python SDK 和 CLI 可复用这一层。Deferred／UI 工具的等待和回传另议。

模型提交 `status`、`message` 和 `reply`。下例是四种独立的模型输出。

```jsonl
{"status":"input_required","message":"要核对哪个月？","reply":null}
{"status":"completed","reply":{"text":"十一月预算差额为 −13 万元。"}}
{"status":"completed","reply":{"data":{"variance":-130000,"currency":"CNY"}}}
{"status":"completed","message":null,"reply":{"text":"十一月预算差额为 −13 万元。","data":{"variance":-130000,"currency":"CNY"}}}
```

- 待补充时，`message` 提供问题文字，`reply.text` 可提供已知情况，`reply.data` 留到任务完成时交付。这样只配置严格业务 JSON 的 Agent 也可以先追问，完成时再按业务 Schema 交付。
- 完成时，纯正文填 `reply.text`，纯结构化填 `reply.data`，结构化加正文时两项都填。`reply.data` 保留业务结构和校验，业务 JSON 以原始对象交付。
- Harness 把问题映射到 Task 的 `status.message`，把最终正文和业务数据分别放到 `artifacts[].parts[].text` 和 `data`。显式交付的文件单独映射为下载引用。任务的排队、运行、取消、失败等由 Harness 依据执行结果记录，Task ID、Context ID、产物 ID 及协议回包也由 Harness 生成或组装。
- 发布输出统一使用一个 Pydantic AI `ToolOutput`，沿用完整响应校验和有限重试。A2A 和 MCP 都将状态、正文、业务数据和随 Run 登记的文件分开组织。
- 会话和轨迹使用折叠输出框，标识为 A2A Output 或 MCP Output，文件卡保持可见。发布任务管理页采用轮次时间线。

## A2A 产物交付

**变更前**，执行层扫描本轮受支持工具的 `tool/results`，把其中登记为 `generated_file` 的文件去重后放入 A2A `artifacts`。工具生成五份合格文件时，调用方会收到五份。MCP 共用这批产物。[A2A 规范](https://a2a-protocol.org/v1.0.1/specification/)

**目标**：模型明确选中要交付的文件，服务端核验会话归属、可用性和下载权限，再将经确认的文件映射到 A2A Artifact。中间文件留在工具结果及历史中供后续步骤使用。有候选但没有交付声明时，文件列表为空。

**交付声明**：普通聊天固定注入 `present(file_ids)`。模型在完成准备和检查后、最终答复前，一次选出本会话中要交付的附件或工具文件。单次 Run 累计最多 10 个不同 ID。声明保存于现有工具结果，成功完成时才发布这份清单。UI 工具等待及其续跑按不同 Run 处理，续跑从新的清单开始。

普通聊天继续使用原有 Markdown 流和业务输出，成功的最终回包额外提供可选的 `output_files`，History 从同一 Run 的工具结果恢复。Console 把文件卡放在回复底部，轨迹只在对应的助手详情里显示卡片。发布 Agent 共用这份显式交付记录。模型提交待补充时也可以声明已准备好的文件。 [文件卡片初版布局原型](<Prototype [DeepAgent 回复产物卡片]/index.html>)

## MCP 追问与返回格式

**变更前**，发布 Agent 通过专用输出提出追问，MCP 将任务置为 `input_required`。调用方用 `reply_task` 携带原 `task_id` 和追问 ID 补答，服务端校验任务状态及追问归属。

MCP 合并 `submit_task` 和 `reply_task` 为 `SendMessage`，沿用异步受理。`message` 的结构和任务回复语义向 A2A 对齐。带原 `message.taskId` 发普通消息即可接续待补充任务，省略时创建新任务。统一入口创建任务时保留有实际路由用途的发布编码。

`INPUT_REQUIRED` 表示原 Task 可接收后续信息。主控可自动提供答案、询问用户，或结束当前调用方流程。回复按标准 `message.parts` 传入，允许一句普通文字。统一工具不设 `reply_to`、`question_id` 或补答专用业务 Schema，服务端不审核答复是否回答了问题。保留协议入参、调用身份及原任务是否可续作的检查。

### 三层返回约定

| 层 | 规则 |
| --- | --- |
| JSON-RPC 2.0 | MCP 两端传输和关联请求的外层报文规范。 |
| MCP 工具结果 | 任务 `structuredContent` 向 A2A Task 字段对齐。列表回包沿用 A2A 的列表字段，按调用需要带历史与产物。 |
| Agent 业务结果 | 由发布 Agent 自身配置决定正文与业务 JSON。A2A 暂缓处理 `acceptedOutputModes`，MCP `SendMessage` 不加该参数。 |

当前 MCP 工具把同一个结果对象写进 `structuredContent`，也把序列化 JSON 放进 `content` 的文本块。对齐时核对现有调用方对工具名和旧回包字段的依赖。 [现有返回组装](../../../src/deep_agent/api/v1/agent_service_mcp.py#L144) · [MCP 工具结果规范](https://modelcontextprotocol.io/specification/latest/server/tools#structured-content)

## 下一轮验收

- 生成五个文件并明确交付一份。A2A 与 MCP 都应只交该份。补测未选、重复引用及无权限文件。
- 发布 Agent 覆盖普通正文、纯业务 JSON、业务 JSON 加正文三种完成结果。严格结构化配置也能返回一句追问，补答后同一 Task 再交付正确的结构化结果。
- 两种入口通过 `SendMessage` 发送普通补充消息，均沿用原 Task ID，无需追问编号。验证 MCP 的异步查询、取消和取件，在实际 MCP 客户端检查回包。
- 核对列表标题、简介、状态消息和时间筛选边界。覆盖 `statusTimestampAfter` 前、等于及后的任务。
- 普通聊天回归核对实时流、刷新后的历史和 Console 回复底部的文件卡一致。旧回包缺附件字段时按空列表展示。
