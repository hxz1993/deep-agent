# deep-agent 前端

一个**无需构建**的智能体对话界面，适配后端 `POST /api/v1/chat/stream`（SSE 流式）接口。Markdown 渲染通过 `frontend/vendor/` 中的预构建开源库实现（见 [vendor/README.md](./vendor/README.md)）。

## 功能

- 流式对话：通过 `fetch` + `ReadableStream` 解析 SSE（浏览器原生 `EventSource` 不支持 POST，因此手动解析）。
- 工具调用可视化：实时展示 `tool_call` / `tool_result`，可折叠查看参数与结果；同 `tool_call_id` 自动合并更新（避免空参数闪烁）。
- Markdown 渲染：基于 [marked](https://github.com/markedjs/marked) + [DOMPurify](https://github.com/cure53/DOMPurify) + [highlight.js](https://github.com/highlightjs/highlight.js)，样式采用 [github-markdown-css](https://github.com/sindresorhus/github-markdown-css)（暗色主题）。支持 GFM 表格、任务列表、代码高亮、引用、链接等。
- 会话管理：自动记录后端返回的 `session_id`，支持新建、切换、删除（调用 `DELETE /api/v1/sessions/{id}`）。
- 能力面板：拉取 `GET /api/v1/tools` 展示已注册的 capabilities 及其工具。
- 用量显示：解析 `final` 事件中的 `usage`（requests / tokens）。
- 可在侧边栏配置「后端地址」与「模型覆盖」，配置保存在 `localStorage`。

## 对接的 SSE 事件

完整协议说明见 **[docs/sse-protocol.md](../deep_agent/docs/sse-protocol.md)**（含事件生命周期、`tool_call` 同 ID 多次推送、客户端实现清单等）。

| event         | data 字段                                  |
| ------------- | ------------------------------------------ |
| `session`     | `{ session_id }`                           |
| `text`        | `{ type, delta }`                          |
| `tool_call`   | `{ type, tool, args, tool_call_id }`       |
| `tool_result` | `{ type, tool, result, tool_call_id }`     |
| `final`       | `{ type, output, usage }`                  |
| `error`       | `{ type, message }`                        |

## 运行

前端是纯静态文件，任选一种方式启动：

```bash
# 1) 用任意静态服务器（推荐，避免 file:// 限制）
cd frontend
python -m http.server 5173
# 然后浏览器打开 http://localhost:5173
```

或直接双击 `index.html` 用浏览器打开也可（`file://` 模式下后端地址会默认指向 `http://localhost:8000`）。

### 启动后端

```bash
# 在仓库根目录
uvicorn app.main:app --reload --port 8000
```

后端已启用 CORS（`allow_origins=["*"]`），前端可从任意端口跨域访问。

### 配置

首次打开后，在左下角：

- **后端地址**：默认 `http://<当前主机>:8000`，可改为你的后端地址。
- **模型覆盖**：留空使用后端默认模型；可填写如 `openai:gpt-4o`、`anthropic:claude-sonnet-4-5`。

顶部左侧的连接状态点变绿表示 `/health` 正常。

## 说明

- 历史消息由后端按 `session_id` 维护，前端不持久化消息正文。切换历史会话时，界面会清空并提示「继续输入即可接上下文」。
- 若部署到生产，建议把 CORS 的 `allow_origins` 收紧为前端实际域名。
