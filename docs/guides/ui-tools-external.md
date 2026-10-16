# UI 工具使用与扩展指南

> 本文描述当前有效机制。MCP UI 工具发现和同步协议已在数据库版本 `019` 移除。

## 能力边界

UI 工具用于在 Agent 运行过程中暂停并等待用户确认、选择或填写表单。它们作为 Pydantic AI `ExternalToolset` 进入模型工具列表，调用后通过 Deferred HITL 返回 pending 状态；用户提交结果后，DeepAgent 再续跑同一会话。

当前只有两种来源：

| 来源 | 保存位置 | 配置方式 |
| --- | --- | --- |
| `builtin` | DeepAgent 代码 | 系统预置、只读，由 Agent 显式选择 |
| `manual` | `ui_tool_packages` + `ui_tool_definitions` | Console 手工创建包并在包内维护工具 |

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

两个内置工具同属 `ui:builtin`。该包和工具在 Console 中只读展示，不能修改或删除；
它们不会自动关联到 Agent。

## 手工工具

先在 Console 的“配置中心 → UI 工具”创建手工工具包，再在包内创建工具。对应接口为：

```http
POST /api/v1/ui-tool-packages
POST /api/v1/ui-tools
```

工具包示例：

```json
{
  "code": "ui:deepcube",
  "label": "DeepCube 助手",
  "description": "DeepCube 专用交互"
}
```

工具示例：

```http
POST /api/v1/ui-tools
```

示例：

```json
{
  "name": "deep_cube__clarify",
  "package_code": "ui:deepcube",
  "description": "向用户展示澄清表单并等待填写",
  "parameters_json_schema": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "fields": {"type": "array"}
    },
    "required": ["title", "fields"]
  },
  "widget": "clarify"
}
```

内置和手工工具都不会自动对所有 Agent 生效。编辑 Agent 时，在统一工具包列表中选择：

```text
ui:builtin
ui:deepcube
```

运行时只有同时满足以下条件才会进入模型工具列表：

1. Agent `capabilities` 包含精确的 `ui:<package>` 工具包编码。
2. 工具 Schema 有效。

创建和更新 Agent 时，不存在的包编码返回 422。`capabilities`是非空列表；空数组或未选择UI包时
不加载 UI 工具。旧的单工具编码只在 Alembic `002` 中一次性转换，运行时不保留兼容分支。
Alembic 010删除工具包和工具自身的`enabled`字段，目录层不再存在第二个运行开关。

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
| `GET/POST` | `/api/v1/ui-tool-packages` | 列出或创建 UI 工具包 |
| `GET/PATCH/DELETE` | `/api/v1/ui-tool-packages/{code}` | 查看、修改或删除手工包；内置包只读 |
| `GET` | `/api/v1/ui-tools` | 列出内置和手工 UI 工具 |
| `GET` | `/api/v1/ui-tools/{name}` | 查看详情 |
| `POST` | `/api/v1/ui-tools` | 创建手工工具 |
| `PATCH` | `/api/v1/ui-tools/{name}` | 修改手工工具 |
| `DELETE` | `/api/v1/ui-tools/{name}` | 删除手工工具 |
| `GET` | `/api/v1/tools` | 查看 Agent 可配置工具包目录 |

内置包和工具不能通过修改或删除接口变更。手工包没有Agent引用时，确认后同时删除工具包与包内全部工具，后端在同一事务执行；失败则整体回滚。
仍被Agent引用的工具包不能删除，接口返回409及关联Agent名称；包中的单个工具也不能删除。删除Agent本身仍保留共享工具资源。
工具名称、描述和参数 Schema 会进入模型上下文，应保持
精确、简短，并避免包含敏感信息。
