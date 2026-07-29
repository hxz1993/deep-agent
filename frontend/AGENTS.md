# AGENTS.md — 前端

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端 API：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

纯静态 HTML/CSS/JS，**无构建步骤**。后端将 `frontend/console/` 挂载到 `/console`，根路径跳转到 Console。

---

## 文件结构

```
frontend/
├── console/             # 唯一页面：测试工作台 / 配置中心 / 诊断中心
│   ├── index.html
│   ├── chat.js          # SSE、会话、附件、HITL
│   ├── workspace.js
│   ├── configuration.js
│   └── configuration/   # Agent / 模型 / MCP / UI 工具管理
└── vendor/              # marked、DOMPurify、highlight.js 等预构建库
```

---

## 功能概览

| 功能 | 实现 |
| --- | --- |
| 流式对话 | `fetch` + `ReadableStream` 手动解析 SSE（非 `EventSource`，因需 POST） |
| 附件上传 | `POST /api/v1/uploads`（multipart）→ 发送时仅传 `attachments[]` 引用；文档由 Agent 可选 `attachment` 工具按需读取 |
| 思考过程 UI | `thinking` 可折叠块，默认折叠；与 `text` 分离 |
| 工具调用 UI | `tool_call` / `tool_result` 可折叠；同 `tool_call_id` 合并更新 |
| HITL UI 工具 | 内置 `builtin__confirm` / `builtin__choice` + 手工 UI 工具；都由 Agent 显式选择，SSE / history 携带 `widget` / `metadata`；见 [`../docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md) |
| Markdown | marked + DOMPurify + highlight.js |
| 会话 | `session_id` 由后端 `session` 事件返回；`GET/DELETE /api/v1/sessions`；标题展示 `title \|\| user_prompt`（见 [`../docs/guides/experience/ai-title-generator.md`](../docs/guides/experience/ai-title-generator.md)） |
| 能力面板 | `GET /api/v1/tools` |
| 配置 | `localStorage` 只保存调试 App；全局环境配置只读展示，身份和鉴权由平台提供 |
| 一体化控制台 | `/console/`；工作台、配置和诊断的唯一入口 |

## 一体化控制台

`frontend/console/` 是不依赖构建工具的 ES Modules 页面。它复用现有聊天和配置 API，但不读取旧设置页的 `da_request_headers`：

- 平台用户与鉴权只读，通过 `GET /api/v1/request-context` 获取。
- 当前控制台只允许覆盖 `app`；Space 根据标准 App 编码自动去掉末尾三位，Language 固定为 `zh-cn`。
- Console 调试值使用 `X-Deep-Agent-App` / `X-Deep-Agent-Language` 穿过平台网关；后端再转成 MCP 所需的标准 `app` / `space` / `language`，不要从浏览器直接发送会触发网关路由校验的 `app` Header。
- 控制台 API 根地址统一由 `api.js` 基于 `document.baseURI` 的上一级动态解析；本机 `/console/`、平台多级代理 `<服务前缀>/console/` 共用同一规则，其他模块不要自行拼接域名或服务前缀。
- 调试 App 变化会停止当前流并重新加载页面状态；旧缓存中的 Space、Language 和自定义后端地址会被清理。
- 控制台请求固定使用当前同源后端。本地联调直接访问本机 `8000/console/`，不在浏览器中切换后端地址。
- SSE 解析、HITL 参数归一化和 Schema 文本校验分别位于 `protocol.js`、`hitl.js`、`schema-editor.js`，可由 Node 内置测试独立验证。
- 控制台测试：`node --test tests/frontend/console/*.test.mjs`。
- 配置中心默认打开 Agent；“全局配置”仍位于“辅助模型”后、模型资源分割线前，通过 `GET /api/v1/global-settings` 按需展示安全白名单，没有编辑或保存入口。
- 配置中心左侧页签只在当前前端页面的模块内存中保留；工作台与配置中心之间切换会恢复上次页签，浏览器刷新后重新回到 Agent，不写入 LocalStorage 或后端。
- Agent 编辑弹窗在标题区显示完整 Agent ID；测试工作台对话标题第二行以 `会话 ID：<完整 ID>` 展示当前会话，不再截取前八位。
- `/` 跳转到 `/console/#/workspace`；旧 `/settings.html`、`/models.html`、`/admin/` 返回 404。

---

## SSE 对接

主接口：`POST /api/v1/chat/stream`

| event | data |
| --- | --- |
| `session` | `{ session_id }` 或含 `agent_id` |
| `context_compaction` | `{ type, phase, message, ... }`（当前请求瞬时状态，不进入历史） |
| `text` | `{ type, delta }` |
| `thinking` | `{ type, delta }` |
| `tool_call` | `{ type, tool, args, tool_call_id, widget?, metadata?, source? }` |
| `tool_result` | `{ type, tool, result, tool_call_id }` |
| `final` | `{ type, status, output, usage, deferred?, context? }`（`context` 为预算/压缩观测） |
| `error` | `{ type, message }` |

完整协议（含同 ID 多次推送、生命周期）：[`../docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。

实现参考：`console/chat.js` 与 `console/protocol.js`。

上下文压缩状态延迟 150ms 显示；同一请求只保留一个状态胶囊，收到首个
`thinking` / `text` / `tool_call` 或结束事件时必须清理。

---

## 运行

**随后端（推荐）**

```bash
# 仓库根
uvicorn deep_agent.main:app --reload
# → http://127.0.0.1:8000/console/
```

**独立静态服务**

```bash
cd frontend
python -m http.server 5173
# → http://localhost:5173/console/
```

页面固定请求当前同源后端；顶部状态表示 `/health` 是否正常。

---

## 修改指南

| 改什么 | 看哪里 |
| --- | --- |
| 聊天气泡、工具块 UI | `console/chat.js` + `console/workspace.css` |
| 新增 HITL UI 工具（改仓库） | [`../docs/guides/ui-tools.md`](../docs/guides/ui-tools.md) |
| 使用 / 对接 HITL（不改仓库） | [`../docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md) |
| HITL 渲染与 metadata | `console/chat.js` → `isUiToolCall`、`loadUiTools`、`renderHitlWidget` |
| 页面布局 | `console/index.html` 与两个 Console template |
| 一体化控制台布局与编排 | `console/index.html`、`console/console.css`、`console/workspace.js`、`console/configuration.js` |
| 配置 CRUD | `console/configuration/` 下各文件 |
| 模型运行属性 | `console/configuration/llm-models.js`；上下文、最大输出、输入模态和 tags 保存到 `llm_models` |
| 模型目录 | `console/configuration/catalog.js` → DB-only GET 与管理员 POST refresh |
| 上下文压缩观测 | `console/chat.js` 拉取 session context-status；`console/workspace.js` 在请求上下文概览展示 |
| 上下文压缩测试会话 | `console/workspace.js` → 场景弹窗、创建后打开会话并预填验证问题 |
| Markdown 主题/安全 | `vendor/`，见 `vendor/README.md` |

历史消息由后端按 `session_id` 维护；切换会话时 `GET /api/v1/sessions/{id}/history` 回放。pending UI 工具的 `tool_call` part 同样经后端 enrichment 附带 `widget` / `metadata`。

---

## 陷阱

- `file://` 打开可用但有限制；推荐静态服务器
- 生产环境应收紧后端 CORS `allow_origins`
- `tool_call` 首帧 `args` 可能为 `{}`，需按 `tool_call_id` 合并后续帧
- 配置表单必须区分“主体保存失败”和“保存成功后的列表刷新失败”；后者保留成功结果并提示重新加载，不能让用户按新建模式重复提交。条件字段（如 URL MCP 的 URL）需要前端动态必填和后端 Schema 双重校验。
- Agent 的 `capabilities: null` 表示使用系统默认能力，`[]` 表示明确不启用任何可选能力；新建表单必须按 `null` 初始化。删除 MCP 或 UI 工具若返回 `detail.referenced_by`，应直接展示引用它的 Agent 名称。
- `builtin__confirm`、`builtin__choice` 是代码内置但非默认启用的普通 `ui:<name>` 能力；Agent 新建和历史回填不得自动勾选。choice 始终提供候选外自由输入，提交结果固定包含 `selected: string[]` 与 `custom_input: string | null`，二者不能同时为空。
- MCP 新建页只允许 URL transport；历史 stdio 配置只读且不显示编辑入口。Builtin MCP 继续由仓库内程序配置管理，列表保留编辑入口但不显示删除按钮，后端删除接口也会拒绝。
- 模型目录只由管理员手工刷新 models.dev，保存模型配置不得触发外网请求。目录按 Lab → Model → Provider 展示，仅负责预填；模态与能力 tags 分开，Agent 运行时只认 `llm_models`。
- 上下文压缩在模型配置有效上下文窗口时自动启用；Console 只读展示全局策略、预算、
  压缩前后和 Checkpoint 状态，不提供启用开关，也不展示或编辑摘要正文。
- `attachment` 是默认关闭的 Agent 能力。Console 从 `/uploads/capabilities` 读取实际 MIME、
  单文件大小和单次数量限制；新请求不得再发送 `extracted_text`。工具分页协议和解析边界见
  [`../docs/design/06-attachment-parsing/`](../docs/design/06-attachment-parsing/)。
- “新建测试会话”是管理员控制台的常驻入口；它位于会话搜索框和普通新建按钮之间，
  当前 Agent 的模型未配置上下文窗口时不可用，但不能改变普通新建行为。场景只构造合法
  历史，不在前端拼接或伪造消息 payload。

更多说明：[`README.md`](README.md)。
