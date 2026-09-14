# AGENTS.md — 前端

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端 API：[`../src/deep_agent/AGENTS.md`](../src/deep_agent/AGENTS.md)

纯静态 HTML/CSS/JS，**无构建步骤**。后端将 `console/` 挂载到 `/console`，根路径跳转到 Console。

Console 图标统一使用仓库内 `console/vendor/lucide.min.js` 的线性 SVG。静态模板使用
`data-lucide`，动态插入 DOM 的图标必须在插入后调用 `window.lucide.createIcons()`；不要重新
引入 emoji、字符图标、外部 CDN 或第二套图标库。

修改 Console 页面结构、控件或样式前，必须先读
[`../docs/guides/console-ui-guidelines.md`](../docs/guides/console-ui-guidelines.md)，并在当前页面和相邻页面中
找到可复用模式。没有独立层级、滚动、选择、状态或安全边界的字段组不得新增装饰性容器。

## Console UI 开发分支

Bundled Console 与后端统一在 `feat-ruge-0803` 主开发分支维护，不再保留独立 UI worktree。
每批 UI 需求开始前先确认分支和工作区，保留已有改动；修改范围通常限定在
`console/**`、`tests/frontend/console/**`，必要时同步前端说明。若需求实际需要后端
API、SSE、Event Store或持久化契约变化，应先确认产品边界，再修改对应拥有层。

---

## 文件结构

```
console/
├── index.html           # 唯一页面：测试工作台 / 配置中心 / 诊断中心
├── chat.js              # SSE、会话、附件、HITL
├── conversation-presenter.js # 对话测试 / 诊断共用正文与工具展示
├── session-inspector.js # 共用右侧会话信息栏，只读数据注入
├── workspace.js
├── diagnostics-center.js # 诊断入口、白名单路由与返回状态
├── diagnostics-sessions.js # 会话概览 / 列表 / 独立只读详情控制器
├── diagnostic-detail.js # 只读详情宿主，不导入工作台执行控制器
├── diagnostics.js      # 紧凑日志列表、原始文本与右侧字段详情
├── configuration.js
├── configuration/       # Agent / 模型 / MCP / Skill / UI 工具管理
└── vendor/              # marked、DOMPurify、highlight.js 等预构建库
```

---

## 功能概览

“集成”由 `configuration/integrations.js` 管理，一级导航使用 `puzzle` 图标，复用渠道页的普通表格布局，不展示分组、分割线或二级标题。
条目取自 `GET /api/v1/integrations`，前端将目录条目平铺显示；不补固定条目、不创建初始化记录。按集成编码维护编辑器与摘要渲染，
当前两个集成均通过现有 Agent 下拉选择，表格“配置”列显示 Agent 名称；JSON 是底层存储，不向用户提供通用 Key/Value 编辑器。
配置和接口示例放在同一个固定尺寸左右分栏弹框，左侧 40% 配置并解释关联 Agent 的用途，右侧 60% 在填满剩余高度的同一个代码框展示请求与返回；接口条目使用 `code`、`settings`。API 示例仍与 OpenAPI 共源。
切页和关闭弹框中止读请求并忽略迟到结果，保存期间阻止重复提交和离开编辑器；30秒无响应时中止客户端请求、解锁并提示结果待确认，需重新打开集成页核对。契约见
[`../docs/guides/integrations.md`](../docs/guides/integrations.md)。

观察页面先读取 Run Status；已有活动 Run 时，`session-observer.js` 通过带固定 `run_id` 的只读
`GET /sessions/{id}/live`接收原有 SSE 实时变化，当前 POST SSE 页面不重复订阅。
连接先建立、随后读取 History；按服务端 Run 起点 Sequence 和目标 Run ID 排除待重放内容及后续 Run，
保留已保存用户输入，串行重放后继续接收增量。终态后忽略迟到正文，`observation_end`校准历史后关闭。
此连接只观察打开时已在运行的那次执行；空闲会话不订阅、不自动跟随企微后续新消息。
断线仅重连同一 Run；无重放缓存时回退 `history-follower.js`串行读取该 Run 的状态与 History，
不得因状态返回新 Run 而继续跟随。正常终态 History 成功读取后才解锁。
切会话/Agent/卸载必须中止请求且忽略迟到结果，刷新正文保留滚动和未变化 DOM 的折叠状态。
History 中运行工具一律只读，不能因工具目录补齐 widget 自动挂 HITL；只有正式 awaiting_input 可交互。

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
| 诊断日志 | `diagnostics.js`／`diagnostics-query.js`使用管理员只读`/diagnostics/logs/status`与`/diagnostics/logs`，查询当前实例API／网关的管道分隔文本日志；不读会话事件、不查询任意文件路径 |
| 诊断会话 | `diagnostics-center.js`／`diagnostics-sessions.js`使用管理员`/diagnostics/sessions`五个GET接口，跨用户概览、列表和完整只读详情；统计按平台账号归属，含渠道会话但不解析企微发送人 |
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
v0.8.5不再投影RUN快照或保存回执；执行异常按`execution/error`展示“异常”和脱敏原因、分类、阶段。
普通完成或用户取消不增加独立轨迹行；`error/cancelled` SSE只收口当前内容，异常行来自持久事件，
避免同一次故障被SSE与Event重复展示。旧RUN数据不做特殊隐藏、迁移或恢复兼容。
真实消息part事件通过`run_id + payload.source.message_ordinal`归组助手Loop，按`part_index`排序；
工具调用与结果仍以真实事件关联独立展示，不改变普通History轮询或人工操作权限。
同组助手行ID固定为最早Sequence的源事件ID，后续part追加不换ID；`native.event_ids`保留组内全部
源事件ID，模型usage是该消息的完整元信息而不是按part相加。已落库工具后的同名SSE帧复用该调用行。
不同助手Loop复用`tool_call_id`时，以调用源事件和part位置区分轨迹行，结果配最近调用，详情链接
按助手Loop来源匹配；不能用全会话call_id去重而覆盖早轮结果或抑制后轮SSE。
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
单包下载遵循后端 Content-Disposition，文件名为 `<code>-v<version>.zip`；无版本只用 `<code>.zip`。
仅调整下载名称，原始 ZIP 字节和上传安装契约不变；“导出全部”的外层 ZIP 需解压后逐包安装。
顶部“上传安装”先打开说明弹窗，可多选ZIP并逐包POST安装，逐项展示成功、冲突、失败或结果待确认；
不自动覆盖已有编码，不自动重试不确定请求。详情“上传”仍单包替换。上传期间阻止重复提交和关闭弹窗。
单包前端等待上限120秒，超时标记待确认并停止后续包，不声称后端已取消；HTTP 5xx/408也不视为确定未安装。
安装收尾后立即解锁弹窗，后台列表刷新不能延长安装锁。

Provider 删除须确认同时删除其模型配置，允许删除最后一个；真实 Agent 或 Web Search 引用须先解除，失败不得部分删除。空列表仍可新建 Provider。渠道 Secret 复用 API Key 掩码样式，不提供清除；原掩码或留空均保持现有 Secret，只提交新输入。

Provider“关联模型”操作区在“从目录添加”左侧提供“测试模型”，只串行测试当前Provider已开启模型。
使用管理员`POST /llm-models/{id}/test?provider_id=…`做小型真实请求，不经过Agent/会话工具循环，
不保存结果、会话或事件。结果以紧凑文字位于开关左侧，失败原因悬停可读；刷新清空。
切Provider/目录/页面时中止前端批次、忽略迟到结果，不自动重试或改变启停；后端单项30秒上限。
中止时同步清理按钮和行内等待状态，不能依赖下一次详情重绘。所有探针专属SDK客户端均关闭自动重试，正常Agent客户端不变。

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
设置之后的“诊断”分组只提供日志和会话；底部“系统”分组提供全局配置、模型目录和接口文档；下方以一条分隔线划分用户栏；展开导航时四选一主题栏位于用户名
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
搜索使用同一后端列表接口的`query`：名称字面子串（不区分大小写）或完整Session ID精确匹配，
不搜索正文、不扩大当前用户/Agent范围；250ms防抖，改条件立即取消旧请求、重置分页并忽略迟到结果。
查询结果以服务端命中ID为准，不让当前打开的会话快照混入不匹配结果；清空条件重新加载首屏。
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
MCP配置表单与标题栏居中、最大1080px，窄屏同一表单改为单列；不保留装饰性的分组标题和纵向分隔线。
Headers JSON使用透明输入层与语法高亮层重叠，两层及高亮token的字号、字重、行高、换行与滚动宽度
必须一致；语法仅改颜色，不能继承普通字段标签的13px样式或只读JSON的粗体。高亮层不参与内容撑高。

`console/` 是不依赖构建工具的 ES Modules 页面。它复用现有聊天和配置 API，但不读取旧设置页的 `da_request_headers`：

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
- 只读SSE须覆盖历史屏障、当前Run重放去重、逐段输出、切页Abort无取消副作用、固定Run重连、
  有界缓冲回退、终态迟到帧、终态History失败不解锁及不自动观察后续Run。
- 增量事实轨迹测试须覆盖同轮part乱序/分批到达、助手行ID和来源ID稳定、usage不重复累计、并行工具
  先后返回、持久事件先于工具SSE无重复、异常SSE无重复异常行、取消无回执，以及旧RUN旁正常历史可读。
  History跟随仍须测试轮询串行、切会话忽略迟到结果、终态History失败不解锁、运行工具禁止提前交互。
- 全局配置上下排列Web Search、紧凑数据库摘要和运行设置：Web Search默认只回显一个当前Provider选择，点击编辑后在标准Console弹窗中维护当前模型/指定Provider及当前Provider必填字段；Provider与模型同排并使用Console自定义下拉，DeepSeek/Kimi可选择沿用同类型会话Provider或单独配置API Key；条件字段直接落在弹窗正文，不增加装饰性配置卡片；Fetch无配置不展示；运行设置沿用原安全白名单。数据库摘要通过 `GET /api/v1/database-info` 只展示类型、主机、端口、库名或 SQLite 文件名，不返回凭据、连接参数或本地完整路径。Provider详情默认撑满剩余页面高度。会话标题使用首轮实际模型并通过`session/title`事件更新，不提供独立模型入口或轮询刷新。
- “工具”统一提供 UI / 内置切换，默认 UI；新建工具包仅在 UI 视图显示。内置视图展示代码随版本发布、由Agent显式配置的工具包，以及运行时自动注入的系统工具；该视图没有注册、编辑、删除、全局启停入口，系统工具不得进入 Agent 能力勾选。
- Console 顶部只展示一处产品版本，通过同源 `GET /info` 读取
  `deep_agent.__version__`；配置中心的数据库结构 revision 是独立运维信息。
- Markdown统一由`markdown.js`渲染：工作台最终回复和Skill资源预览复用同一套安全解析；
  `mermaid`围栏只在完整回复落定后按需加载本地vendor并以严格模式生成SVG，失败时保留源码。
- 工作台“轨迹”页与对话共享同一Session，不创建Run或模型请求；Event API只暴露公共Event Payload，
  认证信息、内部Search Text与敏感Provider字段不展示。`session_event_appended/updated`仅更新轨迹，
  刷新后以完整Event分页结果校准。
- 轨迹中的已保存`assistant/message`不能因只有tool-call、thinking/text为空而隐藏；
  同一响应按message ordinal合并Part，`native.event_ids`保留所有来源ID，摘要显示调用工具数。
  工具交互仍独立展示；页面和脱敏导出复用同一投影，不伪造正文。
- 配置中心左侧页签只在当前前端页面的模块内存中保留；工作台与配置中心之间切换会恢复上次页签，浏览器刷新后重新回到 Agent，不写入 LocalStorage 或后端。
- Agent 独立编辑页在标题区显示完整 Agent ID；测试工作台对话标题第二行以 `会话 ID：<完整 ID>` 展示当前会话，不再截取前八位。
- Agent 卡片和列表标题旁以轻量“渠道”图标文字回显已有 Channel Account 关联，悬停显示渠道名称；
  该信息只由渠道账号派生，不增加 Agent 表单字段或反向绑定。停用渠道仍保留关联标记，渠道改绑/删除
  刷新时同步两个视图；渠道信息读取失败不阻断 Agent 目录，也不继续展示过期标记。
- `/` 跳转到 `/console/#/workspace`；旧 `/settings.html`、`/models.html`、`/admin/` 返回 404。
- 前端门禁只负责体验，后端管理Router仍统一校验企业管理员；不要把按钮隐藏当作权限控制。
- 诊断日志进入页面才加载，查询不带调试App，不按当前用户限制Session／Run；只允许后端企业管理员。
  默认最近24小时、每页200条，手动筛选/刷新/继续查询，页面最多保留5000条；不增加后台轮询或 Agent工具。换条件与离开页面立即Abort并失效旧请求；分页固定原时间范围，409游标失效提示重查。
  API／网关日志使用独立运行诊断文件，不把Run回执重新写入会话Event Store。详情只展示后端安全标量，
  HTML转义、不渲染Markdown；日志不可用、缺失/丢弃、部分扫描、权限失败不能伪装成没有匹配结果。
  2026-09-10 后续按用户要求将两个查询页标题与字体收回Console规范：页面标题16px、分组14px、
  正文12–13px，不再显示英文eyebrow。两页整体底色直接继承Console surface，light为白底；日志行24px、列表统一12px，左侧当前查询序号从1开始、追加加载接续，右侧Session ID仅前8位（跳转仍完整ID），
  无状态圆点/逐行线/表格网格；顶栏32px，第一行时间/来源/等级/Session ID，第二行关键词/查询/文字重置；共用640px最大宽度，左右对齐，不用高级展开或浮层。760px以下首行改两列，避免溢出。
  日志只保留一个“查询”（同时更新相对时间窗口），重置为无边框文字。自定义时间、返回、分页保留固定范围。
  日志底部不设重复计数栏；已加载数量、部分扫描/上限提示、加载更多统一在“查询结果”旁，日志保留策略置于数量提示title。
  详情默认436px、可调宽，左侧标签12px并按内容紧凑排布、右侧值12–13px，列间距8px；已知字段名使用中文标签、未知字段保留原名，完整raw默认折叠，仅Session ID可复制并跳转，
  不展示重复JSON或大号关联按钮。窄屏仍用同一布局，先压缩次要列与宽度。
- 诊断会话默认最近7天，UTC半开范围最多31天，按浏览器时区分日；概览和列表共享公共筛选，
  列表默认50条；完整Session ID存在时精确匹配，否则名称字面量模糊匹配；不做ID前缀/包含匹配，
  不按ID格式猜测搜索类型（允许业务自定义ID）。筛选不加外层表单卡；指标独立卡片、图表及列表
  保留白色工作区、Console控件及必要边界，不再把“少边框”理解为全部无框；表格保留表头、外框和行线。
  概览/列表直接复用Agent的`agent-view-switch`样式和图标，固定第二行；第一行“查询”右边常显文字“重置”。
  第二行集中时间/条件回显与自定义时间，header预留固定行高，加载提示不得挤动内容起点。
  概览/列表加载提示放在第二行左侧普通文档流中，空闲隐藏文案；不得在表格或筛选工具条右上方绝对定位覆盖控件。慢请求回归需拦截并延迟列表响应，核对加载前后表头坐标不变。
  最近提问38px单行，右侧“Agent · 用户名 · MM-DD HH:mm”自然宽度整体右对齐，完整时间保留title；会话列表50px双行，次行“复制＋Session ID前8位 · 最近问题”，不加“最近问题：”前缀，ID与问题同色。
  列表分页与每页条数放在表格底部右侧，去除组合外框及多余分隔线；每页菜单向上展开，表格滚动区填满分页之上的剩余高度。表头6列，移除末尾进入图标，行点击进入详情。`diagnostic-columns.js`以相邻列互补方式有界拖拽，支持键盘方向键，离页释放监听。
  概览只接收Agent/时间/来源条件；搜索框在左侧，用户与状态在同一行右侧且无边框，与关键词/状态/下钻条件同属列表私有状态，切概览不传入URL或聚合请求。筛选两行固定高度但间距紧凑；查询输入不叠加紫色焦点光圈。复制Session ID成功后原位显示Lucide小勾1500ms，再恢复复制图标，同时保留共享Toast；失败不显示小勾。默认会话/Agent/用户按2.6:1:1分配弹性空间，避免会话列独占宽屏。
  趋势按容器实测宽度重算横坐标，保持184px高、固定文字/圆点尺寸；ResizeObserver离页必须断开，不触发数据请求。
  “范围内有执行异常”不等同“最近一轮异常”，提问数下钻的列表仍按会话计数。
  用户只显示平台User ID对应用户名，不显示头像/邮箱；姓名搜索仅过滤已加载选项，实际查询提交ID；
  不依赖测试App或企微发送人接口。
- `diagnostics-navigation.js`负责白名单路由、完整ID、范围和返回快照；`diagnostics-session-query.js`
  负责可取消读取和Events分页。`diagnostics-sessions.js`使用独立状态，History/Events/上下文估算各有请求代次，
  切会话、刷新、离开与鉴权失败均取消旧读取并拒绝迟到响应。URL仅保存筛选/定位ID，正文、日志、头像和认证
  不写URL或LocalStorage；来源/滚动/展开仅当前历史与内存保存，鉴权失效清空诊断数据。
  会话列表表头/分页留在工作区内；返回同时恢复外层、表格和概览内部滚动，不能只保存外层位置。
- `diagnostic-detail.js`注入共享正文Presenter、`trajectory.js`和`session-inspector.js`，不导入会触发
  初始化的`chat.js`/`workspace.js`。只读页不挂载发送、上传、重试、停止、审批、改名、删除、压缩或widget
  提交处理器，不调用普通用户接口伪装所有者，不订阅SSE/轮询。Events仅在轨迹或轮次定位需要时按500条分页，
  History投影失败保留显式错误及轨迹入口。上下文仅按需估算并标注当前配置，不拿管理员测试Header补历史。
  元数据/上下文迟到后重绘正文必须保留已定位消息的高亮与滚动，不重复滚动或遗失定位。
- 所有诊断下钻和返回按[联动矩阵](../docs/guides/diagnostic-sessions.md#页面联动)维护并测试。
  会话详情按最新要求不新增反向查日志按钮，完整骨架复用对话测试；日志跳会话不受旧列表条件限制。
  完整ID用于查询，缩略仅展示；日志Run ID保留原值，不支持页面筛选/跳转，日志跳会话只传Session ID，旧log路由run_id忽略。
  会话内部的真实问题/异常事件定位不受影响。复制不得冒泡触发跳转，返回恢复分页和滚动。
- 这两个查询页面使用限定作用域的`diagnostics-design.css`（直接映射Console主题变量）以及各自CSS；不全局导入
  DevBox样式，也不影响会话详情和其他目录。旧视觉依据、生成提示词、合成截图/逐点比较保存在
  `docs/design/23-diagnostics-and-logs/visual-rebuild-20260910/`；图片中的演示计数不能进入生产。
  可复跑`node tests/frontend/console/diagnostics-visual-review.mjs`：默认隔离路由夹具；`--mode live`
  仅同源GET/HEAD，禁止执行请求和外网。浏览器必须finally关闭，截图通过不等于像素完全相等。

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

从仓库根使用稳定本地 Profile 启动后端：

```bash
# 仓库根
PYTHONPATH=src python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/sqlite.env --port 8000
# → http://127.0.0.1:8000/console/
```

Test 联调改用 `~/.config/deep-agent/test.env`。页面固定请求当前同源后端，品牌处连接状态表示
`/health` 是否正常；完整 Console 联调使用后端提供的静态路由。旧 `frontend/` 已移除，
不再提供旧目录预览命令；`console/` 与 `console/vendor/` 是唯一产品资源。

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
- `conversation-presenter.js`：共用正文、思考和工具DOM呈现，通过宿主和能力注入支持工作台与诊断；导入不触发业务请求。
- `session-inspector.js`：共用概览/当前/上下文与已有渠道信息，只读取注入的会话数据；不取管理员当前测试身份替代历史。
- `chat.js`：保留工作台请求编排、SSE、Session/附件/Context状态和执行副作用，使用共享Presenter，不维护第二套事件投影。
- `diagnostic-detail.js`：独立只读宿主，复用以上呈现与轨迹，不复制整页工作台或挂载可写工具widget。
- 诊断页面视觉以 `docs/design/23-diagnostics-and-logs/interactive-prototype` 的 L4/S1/S2 为基线；
  表格保留外框、表头与行分隔，图表保留坐标与参考线，不把减少装饰理解成全面去边框。
  用户信息只显示用户名，资料缺失回退真实 ID，不再显示头像或邮箱。详情严格复用对话测试模板与信息栏，
  不新增用户卡、轮次日志栏或跳日志按钮。日志右上筛选首行时间/来源/等级/Session ID，次行关键词/查询/重置，不提供Run筛选；
  详情优先会话和执行结果，服务及其他字段紧凑左右平铺，不折叠，仅 Session ID 保留复制。
- `trajectory.js`：独立消费Event API，不参与普通聊天消息投影。诊断复用导出按钮，仅完整读取后允许导出脱敏history轨迹。
- `session-view-shell.js` 从原始工作台模板取只读骨架；标题、按钮、正文间距在 `console.css` 共享选择器维护，禁止在诊断另复制一套外观。定位消息保留准确滚动，使用小标签而非整块描边。

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
  [`../docs/design/06-attachment-parsing/`](../docs/design/06-attachment-parsing)。
- “新建测试会话”是管理员控制台的常驻入口；它位于会话搜索框和普通新建按钮之间，
  当前 Agent 的模型未配置上下文窗口时不可用，但不能改变普通新建行为。场景只构造合法
  历史，不在前端拼接或伪造消息 payload。

更多说明：[`README.md`](README.md)。

### 会话归属与测试上下文

会话概览的 App / Space 只展示会话主表创建时保存的 `app_id / space_id`，缺失显示横杠“—”，不得用当前测试上下文兜底。当前请求的 App、Space、Language、User ID 集中在顶部“调试”按钮及其浮层中；普通会话列表和详情接口均返回原始归属。

轨迹类型和详情页签仅做中文展示映射；执行状态保留原来的英文 Completed、Running、Pending、Interrupted、Failed，内部 kind、协议和原始数据保持不变；计划显示“计划”，run_code 标签保留 PTC。上下文引用保留在用户输入详情中，不再额外生成独立 CONTEXT 轨迹行。

空值按字段保留既有中文或横杠及原有字体颜色，不统一替换为 `No`，数值 0 保留。会话概览只显示 Agent 名称，隐藏内部 Agent ID；不展示后台 Token 估算方式。聊天正文统一 12px。

Agent 编辑器不展示 Temperature 配置，创建和编辑请求不主动设置或清空该字段，已有后端值及复制行为保留。

公共弹框沿用原有主题变量、控件高度和间距；`ui-dialogs.css` 仅维护布局辅助规则；字段短说明放标签右侧，长说明复用 `ui-field-help.js` 的问号提示。普通单选 select 由 `ui-select.js` 自动增强，原生元素保留表单与事件契约；不要另写原生下拉外观或逐页复制自定义菜单。具体结构和验收见 UI 实施规范第 10 节。顶部“调试”在未选 App 时回显“不分应用”。

内置工具左侧按“预制工具 / 运行时工具”分为两个卡片组，共用右侧详情；运行时条目额外展示实现来源和注入条件，沿用只读目录接口。沙箱显示名称统一为“沙箱”。UI 工具编辑为 560px 单列，名称编辑时只读；Widget 使用 `ui-editable-select.js` 在同一输入中完成推荐、自定义与清空，协议值不翻译；三份 Schema/Metadata 用页签切换并保留草稿。

模型、Skill、内置工具、UI 工具的主从布局统一由 `ui-resource-layout.css` 维护：左侧卡片 54px 高、模型和 Skill 桌面宽 276px，内置工具和 UI 工具宽 221px，统一分组间距且不显示右箭头；选中态不加左侧标线。右侧取消详情外框，以顶底各留 8px 的竖线分隔，使用现有主题变量。

“工具”是 UI 工具与内置工具的统一导航入口（`panel-tools` / `configuration/tools.js`）。标题同行的图标分段切换默认 UI 工具在左，内置工具在右；切换左侧的“新建”仅在 UI 视图显示，沿用新建工具包表单。子视图保留各自选择状态和控制器，不使用全局 `.tab` / `.panel` 类，以免与导航切换相互干扰。旧 `ui-tools` / `builtin-tools` 导航记忆映射到 `tools`。

日志提供“列表 / 原始”切换，默认列表；列表只显示后端写入时生成的正文，附加字段放入详情；会话 ID 固定显示前 8 位无省略号。详情使用集中中文字段标签，未知标签保留原名，值不翻译；只有会话 ID 保留复制与跳转，原始日志默认折叠、展开带边框，不增加业务分组。原始视图为11.5px等宽只读文本框、不自动换行，自动分页加载当前固定查询范围；取消、换条件、离开时中止请求并忽略迟到结果。原始视图和导出共用 `diagnostics-export.js`，上限5,000条 / 10 MB / 100页 / 60秒，明确提示加载、失败和截断。导出先显示预计条数，确认后下载实际 `raw` 拼成的 `.log`；文件不插入页面生成的标签或元信息。空结果、权限/游标/实例变化及请求失败禁止下载。模型目录只浏览，添加入口保留在模型配置中。
静态资源发布时同步更新入口、动态模板与传递依赖的版本参数，避免同一模块因旧新URL并存而重复加载。目录筛选加载期间的新请求需排队并忽略过时结果。Widget的往返缓存pagehide仅收起，正式离开再释放监听。

Agent 安装包由 `configuration/agent-packages.js` 管理。第一步只选预制目录或上传 ZIP；第二步配置新建/覆盖、名称或目标、可空模型；第三步按类型表格检查并安装。覆盖必须显式选择目标，不显示名称输入；新建默认包内名称，在第二步下一步时拦截重名。缺失不阻断、冲突阻断，共享更新统一确认，MCP 无映射控件。预检请求携带 mode/target_agent_id，安装 options 只传 digest/mode/target_agent_id/name/model_id。成功后显示独立反馈页。安装和反馈固定同尺寸，错误固定在底部，缺失说明放资源行。超时或中断提示结果待确认，并重新查询 Agent 列表；中止后忽略迟到结果。卡片和列表导出复用 row-action 图标，先确认再下载实际后端 ZIP，点击不进入配置。普通 Agent 配置支持空模型，显式保存 model_id:null。


导出确认弹窗固定860×600（受视口上限约束）；包名与统计同行，资源按Agent、Skill、UI工具、模型、MCP、内置工具分类，每类最多一行，以“类型 / 内容 / 说明”呈现。内容默认最多列出前三项，超出显示+N、悬停完整清单；简短提醒放标题副标题。不要增加提示词折叠或正文小标题。正常安装包目录不显示刷新，只在错误时提供重试。同类弹窗标题图标与通用确认图标统一15px。Agent 不再提供或复制温度字段。


### 弹窗视觉基线（2026-09-12）

普通配置弹窗与公共确认弹窗统一使用 `--console-surface`，与 Agent/MCP 编辑页保持相同主题表面；不得用 `--bg-soft` 或 `--bg-elev` 充当整张弹窗底色。只读字段及表头用中性的 `--console-readonly-surface`，可写字段使用基础表面加边框，提醒/错误使用状态色，不用整张弹窗染色表达主题。图片灯箱保留独立的透明媒体查看方式。

安装弹窗的状态提示固定在滚动正文外、底部操作栏上方，错误不随内容滚走。安装的新建/替换及共享资源策略需独立梳理并确认，不借样式修改改变业务行为。


UI 工具明细表只展示名称、Widget、描述、操作，不展示更新时间。自定义工具点击行进入编辑，内置工具点击行进入只读详情；行支持 Enter/空格，删除按钮独立处理事件，不触发行打开。工具包标题区的编辑入口保持原用途。

Agent UI 工具范围使用 `ui_tool_selection`：无包编码 key 表示整包（含后续新增工具），有 key 表示显式工具名白名单，空数组表示该包不启用任何工具。配置页 UI 能力卡可逐工具调整，保存仅发送已关联包的范围，复制原样保留；导出仍携带完整包定义，并单独展示当前 Agent 的选择范围。

### 安装弹窗轻量调整（2026-09-13）

当前确认流程为：纯安装包选择 → Radio 新建/覆盖及名称或目标、模型配置 → 带状态色的紧凑检查表 → 独立安装反馈。名称/目标与模型使用对齐表单行；不再沿用上一版第二步无表头列表和第三步原地反馈。缺失、额外工具保留、共享影响写在对应行；空值占位不作为菜单选项，新建默认模型不显示外置清空按钮。覆盖隐藏默认模型配置行，提交保留目标值（含空值）；未选择覆盖目标时占位文字使用灰色。所有阶段弹窗保持 860 × 650。后续纯 UI 调整只做一次必要的桌面冒烟，由用户继续验收，不主动扩展移动端或反复回归。

上传 Agent ZIP 后立即调用现有预检，摘要或错误展示在上传框下方，底部留给下一步校验；摘要只显示包名、Skill/UI 工具数量及简短名称，不展示提示词。关闭、换来源或换文件时取消读取并忽略迟到结果；配置后的下一步仍重新预检。缺失 MCP 行应明确提示先在 MCP 中配置服务，再到 Agent 中关联。


### 企微应用与成员缓存（v0.10.1）

渠道一级页面在标题同行右侧切换“渠道账户 / 企微应用”，由 `configuration/channels.js` 和 `configuration/wecom-applications.js` 分别维护；切换保留应用选择与列表滚动。企微应用复用已有资源主从布局，右侧应用名称标题、合并企业 ID 的配置卡及紧凑缓存统计，不展示成员表。应用企业 ID、Agent ID 编辑只读；Secret 只回显接口掩码，空值或原掩码不提交替换。数据写入后回读，刷新任务只轮询后端结果；任务丢失提示“结果待确认”。切页取消读请求和轮询并忽略迟到结果。

渠道 `settings.wecom.user_details_enabled` 缺失或非 true 时关闭；只有开启才必须选择有效应用。编辑保留其他 settings 和企微扩展字段，关闭查询仍可保留应用引用；需要删除应用前可在渠道选择字段“清空”引用。缓存重置保留用户资料及映射、只标记过期；统计通过后端回读，不能由 UI 推测成功。静态模块、模板及入口缓存版本保持同步。沿用现有桌面主从布局断点，不增加专用移动端流程。


企微应用详情操作沿用目录 `.btn`（图标、文字和边框），左侧应用条目整张卡片是选择按钮，不放列表删除入口；首次进入默认选首项，已有有效选择保持，删除只在详情操作区。缓存刷新／重置使用按钮锚定的轻量 Popover 二次确认，切应用或离页收起；“成员缓存”标题旁复用公共 `field-help`，操作结果使用浅底状态提示；测试连接反馈在应用操作栏下，刷新／重置反馈统一在缓存操作栏下。关联渠道直接展示后端 `channels` 名称列表，不从数量反推名称。渠道弹框在会话用户下以“扩展设置”分隔，查询开关和条件应用选择左右两列，关闭仅隐藏右列。


App Secret 的短说明位于标签同行，标题右侧复用 API Key 的 `field-text-action` 文字按钮。“清空”只标记草稿，可撤销；保存发送 `clear_app_secret:true`。手动留空或原掩码仍表示保留；输入新值取消清空标记，仅发送新凭据。取消／关闭编辑器不保存。凭据清空不等同缓存重置；重置仍只使资料过期，保留姓名和 ID 映射。
