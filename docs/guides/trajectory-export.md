# 轨迹与文件导出

Console 工作台和管理员会话诊断的轨迹工具栏提供两种导出：

- **仅轨迹 · JSON**：沿用 `deepagent-session-trajectory` 版本 1，保留对话、思考与工具记录及文件引用，不包含附件字节。运行中也可导出，不增加确认步骤。
- **轨迹与文件 · ZIP**：运行结束或停止、历史读取完成后可用。先显示文件数量与预计大小，确认后下载；关闭弹框或切换会话会取消本次导出，不停止 Agent 运行。

文件数量按 File ID 去重。预估只读取可信会话引用和已知大小，不先下载全部文件；大小缺失会显示未知数量，不能理解成零字节。预估后历史有变化时，需要重新预估并确认；序号与快照摘要共同校验，也覆盖原序号上的事件更新。

## 包内内容与完整性

```text
trajectory.json
manifest.json
files/安全命名的文件
```

`trajectory.json` 是当前 Console 导出的轨迹 JSON，不改变原有格式。`manifest.json` 记录本次会话、事件截止序号、各文件来源与 File ID、包内路径、实际大小和 SHA256，未包含的文件也会逐项说明。文件重名不会互相覆盖，原文件名不直接作为任意 ZIP 路径。

收集范围是用户上传的会话文件，以及已上传并持久化可信文件事实的 Python、Skill、PTC 产物、文档解析证据和大工具结果外置文件。不会扫描模型正文里的任意 ID 或链接，不抓取公网 URL、Skill 安装包或安装目录、脚本临时目录、PDF 临时渲染页图。没有可信 File ID 的旧结果只能保留原引用。

文件已删除、无权限或下载失败时，其他文件仍可导出，界面提示部分完成；必须查看清单，不能把收到 ZIP 等同于全部文件齐全。此包供离线核验和 Coding Agent 排查，**不是 Agent／Skill 安装包，也不是可恢复的配置备份**。

## 接口

路径相对于服务根。普通入口校验当前用户会话归属；管理员入口沿用 Console 管理权限。

| 操作 | 普通入口 | 管理员诊断入口 |
| --- | --- | --- |
| 预估 | `GET /api/v1/sessions/{id}/trajectory-export/preview` | `GET /api/v1/diagnostics/sessions/{id}/trajectory-export/preview` |
| 打包 | `POST /api/v1/sessions/{id}/trajectory-export?include_files=true` | `POST /api/v1/diagnostics/sessions/{id}/trajectory-export?include_files=true` |

普通入口的 `agent_id` 与现有会话接口一致。预估返回 `session_id`、`event_head_sequence`、`snapshot_digest`、`file_count`、`upload_count`、`artifact_count`、`known_size_bytes`、`unknown_size_count`、`can_export`、`message` 和 `limits`。只在可导出且用户确认后提交：

```json
{
  "event_head_sequence": 51,
  "snapshot_digest": "<预估接口返回的摘要>",
  "trajectory": {
    "format": "deepagent-session-trajectory",
    "version": 1,
    "session_id": "demo-session",
    "events": []
  }
}
```

示例中的轨迹需替换为 Console 生成的完整 JSON。服务端不会把提交的 JSON、其文件 ID 或来源声明当作文件授权依据；实际文件来自服务端可信会话历史。`include_files=false` 返回同格式 JSON，不收集文件；Console 的仅轨迹选项仍直接在浏览器下载。

ZIP 响应为 `application/zip`。`X-Deep-Agent-Export-Status` 为 `complete` 或 `partial`，`X-Deep-Agent-Export-Included-Files` 与 `X-Deep-Agent-Export-Skipped-Files` 提供实际数量。调用者应先检查 HTTP 状态和 MIME，不要把错误 JSON 保存成 ZIP。

运行未结束或预估截止序号变化返回 409，需刷新状态／重新预估；资源忙返回 429。包内业务文件不改变原来的文件服务权限。管理员本地文件沿用按可信会话引用读取的规则；平台文件始终使用当前调用者鉴权，不冒用会话所有者。

## 固定保护限制

最多 100 个文件，轨迹 JSON 最多 64 MiB，所有原始内容（JSON 加文件）合计最多 200 MiB，打包最长 120 秒，每个 API Worker 同时一个任务。预估明确超限时不开放 ZIP，仍可仅导出 JSON；实际读取超限或超时时停止继续收集并在清单中说明。若压缩写入也耗尽预算、无法生成完整清单，则返回 504，不交付带截断成员的 ZIP。

打包使用临时目录与后台线程。下载完成、失败、取消及断连后释放请求和临时文件，不创建长期导出任务或可公开访问的下载链接。导出包包含原始业务文件，转交给 Coding Agent 或其他人前应确认接收范围。
