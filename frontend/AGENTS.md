# AGENTS.md — 前端

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端 API：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

纯静态 HTML/CSS/JS，**无构建步骤**。后端将 `frontend/console/` 挂载到 `/console`，根路径跳转到 Console。

Console 图标统一使用仓库内 `frontend/vendor/lucide.min.js` 的线性 SVG。静态模板使用
`data-lucide`，动态插入 DOM 的图标必须在插入后调用 `window.lucide.createIcons()`；不要重新
引入 emoji、字符图标、外部 CDN 或第二套图标库。

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
| 停止生成 | `POST /sessions/{id}/cancel` 后等待真实 `cancelled`；History 的 `assistant.status=interrupted` 恢复“已停止”，不以 Abort 或本地假事件代替 |
| 附件上传 | `POST /api/v1/uploads`（multipart）→ 发送时仅传 `attachments[]` 引用；文档由 Agent 可选 `builtin:attachment` 工具包按需读取 |
| 思考过程 UI | `thinking` 可折叠块，默认折叠；与 `text` 分离 |
| 工具调用 UI | `tool_call` / `tool_result` 可折叠；同 `tool_call_id` 合并更新 |
| HITL UI 工具 | `ui:builtin` + 数据库手工 UI 工具包；Agent 按包选择，SSE / history 仍使用稳定工具协议名并携带 `widget` / `metadata`；见 [`../docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md) |
| Markdown | marked + DOMPurify + highlight.js |
| 会话 | `session_id` 由后端 `session` 事件返回；`GET/DELETE /api/v1/sessions`；标题展示 `title \|\| user_prompt`（见 [`../docs/guides/experience/ai-title-generator.md`](../docs/guides/experience/ai-title-generator.md)） |
| 能力面板 | `GET /api/v1/tools` |
| 配置 | `localStorage` 只保存调试 App；全局环境配置只读展示，身份和鉴权由平台提供 |
| 内置工具目录 | 配置中心只读展示 `builtin:general`、`builtin:sandbox`、`builtin:attachment`；Agent 按包选择 |
| 一体化控制台 | `/console/`；工作台、配置和诊断的唯一入口 |

v0.3.12 的配置卡只读展示工具披露策略：附件包为“有附件时可用”，大型 MCP 为“自动预选 Top 3 / 搜索 Top 5”。聊天中的 `search_tools` 是运行诊断过程，默认折叠显示为“正在查找可用工具”，不伪装成业务 MCP 工具。

工作台默认在会话栏顶部用 Agent 下拉框切换；管理员可在当前页面内临时固定左侧 Agent
列表。下拉模式只在下拉框右侧保留固定操作；固定模式把刷新/取消固定集中在 Agent 标题栏。
测试 Header 和会话详情统一位于对话标题栏右侧。该状态不写 LocalStorage。窄屏必须退化为
可访问的下拉或抽屉，不能同时隐藏两种入口。

右侧会话详情不设页签，在一个连续泳道中展示会话、请求 Header、预算、最近一次 Run Usage、
压缩和 Checkpoint。工具过程由对话区负责，Console 不再额外缓存或展示 SSE 事件列表。
`context-status` 返回的是 `estimate_scope=history_only` 的会话历史快照；最近一次 Run Usage
来自当前页面收到的 `final.usage`。前端只格式化和计算展示比例，不复制后端 Token 估算、
输出预测或安全余量算法。

配置中心 UI 工具页使用“左侧工具包树 + 右侧当前包工具表”：来源只用于左树分组，右表不再
重复显示所属包；窄屏改为上下布局，但仍必须先选包再维护工具。Builtin 包只读，只有选中手工包
时才开放新建工具。

Agent 编辑弹窗的系统提示词标题栏显示“字符数 / 预估 Token”。字符数和 Token 估算统一调用
后端 `POST /api/v1/agents/prompt-estimate`，由当前模型对应的 `TokenEstimator` 计算；前端只做
防抖、紧凑格式和 tooltip 展示，不维护另一套字符比例算法。

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
- 配置中心按“Agent 设置 / 系统设置 / 辅助资源”分组；系统设置包含辅助模型、全局配置和只读数据库连接。数据库连接页通过 `GET /api/v1/database-info` 只展示类型、主机、端口、库名或 SQLite 文件名，不返回凭据、连接参数或本地完整路径。
- “内置工具”位于模型之后、MCP 服务之前，只读展示代码随版本发布的通用工具与
  附件读取工具包；页面没有注册、编辑、删除或全局启停入口。
- Console 顶部只展示一处产品版本，通过同源 `GET /info` 读取
  `deep_agent.__version__`；配置中心的数据库结构 revision 是独立运维信息。
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
| `context_compaction_result` | 主动压缩最终结果；成功、无需压缩或失败，不作为聊天消息 |
| `text` | `{ type, delta }` |
| `thinking` | `{ type, delta }` |
| `tool_call` | `{ type, tool, args, tool_call_id, widget?, metadata?, source? }` |
| `tool_result` | `{ type, tool, result, tool_call_id }` |
| `final` | `{ type, status, output, usage, deferred?, context? }`（`context` 为预算/压缩观测） |
| `error` | `{ type, message }` |
| `cancelled` | `{ type, status, usage, history_persisted, model_message_count, user_branch_point }`；保留当前流式内容并标记中断，重新打开会话时再由 History 校准 |

完整协议（含同 ID 多次推送、生命周期）：[`../docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。

实现参考：`console/chat.js` 与 `console/protocol.js`。

自动和主动上下文压缩复用消息区中的同一套独立过程条，不挂在助手消息内部。状态延迟
150ms 显示；成功写入 Checkpoint 后立即刷新历史，并与重新打开会话一样展示当前有效的
压缩分隔标记。临时 Sliding Window、失败和无持久化结果只显示瞬时过程条，不伪造历史标记。

v0.3.4 的输入框在附件线性图标旁提供主动压缩线性图标。按钮状态、上下文百分比和 tooltip
只读取后端 `context-status`，不得在前端复制 Token 估算、动态输出预测或安全余量算法。
前端不得因历史较短、已有 Checkpoint 或没有新增消息禁用主动入口；这些情况发给后端并展示
`noop` 结果。只在无会话、当前 Run/压缩进行中或等待 Deferred 等技术冲突时禁用。
历史中的压缩边界来自 `history.context_checkpoint`，使用分隔标记展示，不渲染成消息气泡。
v0.3.14 起主动压缩的终态不内嵌刷新后的 `context_status`：客户端收到
`context_compaction_result` 后先结束压缩流，再分别刷新 history 与 context-status，避免
Checkpoint 已成功保存但状态查询延迟时让 SSE 长时间不关闭。

v0.3.5 Console 只兼容展示历史 `context_refs` 的 label/type，不提供业务 JSON 构造或编辑器；
含引用的历史消息不允许用旧文本编辑动作重试，避免静默丢失引用。业务 Chip 和详情由宿主前端实现。

v0.3.10 起停止按钮只调用 session 取消接口。`cancel_requested` 后继续消费原 SSE，收到真实
`cancelled` 才结束运行态；当前页面保留部分回答和工具过程，不立即重载整段历史，重新打开
会话时再由持久化的 `interrupted` 状态校准。失败允许重试，`not_running` 不伪造成功。
AbortController 只用于页面卸载或网络资源清理。首条 `session` 事件到达前停止按钮保持禁用。

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
python3.12 -m http.server 5173
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
| 上下文压缩观测 | `console/chat.js` 拉取 session context-status；`console/workspace.js` 在右侧会话详情泳道展示 |
| 上下文压缩测试会话 | `console/workspace.js` → 场景弹窗、创建后打开会话并预填验证问题 |
| Markdown 主题/安全 | `vendor/`，见 `vendor/README.md` |

历史消息由后端按 `session_id` 维护；切换会话时 `GET /api/v1/sessions/{id}/history` 回放。pending UI 工具的 `tool_call` part 同样经后端 enrichment 附带 `widget` / `metadata`。

---

## 陷阱

- `file://` 打开可用但有限制；推荐静态服务器
- 生产环境应收紧后端 CORS `allow_origins`
- `tool_call` 首帧 `args` 可能为 `{}`，需按 `tool_call_id` 合并后续帧
- 配置表单必须区分“主体保存失败”和“保存成功后的列表刷新失败”；后者保留成功结果并提示重新加载，不能让用户按新建模式重复提交。条件字段（如 URL MCP 的 URL）需要前端动态必填和后端 Schema 双重校验。
- Agent 的 `capabilities: null` 表示使用默认工具包，`[]` 表示明确不启用任何工具包；新建表单必须按 `null` 初始化。删除 MCP、UI 工具包或 UI 工具若返回 `detail.referenced_by`，应直接展示引用它的 Agent 名称。
- `builtin__confirm`、`builtin__choice` 同属非默认启用的 `ui:builtin` 工具包；Agent 新建和历史回填不得自动勾选。choice 始终提供候选外自由输入，提交结果固定包含 `selected: string[]` 与 `custom_input: string | null`，二者不能同时为空。
- MCP 新建页只允许 URL transport；历史 stdio 配置只读且不显示编辑入口。Builtin MCP 继续由仓库内程序配置管理，列表保留编辑入口但不显示删除按钮，后端删除接口也会拒绝。
- URL MCP 的普通编辑名称只读；改名必须走列表中的独立“重命名”操作。成功后 Console 同步刷新
  MCP、工具包与 Agent 能力目录；Builtin 和历史 stdio MCP 不显示重命名入口。
- 模型目录只由管理员手工刷新 models.dev，保存模型配置不得触发外网请求。目录按 Lab → Model → Provider 展示，仅负责预填；模态与能力 tags 分开，Agent 运行时只认 `llm_models`。
- 上下文压缩在模型配置有效上下文窗口时自动启用；Console 只读展示全局策略、预算、
  压缩前后和 Checkpoint 状态，不提供启用开关，也不展示或编辑摘要正文。
- `builtin:attachment` 是默认关闭的 Agent 工具包。Console 从 `/uploads/capabilities` 读取实际 MIME、
  单文件大小和单次数量限制；新请求不得再发送 `extracted_text`。工具分页协议和解析边界见
  [`../docs/design/06-attachment-parsing/`](../docs/design/06-attachment-parsing/)。
- “新建测试会话”是管理员控制台的常驻入口；它位于会话搜索框和普通新建按钮之间，
  当前 Agent 的模型未配置上下文窗口时不可用，但不能改变普通新建行为。场景只构造合法
  历史，不在前端拼接或伪造消息 payload。

更多说明：[`README.md`](README.md)。
