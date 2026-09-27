# AGENTS.md — 项目索引

> 面向 AI Agent 的渐进式披露文档。**本文件仅为索引**；具体章节见各子目录下的 `AGENTS.md`。
>
> **边界说明**：`src/deepfos_platform/` 是随仓库发布的平台运行时支撑包，不是
> Agent 能力目录；只在平台配置、客户端、服务发现或部署运行时任务中修改。

---

## 快速上手（30 秒）

| 项 | 内容 |
| --- | --- |
| **是什么** | 通用 AI Agent 全栈：FastAPI 后端 + Pydantic AI 运行时 + 静态前端 |
| **产品版本** | `deep_agent.__version__`为唯一来源；当前`0.11.8`，数据库Head为`008`（飞书来源列） |
| **启动** | 本地 Test 联调固定使用下方命令；部署入口才使用 `deep_agent.main:app` |
| **测试** | `PYTHONPATH=src python3.12 -m unittest discover -s tests -p "test_*.py" -v` |
| **配置** | 仓库根 `.env`（`DEEP_AGENT_*` / `DEEP_AGENT_DBMS_*` / `DEEP_AGENT_SQLITE_PATH`） |

平台接入基础见[服务地址与账号链路](docs/guides/platform-integration.md)：
`http://system-server` 是平台逻辑服务配置，须先归一化再走服务发现；禁止仅凭 URL 协议直接绕过 Eureka。
新增平台身份适配必须复用 `deepfos_platform.discovery.target.normalize_service_target`，
保留显式 IP/端口、真实域名及网关路径的直连语义。

诊断中心依次为「会话 / 用量 / 日志」：见[会话指南](docs/guides/diagnostic-sessions.md)、[用量指南](docs/guides/diagnostic-usage.md)和[日志指南](docs/guides/diagnostic-logs.md)。
会话概览、列表和跨用户只读详情统一使用管理员 `/diagnostics/sessions` 接口；统计只读取现有会话与真实事件，
平台会话概览/列表的用户维度为平台账号（包含渠道会话的归属账号）；新增渠道详情独立按渠道账户、用户/群聊和发言人统计，读取已有渠道台账与会话事件，不新增统计表或事件。
`diagnostics/` 是独立、尽力写入的管道分隔文本日志模块（`.log`），不写会话事件、不改变业务执行。
日志查询限当前实例 API/网关；所有诊断接口均校验管理员，普通 Session 权限保持不变。
日志消息在后端写入时生成，格式为 `时间 | 进程 | 等级 | request-id | 来源 - 消息 | key=value`，管道、换行和反斜杠统一转义；查询返回真实 `raw`。旧行只保留原文，不增加 JSON 兼容解析。新增日志仍只提交安全白名单字段，禁止业务正文、完整参数、结果、凭据和异常原文。消息从已校验的工具名、模型名、Agent名称、状态和耗时生成；查询的message/summary只返回实际写入正文，附加字段独立返回。

集成入口见[集成配置](docs/guides/integrations.md)：固定目录由后端定义，`004`新增`integration_settings`但不插入初始记录。仅保存非空配置才落库；清空删除配置记录。普通组件按集成编码读取JSON，未配置返回409；管理操作仍需企业管理员。

Agent 安装包见[安装包说明](docs/guides/agent-packages.md)。自带包唯一位置为 `src/deep_agent/agent_packages/builtin/<code>.zip`，每个完整 ZIP 集中保存 Agent、上传 Skill 和自定义 UI 工具，与导出/上传格式相同；当前电子表格包取自 Alpha 正式导出的 V91 资产，DeepCube 保留 2026-09-14 Test 官方导出包，来源及哈希见自带包 README。服务从安装功能模块相对路径读取，不保留散文件副本；测试包使用隔离目录。安装可新建或显式覆盖指定 Agent；共享 Skill/UI 同编码同内容复用、内容不同更新并提示影响，UI 多余工具保留供其他 Agent 使用。旧 Agent.ui_tool_selection 列仅为历史数据库兼容保留，运行与安装只按 capabilities 启用整包；旧 ZIP 名单忽略，新保存清空旧列，新导出不携带名单。MCP 同编码自动关联，缺失只提示；模型同名优先、其次首个可用模型、无则留空。005 支持空模型；运行需请求模型或 Agent 模型，禁止环境默认回退。预检只读，安装用配置独占屏障二次校验摘要，文件补偿先核对数据库引用，避免提交确认失败后误删已落库资源。


企微应用与成员资料见[使用指南](docs/guides/wecom-applications.md)及[渠道接入](docs/guides/wecom-channel.md)。本次 v0.10.1 功能增量 `006` 新增企业应用配置和可重建成员缓存，保留已发布 `005`。渠道页面在标题同行切换账户与应用；应用采用左侧列表、右侧应用标题及合并企业信息的配置卡、缓存统计与按需加载的只读成员分页表。渠道 `settings.wecom.user_details_enabled` 默认为关闭，开启必须引用有效应用；查询开关独立于渠道启停，关闭时不访问资料接口。

身份服务位于 `services/wecom_client.py`、`services/wecom_profiles.py`，由 API 进程统一持有 HTTP／Token／任务；Gateway 使用内部鉴权解析接口，不能自行选择应用。密文转换及明文回退以官方结果确认，同应用合并到一份 User ID 资料；原始 `sender_id/peer_id` 不变。资料有效期一周，缓存失效保留姓名与映射，迟到结果受应用 revision 和渠道配置保护。开启后私聊标题为 `User｜姓名(原始 ID)`，来源上下文仅额外增加 `userid/name`；群标题为 `Group｜chat_id`，平台归属／权限不变，私聊首轮、群聊每轮规则不变。

`wecom_applications` 是配置备份的一部分（含敏感凭据），`wecom_user_profiles` 是缓存，仅导出空结构；Token、任务和负缓存不备份，Agent 整包也不包含这些数据。表分类与逻辑顺序由 `db/configuration_transfer.py` 显式维护。`006` 降级遇到应用、成员或渠道企微设置时拒绝，不自动删除。2026-09-14 已核验本地超管机器人“先胜小D”：明文入口与普通机器人共用一条成员映射，真实标题及首轮来源上下文正确；另外只读验证了无缓存时的官方明文回退。无需超管配置，按映射或接口明确结果解析，不按前缀猜测。


### 本地 Test 联调固定启动命令

日常“启动/重启本地 8000”默认使用下节稳定 SQLite＋渠道网关。以下命令仅用于用户明确要求连接共享 Test 的联调：

```bash
# 首次或缺 .venv-sandbox 时：local_dev 默认会自动 setup；也可手动：
# bash scripts/sandbox_venv/setup.sh

PYTHONPATH=src python3.12 -m deep_agent.local_dev \
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
- 纯本地头像菜单通过同源`GET /api/v1/current-user`使用模拟资料；可用
  `DEEP_AGENT_LOCAL_USERNAME`指定显示用户名，未指定时显示“本地用户”。生产与带平台Token的
  本地联调仍由该接口适配账号中心`GET /api/user/full-detail`，浏览器不直连账号服务。
- 纯本地附件闭环必须再显式设置`DEEP_AGENT_FILE_BACKEND=local`和
  `DEEP_AGENT_LOCAL_FILE_ROOT=<独立目录>`。该后端只允许`local_dev + SQLite`，部署/Test默认仍为
  `platform`。文件正文保存为私有Blob，独立`metadata.sqlite3`只存索引；业务API与线上保持一致。

### 稳定 SQLite 本地开发基线

v0.10.16 本地升级验证使用仓库 `.venv/bin/python`，依赖按 `requirements.txt` 安装；
不要用仍装旧依赖的全局解释器启动。该环境可复用已安装的内部 DeepCube wheel。

当前 SQLite 启动命令：

```bash
PYTHONPATH=src .venv/bin/python -m deep_agent.combined_runtime \
  --local-config ~/.config/deep-agent/sqlite.env --api-port 8000
```

后续日常本地开发、Console 联调和人工验收，不再随任务创建全新的空 SQLite。默认固定复用：

```text
Profile:         ~/.config/deep-agent/sqlite.env
主数据库:        data/deep-agent-local.db
本地文件根目录:   data/deep-agent-local-files/
启动端口:        8000
```

用户要求日常本地启动／重启必须同时带渠道网关（2026-09-27）。默认使用以下统一监督入口，同时保留 `local_dev` 身份与文件能力；不要只起 API：

```bash
PYTHONPATH=src .venv/bin/python -m deep_agent.combined_runtime \
  --local-config ~/.config/deep-agent/sqlite.env \
  --api-port 8000
```

该命令先启动`local_dev`，健康后再启动回环地址8010网关；不要再并行启动独立Gateway。

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
| 改后端、API、能力层、数据库、配置 | [`src/deep_agent/AGENTS.md`](src/deep_agent/AGENTS.md) |
| 新增或调试内置 MCP server | [`mcp_servers/AGENTS.md`](mcp_servers/AGENTS.md) |
| 改聊天 UI、admin 或 SSE 客户端 | [`console/AGENTS.md`](console/AGENTS.md) |
| 写或跑测试 | [`tests/AGENTS.md`](tests/AGENTS.md) |

---

## 仓库结构（鸟瞰）

```
deep-agent/
├── src/
│   ├── deep_agent/        ← 产品后端；渠道宿主在 channels/gateway/
│   ├── deepfos_platform/  ← 平台运行时支撑包
│   ├── maintenance/       ← 管理员维护命令，不注册为 Agent 工具
│   └── deepagent_files.py ← 沙箱公共文件接口
├── console/               ← 无构建控制台及 vendor/
├── mcp_servers/           ← 独立 MCP Server 启动入口
├── migrations/            ← Alembic 迁移；配置仍在根 alembic.ini
├── deploy/                ← Gunicorn、Docker Compose、Jenkins
├── scripts/               ← 开发与环境准备
├── tests/                 ← Python、接口、MCP、控制台测试
├── docs/                  ← 说明、设计与原型
└── data/                  ← 持久运行数据，原地保留且不提交
```

v0.8.3 完成目录收敛，本地与线上都从同一份 `src/` 加载 Python 源码，不改业务、协议和数据库。
根目录旧包链接、`frontend/` 和网关兼容包已移除；渠道入口统一为 `deep_agent.channels.gateway`。
安装依赖仍用 `requirements.txt`，不引入 `pyproject.toml`、wheel 或控制台构建。
所有启动、维护和 Python 测试命令从仓库根执行并设置 `PYTHONPATH=src`；Gunicorn 配置统一为
`deploy/gunicorn.conf.py`；Compose 在仓库根使用 `--project-directory "$PWD" -f deploy/docker-compose.yml`，
保留项目名和数据卷范围，不依赖旧路径或符号链接。
尚未实施的原型归入 `docs/design/`，原始 Jenkins 脚本只在 `docs/archive/jenkins/` 归档；
`data/`、`.venv` 和 `.venv-sandbox` 保留；临时验证按需创建 `tmp/`，任务结束清理，不长期保留空目录。
详见 [`项目结构指南`](docs/guides/project-structure.md)。

目录按职责命名，不为外观一致移动公开 CLI、协议名或持久化字段。共享模型支持在
`src/deep_agent/models/`，程序编排在 `ptc/`，通用工具在 `capabilities/general/`；
Gateway 不保留指向企微 Adapter 的旧转发模块。Alpha 制品流水线仍按根目录 `ls` 分词后搬移打包；根目录受 Git 管理的名称不得包含空白或 Shell 通配符。
设计原型统一放到 `docs/design/`，报告放到 `docs/reports/`，带空格的主题目录只能出现在这些子目录中。
新增根目录条目前运行 `tests.unit.test_release_packaging`，不通过修改流水线绕过该约束。
原型不得放在生产 `console/` 静态目录，
跨 unit/integration 的公共模型替身使用 `tests/helpers/models.py`。调整路径后同时检查
真实空目录、导入/子进程路径、文档和 `test_project_layout.py`，不要只看 Git 的文件状态。

2026-09-08 用户同步运维现状：Alpha 与 Test 均使用 `python -m deep_agent.combined_runtime`
和 `PYTHONPATH=/deep-agent/src`，同时运行 API 与企微网关。v0.8.5 保持该入口和线上流水线不变；
不要再默认 Alpha 为仅 API 模式。该记录来自用户提供的部署信息，不替代线上健康验收。

---

## 架构一图

```
HTTP → src/deep_agent/api/v1 → AgentRunner → pydantic_ai.Agent → Toolsets
                                      └→ SessionStore (postgres/mysql/sqlite)
```

工具包来源：`builtin:general` | `builtin:sandbox` | `builtin:ptc` | `builtin:web` | `mcp:*`
（DB + `mcp_servers/` 兜底）| `ui:builtin` | `ui:<package>`。Skill 与 ToolPackage 分离，Agent 通过
`agents.skills` 显式选择，上传包元数据位于 `skill_packages`。

v0.6.1删除目录资源自身的冗余启停层；v0.6.4只恢复Model的`enabled`。Provider是否可用仍由
类型、地址与凭据等真实配置决定，不设置启停字段；模型必须同时启用且Provider有效，才能由Agent
或请求级`model_id`选择。Skill和工具是否进入Run仅由Agent的
`skills/capabilities`显式关联决定。新Agent两项均为空，不再通过`null`或默认包隐式继承能力。

### Pydantic AI 运行边界

- DeepAgent 自研 `ToolPackage` 是产品配置单位，负责把分类、展示元数据和恰好一个 Toolset 组织起来；Pydantic AI 的 `AbstractCapability` 只表示压缩、重试等运行扩展，二者不能混用。
- 模型直接接收 Instructions、历史、当前输入和 Tool Definitions。MCP Resource/Prompt 不会自动成为模型工具，必须由宿主读取、注入或显式包装。
- Pydantic AI 负责 Agent loop、参数校验、工具调用、重试、流式事件和 Deferred 原语；DeepAgent 负责 SQL 会话、用户隔离、MCP 配置/Header、HITL、SSE 和 Console。
- v0.6.0将会话域统一为`chat_sessions + chat_session_events`；v0.8.5运行期间按真实消息部分及时保存，
  以稳定的消息序号和部分标识去重、排序，读取模型历史时重新归组为完整Pydantic Message。
  Event Store是唯一事实源。模型历史、审计轨迹和面向客户端的`ConversationItem`均由服务端确定性
  投影，不双写History表。Branch不兼容也不预埋。
- 不生成`run/progress`或`run/terminal`。正常完成、主动停止不追加辅助回执；真正终止执行的异常
  幂等保存一条脱敏`execution/error`，不进入模型正文。半段只经SSE展示，取消/异常收尾尽量保存一次。
  保留控制台轮询、渠道投递和压缩生命周期；旧RUN行不清理、不迁移、不加解析或恢复兼容。
- 控制台切入活动会话仅订阅指定`run_id`的只读SSE，空闲不连接、结束不跟随下一轮；有界内存回放
  与数据库History校准分离，失败退回该轮轮询。观察断开不取消执行，不增加持久化快照或成功回执。
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
- v0.10.8 搜索先在当前授权目录中匹配，再排除已可用工具；本地结果区分已加载、已可用和精确名称缺失。协议名保留前缀及分隔符，不把不存在的协议名回退为其他保存工具。搜索不刷新 MCP 检测快照；远端新增工具后需在 MCP 配置中重新检测，后续 Run 才使用新目录。
- v0.4.1 将检索器升级为 `lexical_v2`：复用独立的字段化词法内核，按协议名、MCP标准 `title`、参数和描述分级匹配，并支持拼音与受限错字回退；查询明确写出协议工具名时只返回精确工具。不得加入业务词典或把词法匹配描述成中英文语义翻译。
- v0.4.2 引导本地 `search_tools` 在一次调用中提交最多 3 个互补查询：用户原始语言、简洁英文能力描述和可选协议名。后端只做确定性词法检索，不调用翻译模型；首轮 Top 3仍只依赖原始问题和 MCP元数据。
- v0.4.4～v0.4.5完成下线内置MCP清理；Alembic 005和006依次清理对应预制Server记录与Agent能力引用，历史会话不改写。当前代码内置MCP只保留DeepCube；真实MCP进程回归改用`tests/fixtures/`下的测试专用Server，禁止测试Fixture进入生产扫描目录。
- v0.6.0的Harness Planning只暴露`write_plan`完整替换工具；快照继续走普通Tool Call/Result Event，
  不建Plan表。同一Turn的Deferred新Run从最近成功快照恢复InMemoryPlanStore，新用户Turn从空计划开始。
- v0.6.1将运行基线固定升级到`pydantic-ai-slim 2.35.1`与`pydantic-ai-harness 0.26.0`。OpenAI 3.5、aiohttp 3.14、deepfos SDK 1.1.97和Google Provider的`httpx2`客户端必须联合升级。
- v0.6.7将该能力的产品编码规范化为`builtin:ptc`，Console名称为“程序编排”，模型协议仍为Harness
  `run_code`；旧`builtin:code-mode`只作运行、读取和导入兼容。PTC只编排已授权且已披露MCP，
  不可调用Sandbox、Web、History、Planning、UI/HITL、Deferred/Approval、导出、Resource或自身等
  内部工具。Code-only`load_file_data`可把当前会话授权File ID或公开HTTP(S)文件转为Run内不透明
  载荷，真实Base64只在MCP调用边界生成。
- v0.6.3只让证据白名单中的稳定MCP返回Schema进入Monty类型信息，未知Schema退化为通用JSON；嵌套审计有总大小上限。DeepSeek v4 Vision当前轮图片按像素尺寸估算上下文Token，其他路由保守回退。Console用`CODE`标签和Code页签展示`run_code`，附件与Context Reference在输入详情中分区。
- v0.6.5新增默认不关联的`builtin:web`，固定包含`web_search`和`web_fetch`。Search全局配置明确选择当前模型或一个固定Provider，失败不隐式降级；Fetch复用Pydantic AI SSRF-safe实现并禁止私网、云元数据和URL凭据。v0.6.7起Web工具不再进入PTC；PTC的URL原样中转只经`load_file_data`完成。`global_settings`已进入当前Alembic`001`生产基线和配置备份。
- v0.6.6为图片输入模型增加外层普通工具`web_image_view`，不进入Monty。`web_fetch`在原位置保留
  安全图片Markdown引用，但不自动读图；同一Run使用原生`ImageUrl`，Event/History/SSE/轨迹只保存
  URL、MIME和下载模式，Console按URL预览。不得把图片引用加入ToolOutputLimits排除名单，也不得保存Base64。
- v0.6.7扩展`execute_python`的受控文件输入与产物发布：`deepagent_files.input_path()`读取当前
  会话授权File ID，`publish_path()`登记零到多个执行产物，成功后上传FileStore并只向模型和Event
  暴露安全引用。沙箱默认允许公网，但Worker会尽力阻断非公网地址；健康接口仍如实标注
  `security_level=none`，不得把它描述为恶意代码强隔离。
- v0.6.8为工具目录、实时SSE和动态History投影增加仅供会话卡片展示的`display_name`；稳定调用仍
  使用原始`tool/tool_name`和`tool_call_id`，Event Payload、数据库与轨迹不保存或展示派生名称。
  内置工具使用代码元数据，UI工具使用`metadata.display_name`，MCP使用官方`Tool.title`并按工具名回退。
- v0.7.0新增可选渠道网关，当前公共`channels/gateway/`宿主和`channels/`调度不引用企微SDK，
  只有`channels/adapters/wecom/`调用官方`wecom-aibot-python-sdk==1.0.2`，不重写连接/认证/心跳/重连/解密/回复协议。
  API固定一个worker，Gateway按环境部署一个副本；`channel_accounts`保存配置，`channel_session_bindings`映射企微Peer到
  Session，`channel_message_runs`负责幂等和生命周期。企微Session固定归属Account的管理员配置的会话用户，
  外部Peer不冒充平台用户。Gateway不执行迁移，数据库必须先由 API 升级到当前代码 Head（本次增量为 `006`）。
  v0.9.7新增`003`移除数据库外键；先停止旧API/Gateway写入、迁移核验，再启动新版，不能只改版本号。
  五个命令`/help /new /stop /status /whoami`仅在渠道入口处理；没有`/reset`或共享Chat全局拦截。
  控制命令绕过普通队列，15秒等待真实停止；`/new`原子换绑，旧历史保留，投递失败不重复命令副作用。
  Session标题事件`source=channel`保存来源，Console帮助由公共目录生成；旧来源修复入口为
  `PYTHONPATH=src python3.12 -m maintenance.channel_origins --sqlite <本地文件>`，默认dry-run，停服务后`--apply`自动备份。
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
- v0.10.16 固定 Core 2.46.0 / Harness 0.32.0 / Monty 0.0.23。SDK 的 Toolset.id 不能含冒号，动态部分用可逆百分号编码；业务 capability code 和 MCP 协议工具名不改。WebFetch 覆写须同步上游安全转换、字符集和异常边界，不能只升级 requirements。
- Harness 是可选能力库，不是产品平台。会话、身份、配置、HITL、SSE 和 Console 继续由 DeepAgent 负责；引入 Harness 0.x 时必须与 `pydantic-ai-slim` 一起固定准确版本。
- `pydantic-ai-slim 2.46.0`要求OpenAI >=3.8；当前联合固定`openai==3.8.0`、`aiohttp>=3.14.3,<4`和`deepfos>=1.1.97,<2`。OpenAI 3.x在模块导入时就会加载aiohttp传输，不能再与aiohttp 3.9混装。Google Provider的自定义代理客户端使用`httpx2`，禁止退回已弃用的旧`httpx.AsyncClient`路径。
- Provider Usage 是 best effort；OpenAI-compatible 或私有端点返回 0 时可能代表缺失。上下文预算必须结合模型窗口元数据和保守估算，不能只依赖 `result.usage`。
- 新上传只保存附件引用；运行时 `read_file`通过 1-based `offset/limit`、
  `has_more/next_offset`渐进读取 TXT、LOG、DOCX、XLSX。合法 `file_id`
  必须来自当前会话的原始Event或Artifact，不能从摘要Evidence推断授权。详见
  [`docs/design/06-attachment-parsing/`](docs/design/06-attachment-parsing)。

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

### 数据库逻辑引用规范（v0.9.7 / 003）

- 不新增数据库外键。ID、主键、唯一约束和索引保留，10类逻辑引用见 `db/reference_schema.py`。
- 在 Store/Service 同一事务锁父记录、重新校验后写子记录；同类父记录按 ID 排序。不可仅在 HTTP 层或进程内保证引用。
- 普通写事务先持数据库 Revision 行共享锁，配置替换持独占锁；SQLite 使用 BEGIN IMMEDIATE。绕过公共事务入口的维护写入必须显式协调或停服务。
- 会话删除等待本实例运行收尾，原子删除 Events 并将 Channel Binding.session_id 置空。v0.10.9 Agent 有历史会话时返回影响数量及确认 token，确认后级联删除全部用户会话；渠道有绑定/运行记录时同样确认后清理台账，保留 Agent 与历史会话。范围改变必须重新确认；仍有渠道引用的 Agent、仍有 Agent 引用的模型禁止删除，停用不解除引用。取消协调限单 API 实例，不能宣称跨 Pod 停止运行。
- `PRAGMA foreign_key_check` 为空不代表逻辑引用有效，必须执行 `assert_logical_references`。
- 升级/降级需要实际数据库结构检查，不支持生成003离线SQL；001/002保留历史离线编译。
- 备份按源Revision输出，导入要求同版；测试必须使用隔离库，不能改共享Alpha/Test或稳定本地SQLite。

### 临时产物与已落地原型清理

功能落地并发布后，删除对应一次性 HTML 原型、图片评审稿、测试截图和临时报告，保留当前指南、设计决策与正式回归；历史材料通过 Git 恢复，不另存一份备份。尚未实施的课题材料（如数据流 AI 集成）继续保留。
测试夹具应放在 `tests/`，不能依赖随时可清理的原型目录；测试报告默认写到忽略的 `output/TMP_to_delete-*`。
清理前核对在用文件；不删除本地数据库、用户文件、环境配置、依赖环境及正式内置安装包。

## 飞书渠道开发约定（2026-09-27）

扫码注册直接使用飞书 PersonalAgent 默认模板，不传 addons，只通过 app_preset.name 预填渠道名称，保持二维码简洁。不要重新加入未确认必要的权限/事件增量或关闭默认模板。默认清单由飞书服务端维护，事件已添加不等于对应权限已获批，用户身份能力也不等于仅有 App ID/Secret 即可调用。注册改动仅作用于新生成二维码，不能宣称已有应用自动获得新权限。

飞书回复底部三点参考的是 `larksuite/openclaw-lark` 官方插件的独立 `loading_icon` 图片组件，不能把 OpenClaw 内置 `extensions/feishu` 的 `streaming_mode` 当作自带底部动画。正文更新保留独立组件，终态及关闭网关时尽力移除；SDK 测试只能确认协议，实际动效仍需飞书客户端验证。飞书展示调整不要修改企微适配器或共享消息投影。

飞书与企微共用 Gateway，适配器在 `channels/adapters/feishu/`；官方 SDK 有模块级 WS loop，飞书 SDK 按账号在受控子进程隔离，父网关负责数据库与业务调度，匿名管道通信，不新增公网端口。不得绕过公开接口改写 SDK 私有 loop。扫码凭据只在服务端短期保存，查询接口不返 Secret。群聊每条消息独立保存 sender_id/name，tenant/thread 单列，个性化字段放 source_metadata；均不授予平台权限。008 仅加可空列，无新表。无真实租户时受控 SDK 测试与真实授权验收必须分开报告。详见 `docs/guides/feishu-channel.md`。
