# UI 工具使用与扩展指南

> 本文描述当前有效机制。MCP UI 工具发现和同步协议已在数据库版本 `019` 移除。

## 能力边界

UI 工具用于在 Agent 运行过程中暂停并等待用户确认、选择或填写表单。它们作为 Pydantic AI `ExternalToolset` 进入模型工具列表，调用后通过 Deferred HITL 返回 pending 状态；用户提交结果后，DeepAgent 再续跑同一会话。

当前只有两种来源：

| 来源 | 保存位置 | 配置方式 |
| --- | --- | --- |
| `builtin` | DeepAgent 代码 | 系统预置，默认启用，只读 |
| `manual` | `ui_tool_definitions` | Console 手工创建和维护 |

不再支持：

- 从 MCP `ui_tools_list` 发现 UI 工具。
- 从 `ui://tools/manifest` Resource 同步 UI 工具。
- MCP UI 工具刷新间隔、自动同步和手工同步接口。
- `source=mcp` 的 UI 工具定义。

MCP Resource 是另一项独立能力，用于读取 MCP 暴露的静态上下文，不用于注册 UI 工具。参见 [`mcp-resources.md`](mcp-resources.md)。

## 内置工具

### `confirm_action`

请求用户确认或取消。常用参数为 `message`、`confirm_label` 和 `cancel_label`。

### `ask_user_choice`

请求用户单选或多选。常用参数为 `prompt`、`options` 和 `allow_multiple`。

内置工具始终随 Agent 可用，Console 中只读展示，不能关闭或删除。

## 手工工具

在 Console 的“配置中心 → UI 工具”创建，或调用：

```http
POST /api/v1/ui-tools
```

示例：

```json
{
  "name": "deep_cube__clarify",
  "description": "向用户展示澄清表单并等待填写",
  "parameters_json_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "fields": {"type": "array"}
    },
    "required": ["title", "fields"]
  },
  "widget": "clarify",
  "enabled": true
}
```

手工工具不会自动对所有 Agent 生效。编辑 Agent 时，在统一能力列表中选择：

```text
ui:deep_cube__clarify
```

运行时只有同时满足以下条件才会进入模型工具列表：

1. UI 工具 `enabled=true`。
2. Agent `capabilities` 包含精确的 `ui:<name>` 引用。

不存在或已停用的引用会被忽略。

## 调用与恢复

```text
模型调用 UI 工具
  → DeepAgent 校验参数
  → SSE 返回 final.pending
  → 会话历史保存 tool_call
  → 前端渲染 widget
  → 用户提交 deferred_tool_results
  → DeepAgent 续跑并保存 tool_result
```

刷新页面后，前端从会话历史恢复“已有 tool_call、尚无 tool_result”的 pending 工具。详细事件格式参见 [`sse-protocol.md`](sse-protocol.md)。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/ui-tools` | 列出内置和手工 UI 工具 |
| `GET` | `/api/v1/ui-tools/{name}` | 查看详情 |
| `POST` | `/api/v1/ui-tools` | 创建手工工具 |
| `PATCH` | `/api/v1/ui-tools/{name}` | 修改手工工具 |
| `DELETE` | `/api/v1/ui-tools/{name}` | 删除手工工具 |
| `GET` | `/api/v1/tools` | 查看 Agent 可配置能力 |

内置工具不能通过修改或删除接口变更。工具名称、描述和参数 Schema 会进入模型上下文，应保持精确、简短，并避免包含敏感信息。
