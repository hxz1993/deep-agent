# AGENTS.md — 前端

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端 API：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

纯静态 HTML/CSS/JS，**无构建步骤**。后端将 `frontend/console/` 挂载到 `/console`，根路径跳转到 Console。

Console 图标统一使用仓库内 `frontend/vendor/lucide.min.js` 的线性 SVG。静态模板使用
`data-lucide`，动态插入 DOM 的图标必须在插入后调用 `window.lucide.createIcons()`；不要重新
引入 emoji、字符图标、外部 CDN 或第二套图标库。

修改 Console 页面结构、控件或样式前，必须先读
[`../docs/guides/console-ui-guidelines.md`](../docs/guides/console-ui-guidelines.md)，并在当前页面和相邻页面中
找到可复用模式。没有独立层级、滚动、选择、状态或安全边界的字段组不得新增装饰性容器。

## Console UI 开发分支

Bundled Console 与后端统一在 `feat-ruge-0803` 主开发分支维护，不再保留独立 UI worktree。
每批 UI 需求开始前先确认分支和工作区，保留已有改动；修改范围通常限定在
`frontend/console/**`、`tests/frontend/console/**`，必要时同步前端说明。若需求实际需要后端
API、SSE、Event Store或持久化契约变化，应先确认产品边界，再修改对应拥有层。

---

## 文件结构

```
frontend/
├── console/             # 唯一页面：测试工作台 / 配置中心 / 诊断中心
│   ├── index.html
│   ├── chat.js          # SSE、会话、附件、HITL
│   ├── workspace.js
│   ├── configuration.js
│   └── configuration/   # Agent / 模型 / MCP / Skill / UI 工具管理
└── vendor/              # marked、DOMPurify、highlight.js 等预构建库
```

---

## 功能概览

| 功能 | 实现 |
| --- | --- |
| 流式对话 | `fetch` + `ReadableStream` 手动解析 SSE（非 `EventSource`，因需 POST） |
| 停止生成 | `POST /sessions/{id}/cancel` 后等待真实`cancelled`；Event投影的`assistant.status=interrupted`恢复“已停止”，不以Abort或本地假事件代替 |
| 附件上传 | `POST /api/v1/uploads`（multipart）→ 发送时仅传 `attachments[]` 引用；可读文档由系统 `read_file`按需读取 |
| 思考过程 UI | `thinking` 可折叠块，默认折叠；与 `text` 分离 |
| 工具调用 UI | `tool_call` / `tool_result` 可折叠；同 `tool_call_id` 合并更新；`tool_call_removed` 删除被 ModelRetry 或取消撤回的临时卡片 |
| HITL UI 工具 | `ui:builtin` + 数据库手工UI工具包；Agent按包选择，SSE/Event仍使用稳定工具协议名并携带`widget/metadata`；见[`../docs/guides/ui-tools-external.md`](../docs/guides/ui-tools-external.md) |
| Markdown | marked + DOMPurify + highlight.js |
| 会话 | `session_id` 由后端 `session` 事件返回；`GET/DELETE /api/v1/sessions`；标题展示 `title \|\| user_prompt`（见 [`../docs/guides/experience/ai-title-generator.md`](../docs/guides/experience/ai-title-generator.md)） |
| 能力面板 | `GET /api/v1/tools` |
| 配置 | `localStorage` 只保存调试 App；全局环境配置只读展示，身份和鉴权由平台提供 |
| 内置工具目录 | 配置中心展示`builtin:general`、`builtin:sandbox`、`builtin:ptc`（程序编排）、`builtin:web`；Agent按包选择，旧`builtin:code-mode`不作为新目录项展示 |
| 系统工具目录 | 配置中心按任务规划、Skill、文件、工具结果、MCP 工具发现、MCP Resource 分组展示自动注入工具；不参与 Agent 配置 |
| 渠道配置 | 配置中心维护企微Account、Agent、Bot凭证和启停；顶部展示渠道网关部署/运行状态，列表展示机器人连接状态；快捷命令弹框从公共只读目录获取，与渠道 `/help` 共用定义 |
| 任务计划 | 只识别`write_plan`完整快照；仅当前Run直播展示，任何Run终态后隐藏，重新打开History不恢复；同Turn Deferred由后端恢复PlanStore |
| Skill | 独立 Skill 列表与详情；上传包安装/替换/单个与全部导出/删除；文本、Word、Excel 资源可预览，脚本只列包内路径和数量，不展示可执行性判断；Agent 使用独立 `skills` 列表选择 |
| 一体化控制台 | `/console/`；工作台、配置和诊断的唯一入口 |
| 会话轨迹 | 工作台“对话 / 轨迹”页签；普通对话读History/SSE ConversationItem，只有轨迹按Sequence读取Event API |

v0.6.7起，轨迹把`run_code`显示为独立`PTC`类型，并在详情中提供带Python高亮与复制能力的
Code页签；用户输入详情中的附件与Context Reference分区展示。这里只改变Event的Console投影，
不增加Event字段或普通ConversationItem类型。

普通对话中的思考与工具标题共用弱灰色、小图标和文字左对齐；标题后的展开箭头仅悬停/焦点时出现
（触屏保持可见），左侧标注线只显示在展开正文。思考实时显示“思考中…”和动态提示，结束后
显示“思考过程”；工具终态只显示紧随名称的文字，不附加状态图标。两类均可点击或键盘折叠，
不以装饰性卡片区分；实时与历史共用该呈现规则。

`execute_python`返回的生成文件在聊天工具卡与轨迹中展示名称、MIME、大小和可复制File ID；本地
FileStore复用现有下载链接，平台FileStore不新增代理下载接口。页面不得渲染Base64或宿主临时路径。

v0.3.12 的配置卡只读展示工具披露策略：附件包为“有附件时可用”，大型 MCP 为“自动预选 Top 3 / 搜索 Top 5”。聊天中的 `search_tools` 是运行诊断过程，默认折叠显示为“正在查找可用工具”，不伪装成业务 MCP 工具。

工作台仅在会话栏顶部用 Agent 下拉框切换，右侧保留刷新操作；不提供独立 Agent 列表模式。
测试 Header 和会话详情统一位于对话标题栏右侧。测试 Header 使用锚定按钮的轻量浮层，
该状态不写 LocalStorage。窄屏继续压缩同一套多栏布局，不切换成抽屉或另一套交互模式。

右侧会话详情默认使用“概览 / 当前 / 上下文”三页签；Agent关联渠道或当前Session
已有渠道来源时，在概览后增加“渠道”。来源只从当前Session快照读取；普通会话只显示Agent关联
渠道并说明没有渠道来源，不用当前配置拼造历史来源。渠道页承载原渠道信息及ID悬停复制；概览
Session ID同样可复制。四页签在窄栏可横向滚动。顶部页签固定，正文独立滚动，
不使用带外侧留白的悬浮sticky条。会话页展示会话、请求Header和
复合上下文预算；当前回合页展示最近一次Run Usage与Compaction；上下文引用页由
消息中的引用信息按钮定位并展示只读 JSON。用户气泡引用胶囊只显示引用标签，最长180px并省略，
悬停可见完整标签，JSON详情保留完整内容；不重复显示“上下文”前缀。工具过程仍由对话区负责；History与当前Run的实时SSE
分别通过同一套Assistant展示动作驱动DOM Presenter，不引入持久化Conversation Item推送。
独立轨迹页只读取Event API，并在刷新后以持久事件重新校准。
轨迹将同一ModelResponse中的thinking、text和tool call意图聚合为一个助手Loop，同时保留独立
Tool Call/Result记录；用户输入行只按Event中现有`context_refs.type/label/schema_version`和
附件元数据加标记，不从正文猜测业务上下文。轨迹详情复用工作台右侧Inspector，返回对话后恢复
原会话详情。
`context-status` 返回的是 `estimate_scope=history_only` 的会话历史快照；最近一次 Run Usage
来自当前页面收到的 `final.usage`。前端只格式化和计算展示比例，不复制后端 Token 估算、
输出预测或安全余量算法。

配置中心 UI 工具页使用“左侧工具包树 + 右侧当前包工具表”：来源只用于左树分组，右表不再
重复显示所属包；资源自身没有启停状态或启停筛选。窄屏改为上下布局，仍须先选包
再维护工具。Builtin 包只读，只有选中手工包时才开放新建工具；Builtin 工具详情复用自定义工具的
表单布局，但所有字段只读且不显示保存操作，避免维护第二套详情视觉。

配置中心 Skill 页不是工具包页：Builtin Skill 只读，上传 Skill 可安装、替换、单个与全部导出和删除；
详情只展示安全资源索引和 Manifest；文本、DOCX、XLSX 可预览，`scripts/`/`tools/` 只展示路径与
数量，不判断是否可执行，也不展示脚本正文。Agent 独立编辑页将
Skill 与工具包分区保存；是否参与运行只看 Agent 的显式关联。

配置中心所有目录资源都没有独立“启用”配置：Provider只按地址、凭据和类型等实际配置判断可用，
Model由Agent或请求级模型选择；Skill、MCP、UI工具包、UI工具和预制工具包由Agent显式关联后才进入
Run。Console不得重新增加这些资源的启停开关、状态列或停用操作。

Agent 独立编辑页的系统提示词标题栏显示“字符数 / 预估 Token”。字符数和 Token 估算统一调用
后端 `POST /api/v1/agents/prompt-estimate`，由当前模型对应的 `TokenEstimator` 计算；前端只做
防抖、紧凑格式和 tooltip 展示，不维护另一套字符比例算法。

## 一体化控制台

主导航与配置目录合为唯一左栏：品牌与收起按钮位于左栏顶部，移除右侧顶栏，内容从页面顶部开始。
对话测试、Agent位于目录顶部；“设置”按Provider、Skill、MCP、内置工具、UI工具、渠道排列；
“系统”包含全局配置、模型目录；数据库只读摘要并入全局配置。分组以弱灰文字和留白区分，不加分隔线。
底部固定诊断中心和接口文档，下方以一条分隔线划分用户栏；展开导航时四选一主题栏位于用户名
和头像上方，四个按钮固定为28px正方形，不拉伸；可用宽度不足124px或收起导航时，单个主题按钮打开向右的横向浮层。连接状态表示后端健康检查，放在品牌副标题；点击头像向右上展开用户信息，
悬停与单纯聚焦不展开；再次点击、点击外部或Esc关闭。保留版本与数据库信息，浮层内部不加分割线。目录中部独立滚动，不再嵌套配置二级侧栏。
导航宽度桌面200px、920px以下176px、760px以下148px；手动收起为56px纯图标，保留tooltip与
无障碍名称；右边缘在56–200px间拖拽，140–96px之间渐隐文字，96px以下纯图标。
向内拖到100px时以180ms过渡吸附到56px；从收起状态向外拖仍连续跟手，不提前吸回。
按钮展开收起采用180ms过渡；导航分组与底部用户栏始终保留相同行高，图标纵向位置不变，分割线高亮与工作台一致。
收起后隐藏收起按钮，点击Logo展开，底部只留主题与用户图标，主题继续使用横向浮层。
收起状态仅保留当前页面内存，不写入后端或LocalStorage，切页不重置。
会话列表、对话及详情使用连续表面；列表Agent标题独立44px高、不加下边线，与搜索行紧邻。
所有宽度下，目录内容与独立编辑页四边均无外层间距、圆角和卡片边框，直接衔接左侧导航；保留控件内部留白。
中间1px分隔线保留9px拖拽热区；详情展开收起采用180ms过渡，尊重减少动画偏好。
会话列表按当前Agent以50条分页请求真实Session API，触底追加并去重，切Agent丢弃旧请求结果；
底部区分加载中、可重试错误和已经到底；“已经到底了”仅在历史填满一屏且发生滚动手势后显示，短列表不展示。
一屏按剩余空间不足再放一条完整会话判断，避免末行下几像素空隙导致无提示；提示自身高度不参与判断。
列表仅在向下滚到底时提供一次4px、200ms轻回弹，向上滚动不回弹，不改变真实滚动位置；
同次滚轮手势及惯性尾段不重复触发，停止350ms后才能再次触发，触摸每次手势最多一次。
减少动画偏好关闭回弹，不把接口失败当成无历史。
列表统计仅展示回合与附件，不展示Loop计数；Agent下拉和当前选择根据实际渠道配置展示渠道标记。
主导航使用随主题变化的淡底色；配置页标题16px、分组14px、条目13px，运行配置字段12px，避免过粗过大的标题。
Agent卡片/列表的发起对话在当前页切换工作台并选择对应Agent；捕获阶段接管点击，避免行操作
stopPropagation拦截页面导航。
Agent/MCP独立编辑页从主导航右侧展开，跨页和目录切换均保留未保存确认；只有成功切换才记住目录。

`frontend/console/` 是不依赖构建工具的 ES Modules 页面。它复用现有聊天和配置 API，但不读取旧设置页的 `da_request_headers`：

- 平台用户身份只读，通过 `GET /api/v1/request-context` 获取；随后调用`GET /api/v1/admin/access`。
- 顶部用户菜单通过同源`GET /api/v1/current-user`读取账号中心用户名；接口不可用时只回退显示已认证
  User ID，不阻断Console初始化，也不让浏览器直接访问账号中心。
- 只有`allowed=true`且包含`enterprise_admin`角色时才初始化工作台、配置中心和诊断页面。普通用户、
  固定`admin`、401或权限服务503均显示整页门禁，不得继续请求管理数据；直接Hash访问也不能绕过。
- 当前控制台只允许覆盖 `app`；Space 根据标准 App 编码自动去掉末尾三位，Language 固定为 `zh-cn`。
- Console 调试值使用 `X-Deep-Agent-App` / `X-Deep-Agent-Language` 穿过平台网关；后端再转成 MCP 所需的标准 `app` / `space` / `language`，不要从浏览器直接发送会触发网关路由校验的 `app` Header。
- 控制台 API 根地址统一由 `api.js` 基于 `document.baseURI` 的上一级动态解析；本机 `/console/`、平台多级代理 `<服务前缀>/console/` 共用同一规则，其他模块不要自行拼接域名或服务前缀。
- 调试 App 变化会停止当前流并重新加载页面状态；旧缓存中的 Space、Language 和自定义后端地址会被清理。
- 控制台请求固定使用当前同源后端。本地联调直接访问本机 `8000/console/`，不在浏览器中切换后端地址。
- SSE 解析、HITL 参数归一化和 Schema 文本校验分别位于 `protocol.js`、`hitl.js`、`schema-editor.js`，可由 Node 内置测试独立验证。
- 控制台测试：`node --test tests/frontend/console/*.test.mjs`。
- 全局配置上下排列Web Search、紧凑数据库摘要和运行设置：Web Search默认只回显一个当前Provider选择，点击编辑后在标准Console弹窗中维护当前模型/指定Provider及当前Provider必填字段；Provider与模型同排并使用Console自定义下拉，DeepSeek/Kimi可选择沿用同类型会话Provider或单独配置API Key；条件字段直接落在弹窗正文，不增加装饰性配置卡片；Fetch无配置不展示；运行设置沿用原安全白名单。数据库摘要通过 `GET /api/v1/database-info` 只展示类型、主机、端口、库名或 SQLite 文件名，不返回凭据、连接参数或本地完整路径。Provider详情默认撑满剩余页面高度。会话标题使用首轮实际模型并通过`session/title`事件更新，不提供独立模型入口或轮询刷新。
- “内置工具”位于模型之后，在同一目录中分别展示代码随版本发布、由Agent显式配置的工具包，
  以及运行时自动注入的系统工具。页面没有注册、编辑、删除、全局启停或重新加载入口；系统工具
  也不得进入 Agent 能力勾选。
- Console 顶部只展示一处产品版本，通过同源 `GET /info` 读取
  `deep_agent.__version__`；配置中心的数据库结构 revision 是独立运维信息。
- Markdown统一由`markdown.js`渲染：工作台最终回复和Skill资源预览复用同一套安全解析；
  `mermaid`围栏只在完整回复落定后按需加载本地vendor并以严格模式生成SVG，失败时保留源码。
- 工作台“轨迹”页与对话共享同一Session，不创建Run或模型请求；Event API只暴露公共Event Payload，
  认证信息、内部Search Text与敏感Provider字段不展示。`session_event_appended/updated`仅更新轨迹，
  刷新后以完整Event分页结果校准。
- 配置中心左侧页签只在当前前端页面的模块内存中保留；工作台与配置中心之间切换会恢复上次页签，浏览器刷新后重新回到 Agent，不写入 LocalStorage 或后端。
- Agent 独立编辑页在标题区显示完整 Agent ID；测试工作台对话标题第二行以 `会话 ID：<完整 ID>` 展示当前会话，不再截取前八位。
- Agent 卡片和列表标题旁以轻量“渠道”图标文字回显已有 Channel Account 关联，悬停显示渠道名称；
  该信息只由渠道账号派生，不增加 Agent 表单字段或反向绑定。停用渠道仍保留关联标记，渠道改绑/删除
  刷新时同步两个视图；渠道信息读取失败不阻断 Agent 目录，也不继续展示过期标记。
- `/` 跳转到 `/console/#/workspace`；旧 `/settings.html`、`/models.html`、`/admin/` 返回 404。
- 前端门禁只负责体验，后端管理Router仍统一校验企业管理员；不要把按钮隐藏当作权限控制。

---

## SSE 对接

主接口：`POST /api/v1/chat/stream`

| event | data |
| --- | --- |
| `session` | `{ session_id }` 或含 `agent_id` |
| `context_compaction_progress` | `{ type, persisted:false, event_id, phase, elapsed_ms, attempt }`（瞬时状态） |
| `context_compaction_result` | 主动压缩最终结果；成功、无需压缩或失败，不作为聊天消息 |
| `session_event_appended` | `{ type, persisted:true, event }`；按Event ID插入并按Sequence排序 |
| `session_event_updated` | `{ type, persisted:true, event }`；按相同Event ID原位更新 |
| `session_title` | `{ type, persisted:true, session_id, title, source }`；更新标题，不轮询 |
| `text` / `thinking` | `{ type, item_id, delta }` |
| `tool_call` | `{ type, item_id, tool, args, tool_call_id, widget?, metadata?, source? }` |
| `tool_result` | `{ type, item_id, tool, result, tool_call_id, artifacts?, plan? }`；规划结果携带计划快照，大结果外置时保留预览 |
| `tool_call_removed` | `{ type, tool, tool_call_id, reason }`；删除对应实时工具块，不写入Event Store |
| `final` | `{ type, status, output, usage, deferred?, context? }`（`context` 为预算/压缩观测） |
| `error` | `{ type, message }` |
| `cancelled` | `{ type, status, usage, history_persisted }`；保留当前流式内容并标记中断，重新打开会话时由History校准 |

完整协议（含同 ID 多次推送、生命周期）：[`../docs/guides/sse-protocol.md`](../docs/guides/sse-protocol.md)。

实现参考：`console/chat.js` 与 `console/protocol.js`。

自动和主动上下文压缩复用消息区中的同一套独立过程条，不挂在助手消息内部。状态延迟
150ms显示；Running与终态使用同一Compaction Event ID，轨迹在真实Sequence原位更新。普通历史聊天
默认隐藏完成标记，直播状态和轨迹始终可见；失败终态不伪装成有效摘要锚点。

v0.3.4 的输入框在附件线性图标旁提供主动压缩线性图标。按钮状态、上下文百分比和 tooltip
只读取后端 `context-status`，不得在前端复制 Token 估算、动态输出预测或安全余量算法。
前端不得复制压缩边界、Token估算或目标复检算法。只在无会话、当前Run/压缩进行中或等待Deferred
等技术冲突时禁用主动入口；后端结果通过同一Event和`context-status`校准。

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
| 模型运行属性 | `console/configuration/llm-models.js`；启停、上下文、最大输出、输入模态和 tags 保存到 `llm_models` |
| 模型目录 | `console/configuration/catalog.js` → DB-only GET、管理员联网 refresh 与JSON import/export |
| 上下文压缩观测 | `console/chat.js` 拉取 session context-status；`console/workspace.js` 在右侧会话详情泳道展示 |
| 上下文压缩测试会话 | `console/workspace.js` → 场景弹窗、创建后打开会话并预填验证问题 |
| Markdown 主题/安全 | `vendor/`，见 `vendor/README.md` |

普通历史由后端按`session_id`投影；切换会话时一次读取`GET /api/v1/sessions/{id}/history`。
SessionInfo 的可选 `channel` 是历史会话的渠道来源快照：列表提供来源图标提示，详情展示渠道名称和
外部 User ID/Chat ID；缺省不增加展示。不得用当前 Binding 反推旧会话来源，也不从会话标题猜测渠道。
`GET /api/v1/channel-commands` 只用于帮助展示，即使 Gateway 未部署也应可读；Console 不拦截渠道命令。
只有轨迹循环读取`GET /api/v1/sessions/{id}/events`直到`next_after_sequence=null`。
Pending UI工具的`tool/call`Event仍在Payload中携带`widget/metadata`。

Console会话渲染模块边界：

- `conversation-items.js`：把History `ConversationItem[]`确定性投影为用户节点和Assistant节点。
- `assistant-presentation.js`：把History节点和实时SSE统一转换为Presenter展示动作。
- `chat.js`：负责请求编排、Session/附件/Context状态、DOM Presenter和副作用，不再维护第二套事件投影。
- `trajectory.js`：独立消费Event API，不参与普通聊天消息投影。

---

## 陷阱

- `file://` 打开可用但有限制；推荐静态服务器
- 生产环境应收紧后端 CORS `allow_origins`
- `tool_call` 首帧 `args` 可能为 `{}`，需按 `tool_call_id` 合并后续帧
- 配置表单必须区分“主体保存失败”和“保存成功后的列表刷新失败”；后者保留成功结果并提示重新加载，不能让用户按新建模式重复提交。条件字段（如 URL MCP 的 URL）需要前端动态必填和后端 Schema 双重校验。
- Agent 的 `capabilities` 和 `skills` 都是非空显式列表；新建表单必须按 `[]` 初始化，不得自动勾选任何资源。删除 MCP、UI 工具包或 UI 工具若返回 `detail.referenced_by`，应直接展示引用它的 Agent 名称。
- `builtin__confirm`、`builtin__choice` 同属 `ui:builtin` 工具包；Agent 新建和历史回填不得自动勾选。choice 始终提供候选外自由输入，提交结果固定包含 `selected: string[]` 与 `custom_input: string | null`，二者不能同时为空。
- MCP 新建页只允许 URL transport；历史 stdio 配置只读且不显示编辑入口。Builtin MCP 继续由仓库内程序配置管理，列表保留编辑入口但不显示删除按钮，后端删除接口也会拒绝。
- 用户消息下方只提供复制可见正文的图标操作，不提供编辑/截断历史入口；附件、Context Reference和隐藏元数据不得拼入复制内容。
- URL MCP 的普通编辑名称只读；改名必须走列表中的独立“重命名”操作。成功后 Console 同步刷新
  MCP、工具包与 Agent 能力目录；Builtin 和历史 stdio MCP 不显示重命名入口。
- 模型目录只由管理员手工联网刷新models.dev，或导入models.dev官方`catalog.json`；保存模型配置不得触发外网请求。目录按 Lab → Model → Provider 展示，仅负责预填；模态与能力 tags 分开，Agent 运行时只认 `llm_models`。
- v0.6.4起Provider详情中的模型Switch直接维护`llm_models.enabled`。配置中心请求
  `/llm-models?selectable_only=false`以保留停用模型的管理入口；Agent和聊天选择器使用普通
  `/llm-models`，不得在前端自行复制可用性判断或重新显示停用模型。
- v0.6.5 Web Search表单只有一份当前生效配置。API Key未重填时发送空值以保留同Provider旧Key；
  切换Provider必须清理无关模型和密钥状态。连通性图标测试当前草稿，在当前弹层内就地显示测试中、
  成功或脱敏短错误，不使用会落在弹层背后的全局Toast，也不展示测试问题或结果正文。
- v0.6.6的`web_image_view`结果在聊天工具卡和轨迹详情中复用同一URL预览；必须懒加载、
  `referrerpolicy=no-referrer`并保留“打开原图”失败回退，不上传附件、不代理下载、不写Base64。
- 上下文压缩在模型配置有效上下文窗口时自动启用；Console只读展示全局策略、预算和Compaction状态，
  不提供启用开关，也不编辑摘要正文。
- Console 从 `/uploads/capabilities`读取实际 MIME、单文件大小和单次数量限制；新请求不得再发送
  `extracted_text`。附件与 Skill正文统一由系统 `read_file`读取，前端只展示普通 Tool Call/Result，
  无需配置或新增专属事件。工具分页协议和解析边界见
  [`../docs/design/06-attachment-parsing/`](../docs/design/06-attachment-parsing/)。
- “新建测试会话”是管理员控制台的常驻入口；它位于会话搜索框和普通新建按钮之间，
  当前 Agent 的模型未配置上下文窗口时不可用，但不能改变普通新建行为。场景只构造合法
  历史，不在前端拼接或伪造消息 payload。

更多说明：[`README.md`](README.md)。
