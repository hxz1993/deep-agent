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
| **产品版本** | `deep_agent.__version__` 为唯一来源；当前 `0.4.17`，默认发布递增第三位 |
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

工具包来源：`builtin:general` | `builtin:sandbox` | `mcp:*`
（DB + `mcps/` 兜底）| `ui:builtin` | `ui:<package>`。Skill 与 ToolPackage 分离，Agent 通过
`agents.skills` 显式选择，上传包元数据位于 `skill_packages`。

### Pydantic AI 运行边界

- DeepAgent 自研 `ToolPackage` 是产品配置单位，负责把分类、展示元数据和恰好一个 Toolset 组织起来；Pydantic AI 的 `AbstractCapability` 只表示压缩、重试等运行扩展，二者不能混用。
- 模型直接接收 Instructions、历史、当前输入和 Tool Definitions。MCP Resource/Prompt 不会自动成为模型工具，必须由宿主读取、注入或显式包装。
- Pydantic AI 负责 Agent loop、参数校验、工具调用、重试、流式事件和 Deferred 原语；DeepAgent 负责 SQL 会话、用户隔离、MCP 配置/Header、HITL、SSE 和 Console。
- 原始 `chat_messages` 始终完整保存；启用全局上下文压缩后，模型输入可复用
  `chat_context_checkpoints` 派生投影。v0.3.4 增加独立主动压缩接口；主动入口不受自动阈值、
  历史长度或现有 Checkpoint 限制，无实际收益时由后端返回 `noop`，
  Console 只展示当前有效压缩边界，不保存压缩审计列表。压缩不得破坏 Tool Call/Result 配对、原始历史、
  分支和 Pending 恢复；模型未配置上下文窗口时自动停用。
- v0.3.5 增加结构化 `context_refs`：原始引用随 `chat_messages` 保存，`metadata` 只用于历史和
  前端，模型输入与摘要只使用安全派生投影。快捷命令仍是业务 Prompt 文本，不新增命令框架。
- v0.3.6 将语义压缩改为一次安全摘要；不再滚动分块或二次激进摘要。瞬时 Provider 错误只以
  完全相同的 Prompt 重试一次，无法安全放入单次请求时自动链路仅临时 Sliding Window，主动
  链路直接失败，二者均不写入 Checkpoint。
- 新的运行时横切能力优先评估 Core 的 `PrepareTools`、Hooks、`ToolSearch` 和 Harness 的 Compaction/Overflow；不要默认把逻辑继续写入 `AgentRunner`。
- v0.3.12 对已授权 MCP 使用 Pydantic AI ToolSearch 渐进披露；v0.4.9 起全局候选不超过 5 个时全部直接披露，超过 5 个时词法预选最多 3 个完整 Schema，其余只列稳定协议名并可搜索加载。检索不扩大 ToolPackage 权限。
- v0.4.1 将检索器升级为 `lexical_v2`：复用独立的字段化词法内核，按协议名、MCP标准 `title`、参数和描述分级匹配，并支持拼音与受限错字回退；查询明确写出协议工具名时只返回精确工具。不得加入业务词典或把词法匹配描述成中英文语义翻译。
- v0.4.2 引导本地 `search_tools` 在一次调用中提交最多 3 个互补查询：用户原始语言、简洁英文能力描述和可选协议名。后端只做确定性词法检索，不调用翻译模型；首轮 Top 3仍只依赖原始问题和 MCP元数据。
- v0.4.4～v0.4.5完成下线内置MCP清理；Alembic 005和006依次清理对应预制Server记录与Agent能力引用，历史会话不改写。当前代码内置MCP只保留DeepCube；真实MCP进程回归改用`tests/fixtures/`下的测试专用Server，禁止测试Fixture进入生产扫描目录。
- v0.4.6全局采用Harness `Planning()`；每个Run使用独立内存Store，不跨消息或服务重启恢复。v0.4.16将工具收敛为`write_plan`、`read_plan`和`update_task_statuses`，用Harness公开Guidance约束稳定ID、Deferred重建和最终正文前收尾；仅这三个工具按Planning事件识别。规划结果继续走标准Tool Call/Return，DeepAgent只附加只读快照，不建立第二套任务引擎或计划表。Console在正常Final或UI Pending时清除浮层，取消/失败时保留。
- v0.4.8将运行基线固定升级到`pydantic-ai-slim 2.31.0`与`pydantic-ai-harness 0.21.0`。Deferred工具和Capability工具的执行门槛由Core统一判断；DeepAgent继续只负责授权、检索、披露策略和观测。Planning缓存边界、MCP协议兼容、Provider流关闭及安全修复随框架升级生效，不新增产品协议。
- Tool Search 首轮预选只使用聊天 API 的原始非空 `message`；`context_refs.type/label` 仅在纯上下文请求时回退，不能让 Page/Range 标签覆盖用户问题。实时工具若被 `ModelRetry` 或取消撤回，SSE 用 `tool_call_removed` 通知客户端删除临时卡片；取消后的非空部分回答必须以 `assistant.status=interrupted` 恢复。
- 模型目录的 `max_output_tokens` 同时约束普通和流式回答；工具错误只有在模型修改参数或改选工具后可能成功时使用 `ModelRetry`，已确定的下载、解析或上游失败使用 `ToolFailed`，避免消耗工具重试预算。
- v0.3.13 将正式 Run 的官方 `CancellationToken` 传给内部摘要 Agent；取消摘要直接终止 Run，不能转成 Sliding Window 后继续回答。Local/Native 工具配对统一使用 Pydantic AI 2.26 的 Call/Return 基类。
- v0.3.16 让自动与主动压缩复用同一内部投影流程；摘要固定保留任务、决策、事实、状态、下一步和未决问题六类信息。Harness 继续只承担公开确定性整理，Checkpoint 与摘要事务仍由 DeepAgent 管理。
- v0.3.17 通过 Pydantic AI 官方 `conversation_id/run_id` 参数贯通 SSE、History 和运行日志；同一 Session 共用 Conversation ID，每次发送或 Deferred 续跑生成新 Run ID。Provider 未提供缓存计数时必须显示未知，不能把框架归一化的零伪装成缓存未命中。
- v0.3.18 使用 Harness `Skills` 渐进披露 `SKILL.md`；初始只公开名称和描述，正文由 `load_capability` 按需加载。References/Assets 通过只读分页工具访问；`scripts/` 不向模型披露源码，Agent同时选择含脚本Skill和 `builtin:sandbox` 时可通过统一沙箱执行。Skill 本身不授予工具权限。
- v0.3.19 起 URL MCP 只在显式检测时执行 initialize/tools-list 并保存无敏感信息的工具快照；聊天 Run 用快照完成工具披露，真实调用时才按当前请求 Header 建连。同一 Run 复用连接，缺少或配置失效的快照不进入模型工具列表，必须重新“检测并更新”。Builtin MCP 和 Resource 代理保持原链路。
- v0.4.11补齐URL MCP实际工具调用超时的错误分类：Pydantic AI将协议超时包装为`ModelRetry`时，DeepAgent识别其MCP超时、连接中断或5xx原因并转成脱敏`ToolFailed`，让模型改用其他工具或解释失败；参数、对象选择等可纠正错误仍保持`ModelRetry`，正式取消不受影响。
- v0.4.12增加Console企业管理员兜底鉴权：`systemAdmin=true`按平台综合权限语义映射为内部`enterprise_admin`；固定`admin`账号不特殊放行。管理Router统一依赖同一授权器，聊天、会话、附件和固定Agent读取等运行接口不受影响。
- v0.4.13兼容Canvas MCP通过`CallToolResult.isError`返回的Go `context deadline exceeded`及稳定`CANVAS_ERROR`文本契约；不可纠正的超时形成失败Tool Result并允许Run继续回答，结果未知的修改操作提示先读取Canvas快照。可纠正错误和正式取消保持原语义。
- v0.4.15在URL MCP工具最后一次可纠正重试仍失败时，将最后一条模型可见的业务校验原因收口为失败Tool Result并要求模型结束当前Run；失败调用、Result和最终解释正常进入History。该链路只依赖Pydantic AI原生`ModelRetry → ToolFailed`语义，不额外维护同一Run的工具封锁状态。
- v0.3.20 起上传 Skill 在每个 Pod 只保留 `runtime-skills/<code>/` 一份当前运行文件。服务启动、安装、替换、启用和手工重载负责同步；普通 Run 只读本地目录。单个 Skill 文件不可用时跳过该 Skill并继续回答，状态通过 `/skills`、Console 和 `final.context.skills` 诊断。
- v0.3.23 起替换相同 Hash的上传 Skill也必须先检查平台原 ZIP；原包可下载才返回 `noop`，原 File ID失效时重新上传、更新数据库 File ID并返回 `repaired`。只恢复当前 Pod目录而不修复平台事实源属于不完整修复。
- 上传 Skill 的平台 ZIP 是系统级配置文件，不属于业务 App/Space。平台文件使用可无 Space 回读的 `INF` 类型；安装、替换、启动恢复、管理员下载和 Run 兜底均保留调用身份但主动去掉 `app/space/language`业务上下文，保证同一 File ID 始终按系统级语义访问。不得改用 Space 级 `UPL`，数据库也不得保存请求上下文、Token、Cookie 或完整 Header。
- v0.3.21 起 `/api/v1/system-tools` 只读说明 Skill、文件、MCP Tool Search 和 MCP Resource 的运行时工具来源与注入条件；这些 `runtime:*` 展示编码不得进入 Capability Registry、Agent 配置或数据库。Python Sandbox 完整说明由当前 Profile 的包清单动态生成，Function Tool Definition 保持一行。
- v0.3.23 起附件与 Skill 文件统一由自动注入的系统工具 `read_file`读取：只处理文本、DOCX和XLSX，并在同一Run复用字节与解析结果。图片不属于文件工具：当前轮图片始终作为原生多模态内容交给Provider，由Provider决定所选模型是否接受；历史图片和Skill图片不读取。PDF和PPT/PPTX若有产品需求，单独设计文档解析工具，不塞进 `read_file`或伪装成模型原生模态。`execute_skill_script`在Agent同时选择含脚本Skill和 `builtin:sandbox`时注入，与 `execute_python`共用当前子进程沙箱。
- v0.3.14 起每个 Pod 固定一个异步 Gunicorn Worker，保证进程内 `ActiveRunRegistry` 与取消请求一致命中；横向扩容多个 Pod 前必须提供会话粘性或共享取消协调器。工具检索以用户问题为主，Context Reference 标签仅在问题无检索结果时回退，并在单次 Run 内排除已披露工具和重复搜索；不得清空 Pydantic AI 的跨 Run 发现历史。
- Harness 是可选能力库，不是产品平台。会话、身份、配置、HITL、SSE 和 Console 继续由 DeepAgent 负责；引入 Harness 0.x 时必须与 `pydantic-ai-slim` 一起固定准确版本。
- `pydantic-ai-slim 2.31` 的 OpenAI extra 只有 SDK 下限。当前必须同时固定 `openai==2.46.0`，以兼容 DeepFOS平台运行时的 `aiohttp 3.9.x`；升级 OpenAI 3.x 前必须先联合验证平台 aiohttp，不能让干净构建自动漂移。
- Provider Usage 是 best effort；OpenAI-compatible 或私有端点返回 0 时可能代表缺失。上下文预算必须结合模型窗口元数据和保守估算，不能只依赖 `result.usage`。
- 新上传只保存附件引用；运行时 `read_file`通过 1-based `offset/limit`、
  `has_more/next_offset`渐进读取 TXT、DOCX、XLSX。合法 `file_id`
  必须来自当前原始会话，不能从 Checkpoint 推断。详见
  [`docs/design/06-attachment-parsing/`](docs/design/06-attachment-parsing/)。

### 框架能力说明方式

向产品或客户端解释 Pydantic AI、Harness、Provider 能力时，不能只给类名和抽象结论。至少按以下顺序说明：

1. **发生阶段**：能力发生在模型请求前、工具定义披露时、工具执行后，还是 History/Checkpoint 持久化时。
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
