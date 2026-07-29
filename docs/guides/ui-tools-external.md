# UI 工具使用与扩展指南

> 本文描述当前有效机制。MCP UI 工具发现和同步协议已在数据库版本 `019` 移除。

## 能力边界

UI 工具用于在 Agent 运行过程中暂停并等待用户确认、选择或填写表单。它们作为 Pydantic AI `ExternalToolset` 进入模型工具列表，调用后通过 Deferred HITL 返回 pending 状态；用户提交结果后，DeepAgent 再续跑同一会话。

当前只有两种来源：

| 来源 | 保存位置 | 配置方式 |
| --- | --- | --- |
| `builtin` | DeepAgent 代码 | 系统预置、只读，由 Agent 显式选择 |
| `manual` | `ui_tool_definitions` | Console 手工创建和维护 |

不再支持：

- 从 MCP `ui_tools_list` 发现 UI 工具。
- 从 `ui://tools/manifest` Resource 同步 UI 工具。
- MCP UI 工具刷新间隔、自动同步和手工同步接口。
- `source=mcp` 的 UI 工具定义。

MCP Resource 是另一项独立能力，用于读取 MCP 暴露的静态上下文，不用于注册 UI 工具。参见 [`mcp-resources.md`](mcp-resources.md)。

## 内置工具

### `builtin__confirm`

展示通用的继续/取消界面，并暂停运行等待用户操作。它不是后端强制审批或权限校验机制。

参数：

- `message`：必填，需要用户确认的内容。
- `title`：可选标题。
- `confirm_label`：确认按钮文案，默认“确认”。
- `cancel_label`：取消按钮文案，默认“取消”。

结果固定为 `{ "confirmed": true }` 或 `{ "confirmed": false }`。

### `builtin__choice`

展示候选列表并暂停运行，支持单选、多选以及候选外自由输入。

参数：

- `prompt`：必填，需要用户回答的问题。
- `options`：至少一个候选项；支持字符串，或 `{ "value", "label", "description" }` 对象。
- `title`：可选标题。
- `allow_multiple`：是否允许多选，默认 `false`。
- `custom_label`：自由输入项文案，默认“其他（自行输入）”。
- `custom_placeholder`：自由输入框占位提示，默认“请输入其他内容”。
- `submit_label`：提交按钮文案，默认“提交”。

自由输入入口始终显示。结果统一为：

```json
{
  "selected": ["option-value"],
  "custom_input": null
}
```

仅填写自由输入时，`selected` 为空数组，`custom_input` 保存用户输入；多选模式允许候选项和自由输入同时提交。

两个内置工具在 Console 中只读展示，不能修改或删除；它们不会自动随 Agent 启用。

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

内置和手工工具都不会自动对所有 Agent 生效。编辑 Agent 时，在统一能力列表中选择：

```text
ui:builtin__confirm
ui:builtin__choice
ui:deep_cube__clarify
```

运行时只有同时满足以下条件才会进入模型工具列表：

1. UI 工具 `enabled=true`。
2. Agent `capabilities` 包含精确的 `ui:<name>` 引用。

不存在或已停用的引用会被忽略。新建 Agent、`capabilities=null`、`capabilities=[]` 以及未显式包含新 capability 的历史 Agent，均不会加载这两个内置工具；旧的 `ui-builtin:*` 引用也不会被兼容或迁移。

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
