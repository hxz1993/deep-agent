# 调用端交付：Python SDK、CLI 和 MCP

[总案](server-implementation-plan.md) · **本页都是阶段 2 的拟议接口**。阶段 1 已可用标准 A2A 客户端，无需等待这些包装。所有入口的发布权限、作用域、Task/Context、回复回执、产物和错误由同一服务裁决。以下包名、命令和域名为示例，尚不可安装；最终分发渠道和最低 Python 版本待确认。

## Python SDK

建议包名 `deepagent-a2a`，依赖锁定范围的官方 `a2a-sdk` 客户端，不要求安装 DeepAgent 服务、Pydantic Harness 或本机 CLI。异步为主，同步包装走独立运行循环，禁止在已有异步循环里隐式阻塞。SDK 从明确配置的发布 Card 解析接口，校验版本、可信 HTTPS 主机和认证方案；不把 Token 跟随重定向发往未知域名。

| 方法 | 输入 | 返回／行为 |
| --- | --- | --- |
| `discover(publication)` | Agent 编码或已明确允许的 Card URL | `AgentCard`，版本不支持或 Card 不匹配报显式错误。 |
| `upload(path, media_type=None)` | 本地文件路径、允许媒体类型 | 有效期内的 `InputFileRef`，绑定调用者和内容摘要；流式读本地文件。上传是 DeepAgent 扩展。 |
| `submit(publication, *, message_id, text=None, data=None, files=(), context_id=None, reference_task_ids=(), output_modes=())` | 业务输入和去重 ID | 以 `returnImmediately=true` 返回 `Task`。发送前生成或要求调用者持久保存消息 ID。 |
| `get(task_id, *, history_length=None)` / `list(context_id=None, state=None, page_size=20, cursor=None)` | 定位和过滤 | `Task` 或 `TaskPage`。同一 Client 绑定发布与主体；配置多个发布需独立实例。 |
| `watch(task_id)` | 稳定 Task ID | AsyncIterator，首先产出快照，后跟标准化事件；断线时读当前 Task，可在限额内重订阅。跨订阅不承诺逐帧正好一次。 |
| `wait(task_id, *, timeout=120)` | 本地等待期限 | 返回终态或暂停态的最新 Task。超时抛含 Task ID 的 `WaitTimeout`，不取消；可用 SSE，断线回退有退避的查询。 |
| `reply(task_id, *, request_id, message_id, text=None, data=None, files=())` | 当前补材料回执和新消息 ID | 同一 Task；SDK 发送标准 `SendMessage` 加项目回执元数据，不调用内部 UI 接口。 |
| `cancel(task_id)` | 待停止 Task | 最新任务状态；未确认停止就继续查，不能把本地流退出当取消。 |
| `download_artifact(task_id, artifact_id, *, destination, overwrite=False)` | 当前有权的交付 ID | 下载到临时文件，校验长度和 SHA-256 后原子重命名；默认不覆盖。拒绝路径穿越和未批准的下载跳转。 |

```python
# 阶段 2 拟议调用；业务程序自行保存 message ID 与 Task ID。
import os
from pathlib import Path
from deepagent_a2a import DeepAgentClient, WaitTimeout

async def make_report():
    async with DeepAgentClient(
        base_url="https://deepagent.example.com",
        publication="budget-variance-report",
        token_provider=lambda: os.environ["DEEPAGENT_A2A_TOKEN"],
    ) as client:
        await client.discover("budget-variance-report")
        source = await client.upload(Path("budget-input.xlsx"))
        task = await client.submit(
            "budget-variance-report", message_id="finance-batch-0001",
            text="生成预算执行差异报告", files=[source],
        )
        try:
            current = await client.wait(task.id, timeout=90)
        except WaitTimeout as exc:
            return {"task_id": exc.task_id, "continue_with": "get"}
        if current.state == "input-required":
            current = await client.reply(
                task.id, request_id=current.pending.request_id,
                message_id="finance-batch-0001-answer-01", data={"period": "2026-10"},
            )
            current = await client.wait(current.id, timeout=90)
        if current.state != "completed":
            return {"task_id": current.id, "state": current.state}
        report = next(a for a in current.artifacts if a.media_type.endswith("spreadsheetml.sheet"))
        return await client.download_artifact(
            current.id, report.id, destination=Path("预算差异报告.xlsx"),
        )
```

SDK 对 429／明确瞬时网络故障做有界指数退避；提交响应丢失只用同 `message_id` 重试，不自动生成新工作。非幂等配置错误、权限失效、输入格式错误不重试。一次授权刷新只对同主体，失败返回认证错误。等待超时与远端任务失败分别表示。对于完成后追加任务，以原 `context_id` 调 `submit`，可带 `reference_task_ids`，不对终态调用 `reply`。SDK 客户端关闭只关闭自身连接。

## CLI

建议同 wheel 提供 `deepagent-a2a` console script，避免和现有命令冲突；私有 Python 包源或离线 wheel，由部署方选择。支持配置 profile 保存地址、Agent 编码和**凭据来源名**；Token 从环境、受控配置或登录凭据读取，不要求通过命令行参数传密钥。提供 `--format json` 供自动化， stdout 仅一份结果；日志和进度走 stderr。`watch --format jsonl` 逐行输出快照及增量。Ctrl+C 停止本地观察，不隐式停止远端任务。

```bash
# 拟议：包尚未发布
python -m pip install deepagent-a2a
deepagent-a2a --profile finance discover
deepagent-a2a --profile finance --format json submit \
  --message-id finance-batch-0001 --input report-input.json --file budget-input.xlsx
deepagent-a2a --profile finance get task-demo-0065
deepagent-a2a --profile finance --format jsonl watch task-demo-0065
deepagent-a2a --profile finance reply task-demo-0065 \
  --request-id ask-demo-q7m4 --message-id finance-batch-0001-answer-01 \
  --data '{"period":"2026-10"}'
deepagent-a2a --profile finance wait task-demo-0065 --timeout 90
deepagent-a2a --profile finance download task-demo-0065 --artifact report-xlsx --out ./report.xlsx
deepagent-a2a --profile finance cancel task-demo-0066
```

`submit --context-id` 复用对话；`list --state input-required --limit 20` 查看待补材料。可选 `submit --wait --timeout` 默认关闭。单独的 `wait` 不提交新任务。退出码建议：

| 退出码 | 含义 |
| --- | --- |
| 0 | 当前操作成功。普通 submit/get/list 返回远端状态不被当作错误；等待到 completed 或取件成功为 0。 |
| 2 | 参数、配置、响应格式或本地文件错误。 |
| 3 | 未登录、凭据失效或无权。 |
| 4 | 等待超时／待补材料／待授权，保留 task_id 和 `state`/下一步。 |
| 5 | 等待观察到 failed、canceled 或 rejected，JSON 附脱敏错误。 |
| 6 | 连接/服务不可达，可能已受理时附 message_id 供重试恢复。 |
| 130 | 本地 Ctrl+C；不会自动发取消。 |

## MCP 服务端

以环境级 Streamable HTTP 入口 `/api/v1/agent-service/mcp` 提供接入。客户端通过工具列出可用发布，并按 Agent 编码发起调用。远端只获得自己被授权的发布；不允许工具参数自报 owner、企业、内部 session、任意 file_id 或凭据。MCP 身份映射到同一 Task principal；同部署直接调用 Task 服务，独立部署通过官方 A2A 客户端＋可信委托访问，不假冒调用者。工具使用普通 tools/call，初期不要求客户端实现 [MCP Tasks 扩展](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)，也不假定有交互式表单。

| Tool | inputSchema 关键字段 | 行为 |
| --- | --- | --- |
| `submit_task` | required `message_id`；`text/data/files` 至少一个；可选 `context_id, reference_task_ids, wait_seconds` | 接收后立即给 ID；若允许等待，服务限额内等待到暂停/终态，超时仍返回最新状态，不隐藏 Task ID。 |
| `get_task` | required `task_id`；可选 `history_length, wait_seconds` | 返回状态、问题、摘要与交付；避免高频轮询。 |
| `list_tasks` | 可选 `context_id, state, page_size, cursor` | 只列当前授权任务，授权退回不会泄露其他主体数量。 |
| `reply_task` | required `task_id, request_id, message_id`；`text/data/files` 至少一个 | 回答当前问题；成功返回同一 Task。不会接受裸内部工具结果。 |
| `cancel_task` | required `task_id` | 实际停止后才能标已取消。 |
| `get_artifact` | required `task_id, artifact_id` | 返回交付元数据和当前主体可用的授权引用。可配资源读取；不把大文件直接嵌进工具输出。 |

`structuredContent` 是机器读取的产品视图，`content` 含简短可见说明；支持 resource link 的客户端可获得受控 URI。若实现 `resources/read`，按会话与 Task 授权、媒体和大小上限处理；不把全量报表放进模型上下文，也不保证列出所有交付资源。对工具入参/业务失败遵循 MCP tools/call 的错误口径（适用时 `isError=true`），远端 Task 的 failed/canceled 是已成功查询出的状态，不能假装连接错误。任务继续方式放进结构化字段，不依赖模型从多段文案推断。

```json
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{
  "name":"reply_task",
  "arguments":{"task_id":"task-demo-0065","request_id":"ask-demo-q7m4","message_id":"finance-batch-0001-answer-01","data":{"period":"2026-10"}}
}}
```

```json
{
  "structuredContent":{
    "task_id":"task-demo-0065","context_id":"ctx-demo-03","state":"completed",
    "summary":{"period":"2026-10","variance":120000,"currency":"CNY"},
    "artifacts":[{"id":"report-xlsx","name":"预算差异报告.xlsx","media_type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","size":186000}]
  },
  "content":[{"type":"text","text":"报告已完成。用 get_artifact 领取 XLSX。"}],
  "isError":false
}
```

三种客户端均返回统一概念字段：`task_id, context_id, state, pending, artifacts, error`；只有已授权的最终文件进入 artifacts。平台的公开进度、等待回执、错误和过期信息不附内部提示词、工具参数或运行凭据。不同入口之间必须同时满足同一主体、发布及 scope，不能只凭 Task ID 跨协议拿数据。

## 验收和发行

- SDK：干净环境从 wheel 安装，无本机 DeepAgent 依赖，提交、断线重查、补答、取得 XLSX 并打开、认证错误、重复请求幂等。验证兼容的最低 Python 与官方 client 版本。静态类型和 async/sync 生命周期单独测试。
- CLI：shell 端到端验证参数、stdin/JSON、stdout/stderr、退出码、等超时、Ctrl+C、续跑、文件摘要与同名文件。凭据从环境或正式登录态进入，日志不打印。
- MCP：Inspector 或官方客户端先验工具发现和 schema，再用实际 WorkBuddy 或另一个确认的 MCP 客户端验收调用链；支持资源 URI 和无资源读取的客户端分别验证。混合 A2A→MCP 查询同一 Task、MCP→SDK 取件，反向做跨用户拒绝。
- 接入 Skill 只写发现、鉴权来源、submit→wait/get→reply/download、异常、最小例子和格式；安装 Skill 不代表客户拥有服务访问权。包仓、离线制品、文档版本和身份供应方式随阶段 2 决定，本轮无发布。

## 公开类型和最小约束

| 对象 | 字段 |
| --- | --- |
| Task | `id: str, context_id: str, state: TaskState, updated_at: datetime, pending: PendingInput | None, artifacts: tuple[Artifact, ...], error: TaskFailure | None`。公开任务没有内部 run ID。 |
| PendingInput | `request_id: str, kind: clarification | attachment | authorization, prompt: str, schema: dict | None, expires_at: datetime | None`。对普通客户端不要求实现自定义 schema UI；提供文本回执路径。 |
| Artifact | `id: str, name: str, media_type: str, size: int | None, sha256: str | None, expires_at: datetime | None, availability: available | expired | unavailable`。结构化摘要可用内联 data；下载 URL 由授权响应给出。 |
| TaskPage | `items: tuple[Task, ...], next_cursor: str | None, page_size: int, total_size: int`。对外标准 A2A 末页用空字符串，SDK 归一为 None。 |
| TaskFailure | `code: str, message: str, retryable: bool`，只放对调用方可公开的原因。传输异常与任务业务失败分开。 |
| InputFileRef | `url: str, name: str, media_type: str, size: int, sha256: str, expires_at: datetime`，内部存储键不返。MCP 的 `files` 只接收服务认可的引用。 |

CLI 的 `--input` 读取 `{"text":"...","data":{...}}`，与显式 `--text/--data` 不能同时指定；`--file` 可多次使用，先通过同一 SDK 上传并附引用；回复命令不支持省略当下回执。第一版的 `--profile` 包含 `base_url, publication, token_env`，无可用凭据直接退出 3。未明确授权的 URL、超大文件和未支持 MIME 在客户端预检，但后端仍以实际值校验。MCP 的外部工具定义详见[输入 Schema 草案](mcp-tool-inputs.json)，部署限额与校验仍以服务端为准。
