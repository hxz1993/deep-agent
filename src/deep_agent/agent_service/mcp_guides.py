"""Read-only MCP guides for callers of published Agents."""

MCP_GUIDES = {
    "deepagent://guides/agent-tasks": (
        "任务调用与补材料",
        "发现 Agent、发送任务、查询状态以及继续同一任务的工具示例。",
        """# 调用已发布的 Agent

1. 调用 `list_agents`，从返回的发布列表取得 `agent_code`。
2. 调用 `submit_task` 发起任务。为每条消息生成唯一的 `message_id`，同一条消息重试时沿用原 ID 和内容。
3. 保留返回的 `id`（Task ID）和 `context_id`。用 `get_task` 查询状态和结果，或用 `list_tasks` 查看指定 Agent 的近期任务。

## 发起任务

工具 `submit_task` 示例：

```json
{
  "agent_code": "budget-report",
  "message_id": "example-submit-001",
  "text": "核对这份预算，汇总金额差异。"
}
```

用 `get_task` 传 `{"task_id":"<任务 ID>"}`。若任务需要资料，状态为 `input_required`，响应中会给出补充问题及 `question_id`。

## 补材料或继续追问

在等待补材料的任务上调用 `reply_task`，使用原 Task ID、新 Message ID 和返回的 Question ID：

```json
{
  "task_id": "<任务 ID>",
  "reply_to": "<问题 ID>",
  "message_id": "example-reply-001",
  "text": "请按 11 月月末口径核对。"
}
```

任务完成后，在 `submit_task` 中附上原 `context_id` 继续追问，服务端会创建新任务。要停止进行中的任务，调用 `cancel_task` 并传该 Task ID。执行与查询使用同一应用和调用身份。
""",
    ),
    "deepagent://guides/attachments-artifacts": (
        "附件与取件",
        "发送 Base64 附件、读取返回文件的 MCP Resource 或下载完整文件。",
        """# 随任务发送附件

`submit_task` 和 `reply_task` 都接收 `attachments`。每个附件含文件名、MIME 类型和 Base64 文件字节。单次附件解码后总量最多 8 MiB，并受部署的文件类型、大小和数量设置限制。示例中的 Base64 是演示文字：

```json
{
  "agent_code": "budget-report",
  "message_id": "example-with-attachment-001",
  "text": "请查看附件并生成核对结果。",
  "attachments": [
    {"name": "notes.txt", "media_type": "text/plain", "data_base64": "SGVsbG8="}
  ]
}
```

# 读取返回的文件

1. 用 `get_task` 查看 `artifacts`。文本和结构化结果直接出现在 `parts` 中。
2. 如需取回文件，用 `get_artifact` 传原 `task_id` 和该文件的 `artifact_id`，取得服务端返回的 `resource_uri` 和 `download_path`。
3. 通过 MCP 的 `resources/read` 读取返回的 URI，单个文件上限 1 MiB。也可用 `download_path` 在同一个 DeepAgent 服务上下载完整文件，请求携带原任务的应用和凭据。

产物引用按任务授权，使用工具返回的 URI。任务过期后引用也随之失效。
""",
    ),
}
