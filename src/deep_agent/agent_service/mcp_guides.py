"""Secret-free discovery and usage guidance for published-Agent MCP clients."""

MCP_GUIDE_URI = "deepagent://guides/mcp"

MCP_FILE_DELIVERY_HINT = (
    "File parts include an authenticated download URL when available. "
    "For downloadable reports, ask the published Agent to register the file in the caller's DeepFOS Download Center. "
    f"See {MCP_GUIDE_URI} for report delivery and authenticated direct downloads."
)

MCP_INSTRUCTIONS = (
    "Discover enabled Agents with list_agents, then call SendMessage with agentCode and a user message. "
    "SendMessage and GetTask wait up to 120 seconds by default. Set configuration.waitSeconds on SendMessage "
    "or waitSeconds on GetTask to change the wait; use 0 for an immediate snapshot. If the task is still running, "
    "call GetTask with the returned id. It can wait again while the same task continues in the background. "
    "If status.state is TASK_STATE_INPUT_REQUIRED, read status.message.parts. "
    "If you have the answer, send it as a new message with the same taskId and a new messageId. "
    "Ask the user only if you need their input. After completion use contextId without taskId for a new task. "
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

文件产物通过 present 随任务交付。任务 id 与 artifacts[].artifactId 共同标识产物，
文件名、类型和鉴权地址分别见 parts[].filename、parts[].mediaType 和 parts[].url。链接随任务记录失效。
任务结果中的文件引用用于取件。是否能把文件保存为对话附件取决于宿主。

- 用户明确需要一份可下载报告时，优先将「生成报告并登记到当前调用用户的 DeepFOS 下载中心」一起委托给发布 Agent。
  登记成功后，告知用户文件名和领取位置。登记归当前调用身份。若连接使用服务账号，先确认该账号的下载中心是预期领取位置。
  可用性取决于当前用户、App、Space 和平台权限。只得到文件链接或登记失败时，说明文件已生成及实际交付状态。
- 若宿主或脚本能带鉴权，对可信发布服务返回的 parts[].url 发 HTTP GET。使用原 MCP 连接的 app、space（如有）和同一种鉴权凭据。
  由宿主或本地脚本供给凭据，禁止把凭据写进聊天内容或链接。文件字节从下载响应直接写入本地文件。

例如，`artifact_url` 设为已确认的下载地址，私有 Header 文件 `headers_file` 每行写一个 Header。
其中包含 `app`、已配置的 `space`，以及 `X-Deepfos-Key`、`token` 或 `Authorization: Bearer …` 中原连接使用的一种。

```sh
curl --disable --fail --header @"$headers_file" --output report.html "$artifact_url"
```
"""
