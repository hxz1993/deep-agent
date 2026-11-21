"""Secret-free discovery and usage guidance for published-Agent MCP clients."""

MCP_GUIDE_URI = "deepagent://guides/mcp"

MCP_INSTRUCTIONS = (
    "Discover enabled Agents with list_agents, then call SendMessage with agentCode and a user message. "
    "SendMessage and GetTask wait up to 120 seconds by default. Set configuration.waitSeconds on SendMessage "
    "or waitSeconds on GetTask to change the wait; use 0 for an immediate snapshot. If the task is still running, "
    "call GetTask with the returned id. It can wait again while the same task continues in the background. "
    "If status.state is TASK_STATE_INPUT_REQUIRED, read status.message.parts. "
    "If you have the answer, send it as a new message with the same taskId and a new messageId. "
    "Ask the user only if you need their input. After completion use contextId without taskId for a new task. "
    "Download file parts with HTTP GET to parts[].url using the MCP connection's App/Space and authentication headers. "
    "get_artifact returns download_url by default, or can inline up to 20 MiB as data_base64 with format=base64. "
    f"See {MCP_GUIDE_URI} for examples."
)

MCP_GUIDE = """# DeepAgent 发布 Agent · MCP 接入

在 MCP 客户端配置 DeepAgent 的 Streamable HTTP 地址，传入 App、Space 和一种获授权的鉴权凭据。
用 list_agents 取得发布编码。Task 与 Context 均由服务端生成。

| 工具 | 用途 |
| --- | --- |
| list_agents | 列出已启用的发布及 agent_code |
| SendMessage | 新建任务或向待补充的任务发送消息，configuration.waitSeconds 控制等待秒数 |
| GetTask | 按 id 查任务状态、追问和交付，waitSeconds 控制等待秒数，historyLength 控制历史条数 |
| ListTasks | 按 agentCode 查任务，可筛选 contextId、status、statusTimestampAfter；用 pageToken 翻页 |
| CancelTask | 按 id 请求取消任务 |
| get_artifact | 用 task_id 和 artifact_id 取得文件。默认返回 download_url，可选 format=base64 |

## 发起与续接

```json
{"agentCode":"budget-report","message":{"messageId":"budget-001","role":"ROLE_USER","parts":[{"text":"核对预算"}]}}
```

两种工具默认等待 120 秒，传 0 立即返回。任务完成、失败或进入待补答等需要调用方处理的状态时立即返回。
等到时限仍未完成时，返回原 id 和 contextId，任务在后台继续。可立即用 `{"id":"<Task ID>"}` 调用 GetTask，
也可传 `{"id":"<Task ID>","waitSeconds":30}` 继续等 30 秒。客户端 HTTP 超时需覆盖等待时间和请求开销。

若状态为 TASK_STATE_INPUT_REQUIRED，从 status.message.parts 读取问题。
主控已有答案时可以直接回复，缺信息时再询问用户。补充时用新消息编号和原 taskId。

```json
{"message":{"messageId":"budget-002","role":"ROLE_USER","taskId":"<Task ID>","parts":[{"text":"按十一月口径"}]}}
```

重试原请求时使用相同的 messageId 和消息内容。任务完成后，使用原 contextId 并省略 taskId，创建同一会话内的新任务。
附件放在 parts 中，使用 raw（原始字节的 Base64）、filename 和 mediaType，单次合计8 MiB。

## 结果与文件

完整 Task 同时出现在 structuredContent 和 content 的 JSON 文本中。status.state 是任务状态，
status.message 包含追问或状态说明。artifacts 的 parts 中，text 是正文，data 是业务 JSON，url 是文件下载链接。
ListTasks 的每项可通过 metadata.title 和 metadata.description 识别，includeArtifacts 默认为 false。

用同一连接的 App、Space（如有）和鉴权 Header 对 url 发 HTTP GET。
也可用 get_artifact 获取 download_url。传 format=base64 时，至多20 MiB 的文件在文本 JSON 的 data_base64 中；
更大的文件仍通过 download_url 下载。
"""
