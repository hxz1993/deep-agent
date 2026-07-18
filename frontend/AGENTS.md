# AGENTS.md — 前端

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端 API：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

纯静态 HTML/CSS/JS，**无构建步骤**。后端启动时若存在 `frontend/index.html`，会挂载到 `/`。

---

## 文件结构

```
frontend/
├── index.html      # 聊天主界面
├── app.js          # SSE 解析、会话管理、Markdown 渲染
├── styles.css
├── console/        # 一体化控制台（测试工作台 / 配置中心 / 诊断中心）
├── admin/          # Agent / MCP / UI 工具管理后台（需 DB）
│   ├── index.html
│   ├── admin.js
│   ├── agents.js
│   ├── mcp.js
│   ├── ui_schema_editor.js  # Schema 表单编辑器
│   └── ui_tools.js
└── vendor/         # marked、DOMPurify、highlight.js 等预构建库
```

---

## 功能概览

| 功能 | 实现 |
| --- | --- |
| 流式对话 | `fetch` + `ReadableStream` 手动解析 SSE（非 `EventSource`，因需 POST） |
| 附件上传 | `POST /api/v1/uploads`（multipart）→ 发送时 `attachments[]` 引用 |
| 思考过程 UI | `thinking` 可折叠块，默认折叠；与 `text` 分离 |
| 工具调用 UI | `tool_call` / `tool_result` 可折叠；同 `tool_call_id` 合并更新 |
| HITL UI 工具 | 内置 `confirm_action` / `ask_user_choice` + 动态 manual/MCP 工具；SSE / history 携带 `widget` / `metadata`；见 [`../docs/ui-tools-external.md`](../docs/ui-tools-external.md) |
| Markdown | marked + DOMPurify + highlight.js |
| 会话 | `session_id` 由后端 `session` 事件返回；`GET/DELETE /api/v1/sessions`；标题展示 `title \|\| user_prompt`（见 [`../docs/experience/ai-title-generator.md`](../docs/experience/ai-title-generator.md)） |
| 能力面板 | `GET /api/v1/tools` |
| 配置 | 后端地址、模型覆盖存 `localStorage` |
| 一体化控制台 | `/console/`；保留旧页面并集中工作台、配置和诊断入口 |

## 一体化控制台

`frontend/console/` 是不依赖构建工具的 ES Modules 页面。它复用现有聊天和配置 API，但不读取旧设置页的 `da_request_headers`：

- 平台用户与鉴权只读，通过 `GET /api/v1/request-context` 获取。
- 仅允许在当前控制台覆盖 `app`、`space`、`language`，分别参与后续请求 Header。
- `app` 或 `space` 变化会停止当前流并重新加载页面状态；只切换语言时保留会话。
- SSE 解析、HITL 参数归一化和 Schema 文本校验分别位于 `protocol.js`、`hitl.js`、`schema-editor.js`，可由 Node 内置测试独立验证。
- 控制台测试：`node --test tests/frontend/console/*.test.mjs`。
- 原有 `/`、`/settings.html`、`/models.html`、`/admin/` 仍是兼容入口，不要在迁移完成前删除。

---

## SSE 对接

主接口：`POST /api/v1/chat/stream`

| event | data |
| --- | --- |
| `session` | `{ session_id }` 或含 `agent_id` |
| `text` | `{ type, delta }` |
| `thinking` | `{ type, delta }` |
| `tool_call` | `{ type, tool, args, tool_call_id, widget?, metadata?, source? }` |
| `tool_result` | `{ type, tool, result, tool_call_id }` |
| `final` | `{ type, status, output, usage, deferred? }`（`deferred.calls[]` 可含 `widget` / `metadata`） |
| `error` | `{ type, message }` |

完整协议（含同 ID 多次推送、生命周期）：[`../docs/sse-protocol.md`](../docs/sse-protocol.md)。

实现参考：`app.js`。

---

## 运行

**随后端（推荐）**

```bash
# 仓库根
uvicorn deep_agent.main:app --reload
# → http://127.0.0.1:8000/
```

**独立静态服务**

```bash
cd frontend
python -m http.server 5173
# → http://localhost:5173 （CORS 已放开，可连任意端口后端）
```

左下角配置后端地址（默认 `http://<host>:8000`）；顶部绿点表示 `/health` 正常。

---

## 修改指南

| 改什么 | 看哪里 |
| --- | --- |
| 聊天气泡、工具块 UI | `app.js` + `styles.css` |
| 新增 HITL UI 工具（改仓库） | [`../docs/ui-tools.md`](../docs/ui-tools.md) |
| 使用 / 对接 HITL（不改仓库） | [`../docs/ui-tools-external.md`](../docs/ui-tools-external.md) |
| HITL 渲染与 metadata | `app.js` → `isUiToolCall`、`loadUiTools`、`renderHitlWidget` |
| 页面布局 | `index.html` |
| 一体化控制台布局与编排 | `console/index.html`、`console/console.css`、`console/workspace.js`、`console/configuration.js` |
| Admin CRUD | `admin/` 下各文件（含 **UI 工具** 页 `/admin/` →「UI 工具」标签） |
| Admin 模型 Meta 刷新 | `admin/llm_models.js` → `POST /api/v1/llm-models/refresh-metadata`；详见 [`../docs/experience/llm-model-modalities.md`](../docs/experience/llm-model-modalities.md) |
| 模型模态目录 | `models.html` + `models.js` → `GET /api/v1/llm-models/catalog`；浏览 models.dev / 内置 JSON，按 provider / 模态筛选 |
| Markdown 主题/安全 | `vendor/`，见 `vendor/README.md` |

历史消息由后端按 `session_id` 维护；切换会话时 `GET /api/v1/sessions/{id}/history` 回放。pending UI 工具的 `tool_call` part 同样经后端 enrichment 附带 `widget` / `metadata`。

---

## 陷阱

- `file://` 打开可用但有限制；推荐静态服务器
- 生产环境应收紧后端 CORS `allow_origins`
- `tool_call` 首帧 `args` 可能为 `{}`，需按 `tool_call_id` 合并后续帧

更多说明：[`README.md`](README.md)。
