# AGENTS.md — 项目索引

> 面向 AI Agent 的渐进式披露文档。**本文件仅为索引**；具体章节见各子目录下的 `AGENTS.md`。
>
> **边界说明**：`deepfos_platform/` 是随仓库发布的平台运行时支撑包，不是
> Agent 能力目录；只在平台配置、客户端、服务发现或部署运行时任务中修改。

---

## 快速上手（30 秒）

| 项 | 内容 |
| --- | --- |
| **是什么** | 通用 AI Agent 全栈：FastAPI 后端 + Pydantic AI 运行时 + 静态前端 |
| **产品版本** | `deep_agent.__version__`为唯一来源；当前`0.6.2`，数据库Head为`010` |
| **启动** | 本地 Test 联调固定使用下方命令；部署入口才使用 `deep_agent.main:app` |
| **测试** | `python3.12 -m unittest discover -s tests -p "test_*.py" -v` |
| **配置** | 仓库根 `.env`（`DEEP_AGENT_*` / `DEEP_AGENT_DBMS_*` / `DEEP_AGENT_SQLITE_PATH`） |

### 本地 Test 联调固定启动命令

用户要求“启动/重启本地 8000”时，在仓库根直接执行：

```bash
# 首次或缺 .venv-sandbox 时：local_dev 默认会自动 setup；也可手动：
# bash scripts/sandbox_venv/setup.sh

python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

- 该配置连接 Test 数据库，并注入本地可信登录身份。
- 默认先执行启动命令，再用 `/health`、`/console/` 和 `/api/v1/health/sandbox` 快速验证。
- 只有端口占用、启动失败、数据库版本不兼容或用户明确要求审计时，才展开额外检查。
- `subprocess-dev` 默认启用；缺 sandbox 解释器时 `local_dev` 默认跑 `scripts/sandbox_venv/setup.sh`（`--no-sandbox-env` 可关）。
- 纯本地 SQLite 可在仓库外 Profile 设置 `DEEP_AGENT_LOCAL_ADMIN_BYPASS=true`；仍需
  `DEEP_AGENT_LOCAL_USER`，但无需 App/Space 或平台 Token。该放行只存在于回环地址
  `local_dev` 的内部 ASGI scope，部署入口和外部 Header 无法触发。
- 纯本地附件闭环必须再显式设置`DEEP_AGENT_FILE_BACKEND=local`和
  `DEEP_AGENT_LOCAL_FILE_ROOT=<独立目录>`。该后端只允许`local_dev + SQLite`，部署/Test默认仍为
  `platform`。文件正文保存为私有Blob，独立`metadata.sqlite3`只存索引；业务API与线上保持一致。

### 稳定 SQLite 本地开发基线

后续日常本地开发、Console 联调和人工验收，不再随任务创建全新的空 SQLite。默认固定复用：

```text
Profile:         ~/.config/deep-agent/sqlite.env
主数据库:        data/deep-agent-local.db
本地文件根目录:   data/deep-agent-local-files/
启动端口:        8000
```

- 这套主数据库以共享 Test 的**配置数据**为基础，保留 Provider、Model、Agent、MCP 和 Skill 等真实
  配置；不复制 Test 会话历史。日常开发应继续沿用其中的本地会话、附件和验收数据，不得因为开始新
  任务就删除、覆盖、重新初始化或换成另一个数据库文件。
- Test 配置发生变化时，使用正式配置备份/恢复工具做受控刷新，并保留本地会话和本地文件；不要直接
  用新空库替换稳定库，也不要把 Test 的聊天历史混入本地库。
- 启动 SQLite 服务前确认 Profile、`DEEP_AGENT_SQLITE_PATH`、本地文件根目录和 Alembic Revision
  都指向这套基线；启动后至少验证 `/health`，并确认 Console 能读取已有 Agent、模型和验收会话。
- 迁移往返、破坏性 DDL、单元测试和隔离集成测试可以使用独立临时 SQLite 与文件目录，但名称必须
  标记 `TMP_to_delete`，测试结束后清理，且绝不能把破坏性测试指向上述稳定库。
- 稳定数据库、本地 Blob 和仓库外 Profile 都是开发运行数据，不提交 Git；除非用户明确要求清空、
  重建或切换基线，否则一律保留。

---

## 子文档索引

按需打开对应目录的 `AGENTS.md`，不必一次读完。

| 你想… | 阅读 |
| --- | --- |
| 改后端、API、能力层、数据库、配置 | [`deep_agent/AGENTS.md`](deep_agent/AGENTS.md) |
| 新增或调试内置 MCP server | [`mcps/AGENTS.md`](mcps/AGENTS.md) |
| 改聊天 UI、admin 或 SSE 客户端 | [`frontend/AGENTS.md`](frontend/AGENTS.md) |
| 写或跑测试 | [`tests/AGENTS.md`](tests/AGENTS.md) |

---

## 仓库结构（鸟瞰）

```
deep-agent/
├── AGENTS.md              ← 本文件（索引）
├── .env                   ← 运行时配置
├── requirements.txt
├── alembic/               ← 数据库迁移（详见 deep_agent/AGENTS.md）
├── deep_agent/            ← 后端 Python 包 → deep_agent/AGENTS.md
├── mcps/                  ← 内置 MCP → mcps/AGENTS.md
├── frontend/              ← 静态前端 → frontend/AGENTS.md
├── tests/                 ← 测试 → tests/AGENTS.md
├── docs/                  ← 说明与设计文档，入口见 docs/README.md
├── deep_agent/builtin_skills/ ← 代码内置只读 Skill
└── deepfos_platform/      ← 平台配置、客户端、服务发现和部署运行时支撑
```

---

## 架构一图

```
HTTP → deep_agent/api/v1 → AgentRunner → pydantic_ai.Agent → Toolsets
                                      └→ SessionStore (postgres/mysql/sqlite)
```

工具包来源：`builtin:general` | `builtin:sandbox` | `builtin:code-mode` | `mcp:*`
（DB + `mcps/` 兜底）| `ui:builtin` | `ui:<package>`。Skill 与 ToolPackage 分离，Agent 通过
`agents.skills` 显式选择，上传包元数据位于 `skill_packages`。

v0.6.1删除目录资源自身的冗余启停层：Provider、Model、Skill、MCP、UI工具包、UI工具和预制
ToolPackage只表达“资源存在且配置有效”。Provider是否可用由类型、地址与凭据等真实配置决定；
Model是否进入Run由Agent或请求级`model_id`选择。Skill和工具是否进入Run仅由Agent的
`skills/capabilities`显式关联决定。新Agent两项均为空，不再通过`null`或默认包隐式继承能力。

### Pydantic AI 运行边界

- DeepAgent 自研 `ToolPackage` 是产品配置单位，负责把分类、展示元数据和恰好一个 Toolset 组织起来；Pydantic AI 的 `AbstractCapability` 只表示压缩、重试等运行扩展，二者不能混用。
- 模型直接接收 Instructions、历史、当前输入和 Tool Definitions。MCP Resource/Prompt 不会自动成为模型工具，必须由宿主读取、注入或显式包装。
- Pydantic AI 负责 Agent loop、参数校验、工具调用、重试、流式事件和 Deferred 原语；DeepAgent 负责 SQL 会话、用户隔离、MCP 配置/Header、HITL、SSE 和 Console。
- v0.6.0将会话域统一为`chat_sessions + chat_session_events`：一个完整Pydantic Message一行；
  Event Store是唯一事实源。模型历史、审计轨迹和面向客户端的`ConversationItem`均由服务端确定性
  投影，不双写History表。Branch不兼容也不预埋。
- v0.6.0增加显式FileStore适配层：Platform仍是默认实现；仅`local_dev + SQLite`可启用本地Blob。
  上传、附件读取、原生图片、Tool Spill、上传Skill和导出共用同一注入接口，业务层不得自行判断
  环境。Compaction增加确定性Attachment Evidence，但文件授权仍只从原始Event恢复。
- v0.6.0上下文压缩采用单条Compaction Event、固定八段摘要、确定性Tool/Context Evidence和DSH式
  超大结果裁剪；300秒总预算内每约10秒发送SSE进度。失败、超时、取消或Stale不替换上一次有效
  历史；预算、安全边界和Evidence准备保持线性并移出SSE事件循环，准备期也持续发送心跳；明确Provider
  Context Overflow只在没有已输出内容或工具副作用时压缩后安全重试一次。
- 结构化`context_refs`完整保存在`user/message`Event；`metadata`只用于历史和前端，不进入模型、
  摘要、搜索文本或Token估算。被压缩前缀生成确定性Context Evidence，精确事实仍从原Event回读。
- 新的运行时横切能力优先评估 Core 的 `PrepareTools`、Hooks、`ToolSearch` 和 Harness 的 Compaction/Overflow；不要默认把逻辑继续写入 `AgentRunner`。
- v0.3.12 对已授权 MCP 使用 Pydantic AI ToolSearch 渐进披露；当前全局候选不超过 10 个时全部直接披露，超过 10 个时词法预选最多 3 个完整 Schema，其余只列稳定协议名并可搜索加载。检索不扩大 ToolPackage 权限。
- v0.4.1 将检索器升级为 `lexical_v2`：复用独立的字段化词法内核，按协议名、MCP标准 `title`、参数和描述分级匹配，并支持拼音与受限错字回退；查询明确写出协议工具名时只返回精确工具。不得加入业务词典或把词法匹配描述成中英文语义翻译。
- v0.4.2 引导本地 `search_tools` 在一次调用中提交最多 3 个互补查询：用户原始语言、简洁英文能力描述和可选协议名。后端只做确定性词法检索，不调用翻译模型；首轮 Top 3仍只依赖原始问题和 MCP元数据。
- v0.4.4～v0.4.5完成下线内置MCP清理；Alembic 005和006依次清理对应预制Server记录与Agent能力引用，历史会话不改写。当前代码内置MCP只保留DeepCube；真实MCP进程回归改用`tests/fixtures/`下的测试专用Server，禁止测试Fixture进入生产扫描目录。
- v0.6.0的Harness Planning只暴露`write_plan`完整替换工具；快照继续走普通Tool Call/Result Event，
  不建Plan表。同一Turn的Deferred新Run从最近成功快照恢复InMemoryPlanStore，新用户Turn从空计划开始。
- v0.6.1将运行基线固定升级到`pydantic-ai-slim 2.35.1`与`pydantic-ai-harness 0.26.0`。OpenAI 3.5、aiohttp 3.14、deepfos SDK 1.1.97和Google Provider的`httpx2`客户端必须联合升级。
- v0.6.2显式引入`builtin:code-mode`：只在Agent关联后注入Harness `run_code`，已授权且已披露MCP保留原生Schema并同时可供Monty组合调用。Code Mode不扩大MCP权限，不可调用Sandbox、History、Planning、UI/HITL、Deferred/Approval、导出、Resource或自身等内部工具；Code-only `load_file_data`仅将当前会话已授权文件转为Run内不透明变量，真实Base64只在MCP调用边界生成。Shell、Playwright、Host FileSystem和FallbackCompaction仍未启用。
- Tool Search 首轮预选只使用聊天 API 的原始非空 `message`；`context_refs.type/label` 仅在纯上下文请求时回退，不能让 Page/Range 标签覆盖用户问题。实时工具若被 `ModelRetry` 或取消撤回，SSE 用 `tool_call_removed` 通知客户端删除临时卡片；取消后的非空部分回答必须以 `assistant.status=interrupted` 恢复。
- 模型目录的 `max_output_tokens` 同时约束普通和流式回答；工具错误只有在模型修改参数或改选工具后可能成功时使用 `ModelRetry`，已确定的下载、解析或上游失败使用 `ToolFailed`，避免消耗工具重试预算。
- v0.3.13 将正式 Run 的官方 `CancellationToken` 传给内部摘要 Agent；取消摘要直接终止 Run，不能转成 Sliding Window 后继续回答。Local/Native 工具配对统一使用 Pydantic AI 2.26 的 Call/Return 基类。
- v0.6.0压缩吸收Harness/DSH的确定性整理与结构化摘要，但Event生命周期、来源Hash、证据索引、SSE
  和目标复检仍由DeepAgent管理。
- v0.3.17 通过 Pydantic AI 官方 `conversation_id/run_id` 参数贯通 SSE、History 和运行日志；同一 Session 共用 Conversation ID，每次发送或 Deferred 续跑生成新 Run ID。Provider 未提供缓存计数时必须显示未知，不能把框架归一化的零伪装成缓存未命中。
- v0.3.18 使用 Harness `Skills` 渐进披露 `SKILL.md`；初始只公开名称和描述，正文由 `load_capability` 按需加载。References/Assets 通过只读分页工具访问；`scripts/` 不向模型披露源码，Agent同时选择含脚本Skill和 `builtin:sandbox` 时可通过统一沙箱执行。Skill 本身不授予工具权限。
- v0.3.19 起 URL MCP 只在显式检测时执行 initialize/tools-list 并保存无敏感信息的工具快照；聊天 Run 用快照完成工具披露，真实调用时才按当前请求 Header 建连。同一 Run 复用连接，缺少或配置失效的快照不进入模型工具列表，必须重新“检测并更新”。Builtin MCP 和 Resource 代理保持原链路。
- v0.4.11补齐URL MCP实际工具调用超时的错误分类：Pydantic AI将协议超时包装为`ModelRetry`时，DeepAgent识别其MCP超时、连接中断或5xx原因并转成脱敏`ToolFailed`，让模型改用其他工具或解释失败；参数、对象选择等可纠正错误仍保持`ModelRetry`，正式取消不受影响。
- v0.4.12增加Console企业管理员兜底鉴权：`systemAdmin=true`按平台综合权限语义映射为内部`enterprise_admin`；固定`admin`账号不特殊放行。管理Router统一依赖同一授权器，聊天、会话、附件和固定Agent读取等运行接口不受影响。
- v0.4.13兼容Canvas MCP通过`CallToolResult.isError`返回的Go `context deadline exceeded`及稳定`CANVAS_ERROR`文本契约；不可纠正的超时形成失败Tool Result并允许Run继续回答，结果未知的修改操作提示先读取Canvas快照。可纠正错误和正式取消保持原语义。
- v0.4.15在URL MCP工具最后一次可纠正重试仍失败时，将最后一条模型可见的业务校验原因收口为失败Tool Result并要求模型结束当前Run；失败调用、Result和最终解释正常进入History。该链路只依赖Pydantic AI原生`ModelRetry → ToolFailed`语义，不额外维护同一Run的工具封锁状态。
- v0.4.18增加系统工具`export_file_to_download_center`：新UTF-8文本先上传平台DL文件再登记下载中心；当前会话附件或大工具Artifact复用原File ID，只登记不重传。合法已有文件必须从当前Session原始History恢复授权，工具只在可信user/App/Space上下文中直接披露，不进入Agent能力配置。
- v0.4.19将LOG纳入文本附件：Console允许选择`.log`，上传链路把该后缀的通用二进制或日志MIME规范化为`text/plain`，`read_file`继续按行分页读取；其他二进制格式不因此放宽。
- v0.4.20为模型目录增加models.dev官方`catalog.json`离线导入和可往返的当前目录JSON导出；联网刷新与文件导入复用同一解析、Provider白名单、标准化和原子替换逻辑。导入失败保留旧目录，也不修改已保存的模型配置。
- v0.5.0完成一体化Console视觉与布局重构：顶栏和图标导航统一三个主页面，工作台支持会话与详情栏调宽，配置中心提供Agent卡片/列表和独立编辑页，Skill与UI工具采用左右分栏。REST、SSE、History和Alembic `006`保持不变；模型目录保留导出、导入和在线更新，UI工具直接展示当前包的完整工具列表；上传Skill标题栏只保留编辑和上传，其余操作进入三点菜单；删除、未保存提醒和Skill信息输入统一使用轻量Console弹层，禁止回退浏览器原生`alert/confirm/prompt`。
- v0.5.1抽出Console共享Markdown渲染器：Skill正文按Markdown阅读样式预览，主对话支持安全渲染`mermaid`围栏图表；图表仅在完整回复落定后按需加载本地资源并渲染，语法错误保留源码。
- v0.6.0 Console普通对话读取`/history`的`ConversationItem[]`，轨迹才读取Event API；客户端不解析
  Event Payload。实时会话只消费Text/Thinking/Tool等流式事件和`final`，正常收流保留当前直播结果；
  切换、刷新、重新打开或异常恢复时读取`/history`持久化投影。Compaction按同一Event ID原位更新，
  轨迹保留真实触发位置和脱敏详情。
- v0.5.4收敛Console轨迹详情展示与前端实现：Assistant的Thinking归入Preview首段并默认折叠，短正文按内容自适应、长正文超过上限后滚动；Tool、Planning和Deferred复用同一套Payload/Result摘要渲染。REST、SSE、History、数据库和轨迹投影语义均不变化。
- v0.5.5增加独立`llm_providers`配置层、九类Pydantic AI适配与Provider分栏Console，运行时开始按显式Provider类型构造原生Provider/Model，不再按模型前缀或URL猜协议。
- v0.5.7压缩Provider详情接入信息：Base URL改为单行图标事实，地址模式、Key和启停状态改为紧凑胶囊，运行协议并入Provider副标题；仅调整Console展示，不改变Provider、Model、迁移或外部接口。
- v0.5.8完善纯本地SQLite调试：`local_dev`可通过仓库外Profile显式开启仅限回环进程内部ASGI scope的管理鉴权旁路，无需平台Token、App或Space即可维护Provider、Model、Agent并执行基础对话；Profile值覆盖同名环境变量，部署入口、外部Header和平台文件/MCP上下文均不放行。数据库Head仍为`007`。
- v0.5.6完成Provider + Model两层归一化：Provider独占Base URL和密钥，`llm_models.provider_id`建立`RESTRICT`外键，模型名只在Provider内唯一；Provider和Model启停共同形成有效门禁，API以`selectable/unavailable_reason`披露状态。Agent、Chat、Session覆盖、标题与上下文估算统一通过`model_id`解析Provider，外部REST、SSE与History核心协议不变。
- v0.3.20 起上传 Skill 在每个 Pod 只保留 `runtime-skills/<code>/` 一份当前运行文件。服务启动、安装、替换、启用和手工重载负责同步；普通 Run 只读本地目录。单个 Skill 文件不可用时跳过该 Skill并继续回答，状态通过 `/skills`、Console 和 `final.context.skills` 诊断。
- v0.3.23 起替换相同 Hash的上传 Skill也必须先检查平台原 ZIP；原包可下载才返回 `noop`，原 File ID失效时重新上传、更新数据库 File ID并返回 `repaired`。只恢复当前 Pod目录而不修复平台事实源属于不完整修复。
- 上传 Skill 的平台 ZIP 是系统级配置文件，不属于业务 App/Space。平台文件使用可无 Space 回读的 `INF` 类型；安装、替换、启动恢复、管理员下载和 Run 兜底均保留调用身份但主动去掉 `app/space/language`业务上下文，保证同一 File ID 始终按系统级语义访问。不得改用 Space 级 `UPL`，数据库也不得保存请求上下文、Token、Cookie 或完整 Header。
- v0.3.21 起 `/api/v1/system-tools` 只读说明 Skill、文件、MCP Tool Search 和 MCP Resource 的运行时工具来源与注入条件；这些 `runtime:*` 展示编码不得进入 Capability Registry、Agent 配置或数据库。Python Sandbox 完整说明由当前 Profile 的包清单动态生成，Function Tool Definition 保持一行。
- v0.3.23 起附件与 Skill 文件统一由自动注入的系统工具 `read_file`读取：只处理文本、DOCX和XLSX，并在同一Run复用字节与解析结果。图片不属于文件工具：当前轮图片始终作为原生多模态内容交给Provider，由Provider决定所选模型是否接受；历史图片和Skill图片不读取。PDF和PPT/PPTX若有产品需求，单独设计文档解析工具，不塞进 `read_file`或伪装成模型原生模态。`execute_skill_script`在Agent同时选择含脚本Skill和 `builtin:sandbox`时注入，与 `execute_python`共用当前子进程沙箱。
- v0.3.14 起每个 Pod 固定一个异步 Gunicorn Worker，保证进程内 `ActiveRunRegistry` 与取消请求一致命中；横向扩容多个 Pod 前必须提供会话粘性或共享取消协调器。工具检索以用户问题为主，Context Reference 标签仅在问题无检索结果时回退，并在单次 Run 内排除已披露工具和重复搜索；不得清空 Pydantic AI 的跨 Run 发现历史。
- Harness 是可选能力库，不是产品平台。会话、身份、配置、HITL、SSE 和 Console 继续由 DeepAgent 负责；引入 Harness 0.x 时必须与 `pydantic-ai-slim` 一起固定准确版本。
- `pydantic-ai-slim 2.35.1`要求OpenAI 3.x；当前联合固定`openai==3.5.0`、`aiohttp>=3.14.3,<4`和`deepfos>=1.1.97,<2`。OpenAI 3.x在模块导入时就会加载aiohttp传输，不能再与aiohttp 3.9混装。Google Provider的自定义代理客户端使用`httpx2`，禁止退回已弃用的旧`httpx.AsyncClient`路径。
- Provider Usage 是 best effort；OpenAI-compatible 或私有端点返回 0 时可能代表缺失。上下文预算必须结合模型窗口元数据和保守估算，不能只依赖 `result.usage`。
- 新上传只保存附件引用；运行时 `read_file`通过 1-based `offset/limit`、
  `has_more/next_offset`渐进读取 TXT、LOG、DOCX、XLSX。合法 `file_id`
  必须来自当前会话的原始Event或Artifact，不能从摘要Evidence推断授权。详见
  [`docs/design/06-attachment-parsing/`](docs/design/06-attachment-parsing/)。

### 框架能力说明方式

向产品或客户端解释 Pydantic AI、Harness、Provider 能力时，不能只给类名和抽象结论。至少按以下顺序说明：

1. **发生阶段**：能力发生在模型请求前、工具定义披露时、工具执行后，还是Event持久化时。
2. **前后数据**：给出最小的 JSON、SSE 或消息示例，明确哪些字段新增、删除或保持不变。
3. **模型实际看到什么**：区分 Tool Definition、输入参数 Schema、返回 Schema 和真实 Tool Result，说明是否增加输入 Token。
4. **职责归属**：分别说明 Provider、Pydantic AI Core、Harness 和 DeepAgent 产品层负责什么，避免把框架原语说成完整产品功能。
5. **收益、成本和失败边界**：不仅说明“能做”，还要说明为什么值得做、可能增加多少请求/Schema/存储，以及失败时会发生什么。
6. **当前决策**：最后明确归入“待实现、有潜力、暂缓、已关闭、已实现”中的哪一类。

例如解释 `IncludeToolReturnSchemas` 时，应明确它在**模型选择工具之前**修改 Tool Definition：

```text
开启前：cube_query_data(args_schema)
开启后：cube_query_data(args_schema, return_schema)
调用后：仍只返回本次真实 JSON result，不会再把 return_schema 附在结果后面
```

因此它解决“模型调用前不知道返回字段”的问题；真实结果本身过大、空字段过多则属于分页、投影或 Artifact 治理，不能混为同一需求。

---

## 其他文档

| 文档 | 类型与用途 |
| --- | --- |
| [`README.md`](README.md) | 人类快速开始 |
| [`docs/README.md`](docs/README.md) | 文档总入口 |
| [`docs/guides/project-structure.md`](docs/guides/project-structure.md) | **说明**：当前目录、运行链路与职责边界 |
| [`docs/guides/README.md`](docs/guides/README.md) | **说明**：协议、使用指南、实现经验和排障记录 |
| [`docs/design/README.md`](docs/design/README.md) | **设计**：当前方案、研究依据、历史决策和原型 |
| [`docs/design/TODO.md`](docs/design/TODO.md) | **待办**：跨课题唯一待办清单和来源索引 |
