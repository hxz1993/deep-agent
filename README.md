# DeepAgent

DeepAgent 是基于 FastAPI、Pydantic AI 和 SQLAlchemy 的多用户 Agent 服务，并
随服务发布一体化运维控制台。

当前产品版本为 `0.11.5`。`deep_agent.__version__` 是唯一代码版本来源，FastAPI、
`GET /info` 和 Console 顶部版本徽标均读取该值。后续正式发布默认递增
`MAJOR.MINOR.PATCH` 的第三位，只有明确规划重大版本时才调整前两位。产品版本不通过
环境变量或数据库覆盖，也不等同于 Alembic 数据库结构 revision。

各版本功能变更与迁移说明见 [`CHANGELOG.md`](CHANGELOG.md)。

Console 对话测试提供独立的“对话 / 轨迹”页签。会话历史、轨迹、Plan 和持久化 SSE 统一读取
`chat_session_events`；轨迹按 Sequence 原位展示 Run、模型 Part、工具调用/结果和 Compaction，
不再从整条 ModelMessage 做第二套拆解，也不额外建立轨迹表。能力边界见
[`docs/design/18-session-trajectory/`](docs/design/18-session-trajectory)。

v0.5.6 完成 Provider + Model 两层配置：Provider 独占 Pydantic AI 适配类型、地址和密钥，
Model 只保存 Provider 外键、模型编码与能力属性。Provider 不设置启停层，其可用性由真实连接
配置决定；v0.6.4 恢复模型级 `enabled`，用于管理员控制哪些已配置模型可供 Agent 和聊天选择。
模型目录仍是只读候选数据，不参与运行时关联。设计与迁移边界见
[`docs/design/19-model-provider-architecture/`](docs/design/19-model-provider-architecture)。

v0.6.2 增加显式 Agent 能力包 `builtin:code-mode`。关联后，已授权且已披露的
MCP 工具既保留普通 Tool Schema，也可在 Harness `run_code` 的 Monty 环境内组合调用。
当前会话已授权文件通过不透明 Run 变量中转，真实 Base64 只在 MCP 调用边界生成。
v0.6.3进一步收紧 SDK 函数作用域、可信返回类型和嵌套审计，并为 DeepSeek v4 Vision
加入按图片尺寸计价的上下文预算估算；未知路由仍保守回退。
使用和安全边界见 [`docs/guides/code-mode.md`](docs/guides/code-mode.md)。

v0.6.5增加默认不关联的`builtin:web`和公开互联网`web_search/web_fetch`；v0.6.6让Fetch保留
页面图片位置，并为图片输入模型增加`web_image_view`，通过原生`ImageUrl`按需读取公网图片。
Search全局明确选择当前模型或一个固定Provider，不执行隐式降级；Web配置与安全边界见
[`docs/design/21-web-tools/`](docs/design/21-web-tools)。

v0.7.0新增可选渠道网关：同仓、同镜像独立运行，公共Gateway/ChannelService通过Adapter接入平台；
当前只有企微Adapter，唯一引用官方`wecom-aibot-python-sdk==1.0.2`建立WebSocket长连接，
不重写认证、心跳、重连、媒体解密或回复协议。三张Channel表负责配置、外部会话绑定和持久消息去重，
正文及渠道来源标题仍由原Event Store负责。渠道支持`/help`、`/new`、`/stop`、`/status`、`/whoami`，
不拦截控制台及其他客户端的普通消息；控制台提供同一目录的只读“快捷命令”说明。
本版保持一个API worker和一个可选Gateway副本，飞书需要后续新增Adapter。配置、部署和状态说明见
[`docs/guides/wecom-channel.md`](docs/guides/wecom-channel.md)。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| [`CHANGELOG.md`](CHANGELOG.md) | 产品版本更新记录与迁移摘要 |
| [`src/deep_agent/`](src/deep_agent/AGENTS.md) | API、Agent 运行时、能力装配、会话和数据库 |
| [`console/`](console/AGENTS.md) | `/console/` 静态控制台 |
| [`mcp_servers/`](mcp_servers/AGENTS.md) | 仓库内置 MCP Server |
| [`src/deep_agent/builtin_skills/`](src/deep_agent/builtin_skills) | 代码内置只读 Skill；上传 Skill 由平台文件服务保存原包 |
| [`src/deepfos_platform/`](src/deepfos_platform/README.md) | 平台配置、客户端、服务发现和部署运行时支撑 |
| [`src/maintenance/`](src/maintenance/README.md) | 人工维护程序，不属于 Agent 工具 |
| [`deploy/`](deploy/README.md) | Jenkins、Gunicorn 和 Docker Compose 部署配置 |
| [`migrations/`](migrations/) | 数据库迁移；结构版本为 `007` |
| [`scripts/`](scripts/) | 开发、构建与环境准备脚本 |
| [`tests/`](tests/README.md) | Python、接口、MCP 和 Console 测试 |
| [`docs/`](docs/README.md) | 当前说明、技术设计和交互原型 |

v0.8.3 将本地与线上统一到上述源码目录，业务逻辑和接口不变。根目录旧包链接、`frontend/`
和网关兼容包已移除；从仓库根运行时通过 `PYTHONPATH=src` 加载 Python 包，渠道入口统一为
`deep_agent.channels.gateway`。部署和本地开发均携带完整 `src/`、`console/`、`mcp_servers/`、
`migrations/` 与 `deploy/`，不依赖符号链接，也不增加 `pyproject.toml`、Python wheel 或前端构建。

稳定 `data/`、`.venv` 和 `.venv-sandbox` 保留；临时产物只在验证期间存放于 `tmp/`，任务结束清理。
已落地原型已清理，尚未实施的设计保留在 `docs/design/`，Jenkins 原始脚本只在 `docs/archive/jenkins/` 归档。

完整边界见
[`docs/guides/project-structure.md`](docs/guides/project-structure.md)。

## 本地启动

所有命令从仓库根执行，默认使用本机 Python 3.12。安装依赖：

```bash
python3.12 -m pip install -r requirements.txt
```

Python 启动、维护与测试命令都需要 `PYTHONPATH=src`；下方 macOS/Linux 示例按命令设置。
Windows PowerShell 先执行 `$env:PYTHONPATH = 'src'`，再运行相同的 Python 命令。

若启用 Python Sandbox（默认 `DEEP_AGENT_SANDBOX_ENABLED=true`、backend=`subprocess-dev`），
还需**独立** sandbox 解释器（与宿主 `venv` 分离，预装 numpy/pandas）。  
`local_dev` **默认**会在缺解释器时自动执行 setup（可用 `--no-sandbox-env` 关闭）：

```bash
# 手动创建（可选；local_dev 默认也会补齐）
bash scripts/sandbox_venv/setup.sh
```

说明见 [`scripts/sandbox_venv/README.md`](scripts/sandbox_venv/README.md)。

连接 Test 数据库进行本地联调时，使用仓库外的私有 Profile：

```bash
PYTHONPATH=src python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

Profile 至少包含数据库配置和本地可信身份，权限应设为 `0600`。不要把 Token、
Cookie、数据库密码或 API Key 提交到仓库。Sandbox 相关项可按需写入同一 Profile，例如
`DEEP_AGENT_SANDBOX_PYTHON`（默认解析为仓库根 `.venv-sandbox/bin/python`）。
test 固定镜像部署可设 `DEEP_AGENT_SANDBOX_ENSURE_VENV=true`，由 gunicorn 主进程在 fork 前创建临时 venv（如 `/tmp/.venv-sandbox/bin/python`），无需为 pydata 重建镜像。

若不想自动建 venv（例如离线环境），加 `--no-sandbox-env`；此时缺解释器会直接退出并提示手动 setup。

只使用本地 SQLite、且不依赖平台 App 或企业管理员接口时，可以使用独立的仓库外 Profile：

```dotenv
DEEP_AGENT_LOCAL_USER=local-dev
DEEP_AGENT_LOCAL_ADMIN_BYPASS=true
DEEP_AGENT_SQLITE_PATH=/absolute/path/to/deep-agent-local.db
DEEP_AGENT_FILE_BACKEND=local
DEEP_AGENT_LOCAL_FILE_ROOT=/absolute/path/to/local-file-store
```

该开关只由回环地址上的 `deep_agent.local_dev` 读取，通过 ASGI 内部标记放行 Console 管理接口；
它不是 HTTP Header，也不会改变 `deep_agent.main:app` 的生产鉴权。未开启该开关时仍必须配置平台
Token，并由平台综合权限接口确认企业管理员身份。无 App 的本地请求可以完成 Provider、模型、Agent
配置和普通对话。显式启用本地文件后端后，附件上传、图片原生输入、`read_file`、大工具结果
Spill、上传 Skill和文件导出均使用本地 Blob，但保持与平台模式相同的 API、Message和工具协议。
Blob 位于 `DEEP_AGENT_LOCAL_FILE_ROOT/blobs/`，独立 `metadata.sqlite3`只保存文件索引、用户归属和
下载记录；它不属于Alembic数据库，也不会进入配置备份。普通文件仍按User隔离，App/Space只记录
来源。Console对本地导出的`local_`文件显示“下载本地文件”；平台模式固定不开放该下载端点。

`DEEP_AGENT_FILE_BACKEND`默认始终为`platform`，不会因为数据库是SQLite而自动切换。`local`仅允许
通过回环`deep_agent.local_dev`且主数据库为SQLite时启动；部署入口、Test MySQL或普通
`deep_agent.main:app`误配都会失败关闭。继承式MCP上下文仍需真实App/Space及对应服务能力。

访问入口：

- Console：<http://127.0.0.1:8000/console/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- Sandbox：<http://127.0.0.1:8000/api/v1/health/sandbox>

Console 是管理入口，只允许平台综合权限接口确认的企业管理员进入；固定超级用户
`admin` 不特殊放行。Agent 配置、模型、MCP、Skill、UI 工具和运维信息等管理 API
执行同一后端门禁，不能通过绕过页面直接访问。聊天、会话、附件、取消、压缩和按固定 ID
读取 Agent 等业务助手运行接口保持原有用户语义。详细边界见
[`docs/guides/console-admin-access.md`](docs/guides/console-admin-access.md)。

部署环境使用：

```bash
PYTHONPATH=src gunicorn -c deploy/gunicorn.conf.py deep_agent.main:app
```

需要企业微信的环境再额外启动一个Gateway副本：

```bash
PYTHONPATH=src python -m deep_agent.channels.gateway --host 0.0.0.0 --port 8010
```

两个进程必须配置相同的`DEEP_AGENT_CHANNEL_INTERNAL_TOKEN`；API还需配置
`DEEP_AGENT_CHANNEL_GATEWAY_URL`，Gateway可用`DEEP_AGENT_CHANNEL_API_URL`指定API地址。

`deep_agent.local_dev` 只用于本机回环调试，不用于容器部署。容器镜像若启用
`subprocess-dev`，构建阶段同样需要执行 `scripts/sandbox_venv/setup.sh`（或等价步骤），
并设置 `DEEP_AGENT_SANDBOX_PYTHON`；详见
[`scripts/sandbox_venv/README.md`](scripts/sandbox_venv/README.md)。

## 数据库基线

MySQL 的连接及业务文本字段应使用 `utf8mb4`，避免 Emoji 等字符损失引发保存失败；
已有数据库需要检查实际字段。详见[字符集部署要求与排查](docs/guides/mysql-character-set.md)。

新数据库直接执行 `alembic upgrade head`。当前 Alembic head 为`007`：生产基线 `001`完整包含原
Revision 001～012 的最终结构和默认数据：Provider/Model/Agent、MCP、Skill、UI工具、模型目录、
`chat_sessions + chat_session_events` Event Store 和 `global_settings`。全新初始化不生成 Web Search 配置行；
旧 `native` 配置读取为未配置，需管理员明确选择搜索供应商，见[Web 工具设计](docs/design/21-web-tools/web-tools-design.md)。
增量`002`增加企微Channel的Account、Session Binding和Message Run三表；渠道用户由管理员配置
`session_user_id`，Peer键名使用`userid/chatid`。
增量 `003` 移除 10 条数据库外键，由后端事务、记录锁和逻辑引用校验维持原有删除规则。
增量 `004` 新增 `integration_settings`，保存组件 AI 功能的 JSON 配置，不插入初始记录。见[集成配置](docs/guides/integrations.md)。
增量 `005` 允许 Agent 不配置模型；降级前须先补齐空模型。安装包支持导出、上传安装和自带目录安装，见[Agent 安装包](docs/guides/agent-packages.md)。

增量 `006` 新增企微应用及成员缓存，见[企微应用说明](docs/guides/wecom-applications.md)。
增量 `007` 为会话事件新增 `usage_model_id` 与逐请求 `usage` JSON，并建立日期／模型索引；管理员统计口径见[用量统计](docs/guides/diagnostic-usage.md)。

已发布的 `001` 至 `006` 不改写。上线顺序：完整备份 → 停止旧 API/Gateway 写入 → 升级并核验
`007` → 启动新版 → 业务冒烟；不能让旧代码继续向无外键数据库写入。
迁移预检发现孤儿数据会停止，MySQL 中断后保持维护状态并重跑。具体见
[无外键部署与回归](docs/guides/application-references.md)。

已经处于旧Revision 012的数据库不能重放`001`，也不能在旧镜像运行时手工修改
`alembic_version`。必须先完整备份并停止旧实例，再用`maintenance.rebaseline_database`校验物理
结构和全表内容哈希后只做`012 → 001`改标，随后只允许启动包含新`001`基线的镜像。完整切换边界
见[`src/maintenance/README.md`](src/maintenance/README.md)。

配置备份会复制全部配置表并只保留会话表结构；上传Skill的原始ZIP不塞入数据库备份。需要完整
留存或跨环境迁移时，同时在Console Skill页面执行“导出全部”。复制范围按源Alembic Revision自动
发现，新增配置表需登记明确逻辑依赖顺序；两张会话事实表、Channel Binding和Channel Run固定留空，
`channel_accounts`作为配置复制；完整边界见
[`src/maintenance/README.md`](src/maintenance/README.md)。

## 工具包类型

DeepAgent 当前支持：

- 内置工具：
  - `builtin:general`（通用工具）：只包含 `get_current_time`。
  - `builtin:sandbox`（Python 沙箱）：只包含 `execute_python`。
  - `builtin:web`（公开网络）：包含 `web_search`、`web_fetch` 与按模型能力注入的
    `web_image_view`，默认不关联。
- 系统工具不是 ToolPackage。Harness任务规划、`load_capability`、Skill Resource、`read_file`、
  `export_file_to_download_center`、`read_tool_result`、`search_tools` 和 MCP Resource
  由运行时按 Agent/Run 条件自动注入，只在 Console 的只读“系统工具”目录中说明。
- 每个Run只暴露Harness `Planning`的`write_plan`完整快照工具。计划仍以普通Tool Call/Result
  Event保存，不新增计划表；Deferred新Run通过共同`turn_id`恢复本Turn最后一次成功快照，新用户
  Turn从空PlanStore开始。Console从Event Store投影最新快照和每次更新位置。
- MCP Tool Search使用字段化词法检索：标准协议名、MCP `title`、参数和描述提供召回依据；支持
  拼音与有限错字纠正，但不把词法匹配伪装成任意中英文语义翻译。
- Skill 不再属于 ToolPackage。Agent 通过独立 `skills` 列表选择，Harness 按需加载 `SKILL.md`；
  Skill 不能绕过 `capabilities` 获得 MCP 或其他工具。
- `mcp:<name>`：URL或Builtin MCP；历史stdio记录只允许查看/删除，不能新建、修改或运行。
- `ui:builtin`：代码内置的确认和候选选择交互。
- `ui:<package>`：数据库管理的手工 UI 工具包，Agent 按包关联。

产品配置单位是 Tool Package，每个包严格包装一个 Pydantic AI Toolset。资源目录没有第二层
启停状态：Agent 的 `capabilities` 字段保存本 Agent 明确关联的工具包编码，新 Agent 默认为空；
`agents.skills` 同样只保存明确关联的 Skill。UI 工具包支持按 Agent 保存单工具范围；未限定时沿用整包关联。Output Tool 是最终输出契约，
不属于工具包，也不进入 `capabilities`。

Skill 管理接口和包格式见
[`docs/design/12-agent-skills/skill-system-design.md`](docs/design/12-agent-skills/skill-system-design.md)。

## 大工具结果

v0.4.0 使用 Harness `ToolOutputLimits` 在工具执行后、结果进入下一次模型请求前识别超大结果。
完整内容保存到当前FileStore（部署默认平台；显式本地开发模式为本地Blob），模型历史只保留首尾预览、结构草图和 File ID；模型需要细节时
通过系统工具 `read_tool_result` 按行或关键词读取。该机制不增加 Artifact 表，句柄权限只从当前
会话的原始 Tool Result Event 恢复；结构化摘要中的 Evidence 只提供来源索引，不能扩大访问范围。完整边界见
[`docs/design/05-tool-optimization/tool-optimization-design.md`](docs/design/05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits-与大工具结果外置)。

读取结果的 `source_line_numbers` 是正文各行对应的原文行号（从 1 开始），
`source_total_count` 是原文总行数。关键词按字面匹配；可传 `context_lines=3` 查看前后文
（默认 0，最多 5），重叠范围合并。`offset` 仍从 0 开始，表示筛选并合并后的行集合位置，
不等于原文行号；续读请直接使用 `next_read`，长行的字符游标和返回大小上限保持不变。

## 单轮请求预算

`DEEP_AGENT_MAX_REQUESTS` 默认 40，已有显式环境配置优先。计数对象是模型请求，
不是单个工具调用；同一用户轮次的前端工具回传和 Deferred 续跑共用额度。
达到约 80% 时在模型请求中提醒聚焦关键缺口，最后一次请求关闭工具，只生成阶段性总结，
说明已完成和未确认事项，并提示可以回复“继续”。用户发新消息重新获得额度。
正常收束沿用现有文字与 final 事件，不新增客户端协议；真实 Provider、Token 上限等错误仍如实返回。
自动压缩沿用共享请求预算并保留最后一次回答；标题生成不占聊天预算。

## 文件发布到下载中心

v0.4.18自动注入系统工具`export_file_to_download_center`。当请求具备可信的用户、App和Space上下文时，
模型可以把新生成的UTF-8文本内容上传为平台文件并登记到当前用户下载中心，也可以复用当前会话附件或
已外置大工具结果的File ID，仅新增下载记录而不重复上传。工具只返回文件与下载记录元数据；最终正文
提示用户前往下载中心获取，不把它伪装成结构化最终输出或聊天附件协议。完整边界见
[`docs/design/08-artifact-export/`](docs/design/08-artifact-export)。

## 测试

```bash
PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend/console/*.test.mjs

# 压缩核心覆盖率门槛（开发依赖，不进入生产镜像）
python3.12 -m pip install -r requirements-dev.txt
PYTHONPATH=src python3.12 tests/check_context_compaction_coverage.py
```

具体开发和测试要求见根目录及子目录的 `AGENTS.md`。

设计课题和后续待办见 [`docs/design/README.md`](docs/design/README.md) 与
[`docs/design/TODO.md`](docs/design/TODO.md)。
