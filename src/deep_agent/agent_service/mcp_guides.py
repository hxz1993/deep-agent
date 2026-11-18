"""MCP initialization instructions and the published Agent guide."""

MCP_GUIDE_URI = "deepagent://guides/mcp"

MCP_INSTRUCTIONS = (
    "Discover published Agents with list_agents, submit a task with submit_task, and read its state and output "
    "with get_task. Keep task_id and context_id. Reuse message_id and the original payload when retrying a send. "
    "For input_required, ask the user the returned question, then call reply_task with the original task_id, "
    "question_id as reply_to, and a new message_id. Use submit_task with the same context_id after completion. "
    "Submit and reply accept attachments as name, media_type, and data_base64, with at most 8 MiB decoded per message. "
    "To save a selected file, send HTTP GET to the download_url from get_task or get_artifact using the connection's "
    "app, space when set, and authentication headers. If your client cannot send those headers, explicitly call get_artifact with "
    "format=base64 for a file up to 20 MiB. Decode data_base64 programmatically from the original text JSON and "
    "save the bytes under name. Files larger than 20 MiB always use the download URL."
)

MCP_GUIDE = """# 发布 Agent MCP 接入

使用同一个 Streamable HTTP 连接调用已发布的 Agent。经 DeepFOS 网关时，配置 `app` 为应用 ID，`space` 为所属空间 ID。
提供一种凭据，可用 `X-Deepfos-Key`、`Authorization: Bearer <Token>` 或 `token`。
`list_agents` 展示已启用的发布，发起任务时核对应用权限和发布范围。

## 工具一览

| 工具 | 用途 | 参数示例 |
| --- | --- | --- |
| `list_agents` | 查找已发布 Agent 和编码 | `{}` |
| `submit_task` | 发起任务，或在已完成的会话里继续追问 | `{"agent_code":"budget-report","message_id":"budget-001","text":"核对预算"}` |
| `get_task` | 查看状态、正文、待答问题和文件下载地址 | `{"task_id":"<Task ID>"}` |
| `list_tasks` | 查近期任务，按状态或会话筛选，使用 `next_cursor` 续页 | `{"agent_code":"budget-report","state":"completed","page_size":10}` |
| `reply_task` | 在原任务上回答待答问题 | `{"task_id":"<Task ID>","reply_to":"<Question ID>","message_id":"budget-002","text":"按 11 月口径"}` |
| `cancel_task` | 请求停止未结束的任务 | `{"task_id":"<Task ID>"}` |
| `get_artifact` | 默认返回一份文件的完整下载地址，支持显式请求 Base64 | `{"task_id":"<Task ID>","artifact_id":"<Artifact ID>"}` |

## 发起、补答与续跑

1. 用 `list_agents` 取得 `agent_code`，再调用 `submit_task`。每条新消息分配一个 `message_id`，重试同一消息时沿用原 ID 和原内容。
2. 保留返回的 `id` 和 `context_id`。用 `get_task` 查询同一个任务直到结束或需要补答。正文和结构化结果在 `artifacts` 的 `parts` 中。
3. 状态为 `input_required` 时，先显示 `message` 并等待用户。用 `reply_task` 传原 Task ID、`question_id` 作为 `reply_to`、新 Message ID 和用户补充的内容。
4. 任务完成后要沿原会话继续，调用 `submit_task` 传原 `context_id` 和新 Message ID，服务端会创建新 Task。

`submit_task` 和 `reply_task` 的 `attachments` 每项传文件名、MIME 类型和客户端编码的原文件字节。
单次解码后总量最多 8 MiB，并受平台文件类型、大小和数量设置限制。

```json
{
  "agent_code": "budget-report",
  "message_id": "budget-files-001",
  "text": "核对随附预算。",
  "attachments": [
    {"name": "notes.txt", "media_type": "text/plain", "data_base64": "SGVsbG8="}
  ]
}
```

## 获取用户选中的产物

从 `get_task` 的 `artifacts` 选取文件，也可用 `get_artifact` 按 ID 查询单个文件。
默认对完整 `download_url` 发 HTTP GET，携带 MCP 连接的原 `app`、`space`（如有）和鉴权 Header。
客户端无法携带这些 Header 时，对至多 20 MiB 的文件显式传 `format="base64"`。
从同一次响应的 `content[].text` 解析 JSON，解码 `data_base64` 并按 `name` 保存。
超过 20 MiB 时始终返回 `delivery=http_download` 和完整下载地址。任务过期后下载链接失效。
"""
