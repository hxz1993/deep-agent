# DeepAgent

DeepAgent 是基于 FastAPI、Pydantic AI 和 SQLAlchemy 的多用户 Agent 服务，并
随服务发布一体化运维控制台。

当前产品版本为 `0.6.0`。`deep_agent.__version__` 是唯一代码版本来源，FastAPI、
`GET /info` 和 Console 顶部版本徽标均读取该值。后续正式发布默认递增
`MAJOR.MINOR.PATCH` 的第三位，只有明确规划重大版本时才调整前两位。产品版本不通过
环境变量或数据库覆盖，也不等同于 Alembic 数据库结构 revision。

各版本功能变更与迁移说明见 [`CHANGELOG.md`](CHANGELOG.md)。

Console 对话测试提供独立的“对话 / 轨迹”页签。会话历史、轨迹、Plan 和持久化 SSE 统一读取
`chat_session_events`；轨迹按 Sequence 原位展示 Run、模型 Part、工具调用/结果和 Compaction，
不再从整条 ModelMessage 做第二套拆解，也不额外建立轨迹表。能力边界见
[`docs/design/18-session-trajectory/`](docs/design/18-session-trajectory/)。

v0.5.6 完成 Provider + Model 两层配置：Provider 独占 Pydantic AI 适配类型、地址和密钥，
Model 只保存 Provider 外键、模型编码与能力属性；两层启停共同决定模型是否可用。Agent、辅助模型和
聊天请求继续只引用 `model_id`，模型目录仍是只读候选数据，不参与运行时关联。设计与迁移边界见
[`docs/design/19-model-provider-architecture/`](docs/design/19-model-provider-architecture/)。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| [`CHANGELOG.md`](CHANGELOG.md) | 产品版本更新记录与迁移摘要 |
| [`deep_agent/`](deep_agent/AGENTS.md) | API、Agent 运行时、能力装配、会话和数据库 |
| [`frontend/`](frontend/AGENTS.md) | `/console/` 静态控制台 |
| [`mcps/`](mcps/AGENTS.md) | 仓库内置 MCP Server |
| [`deep_agent/builtin_skills/`](deep_agent/builtin_skills/) | 代码内置只读 Skill；上传 Skill 由平台文件服务保存原包 |
| [`deepfos_platform/`](deepfos_platform/README.md) | 平台配置、客户端、服务发现和部署运行时支撑 |
| [`maintenance/`](maintenance/README.md) | 人工维护程序，不属于 Agent 工具 |
| [`tests/`](tests/README.md) | Python、接口、MCP 和 Console 测试 |
| [`docs/`](docs/README.md) | 当前说明、技术设计和交互原型 |

完整边界见
[`docs/guides/project-structure.md`](docs/guides/project-structure.md)。

## 本地启动

默认使用本机 Python 3.12。安装依赖：

```bash
python3.12 -m pip install -r requirements.txt
```

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
python3.12 -m deep_agent.local_dev \
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
uvicorn deep_agent.main:app --host 0.0.0.0 --port 8000
```

`deep_agent.local_dev` 只用于本机回环调试，不用于容器部署。容器镜像若启用
`subprocess-dev`，构建阶段同样需要执行 `scripts/sandbox_venv/setup.sh`（或等价步骤），
并设置 `DEEP_AGENT_SANDBOX_PYTHON`；详见
[`scripts/sandbox_venv/README.md`](scripts/sandbox_venv/README.md)。

## 数据库基线

新数据库直接执行 `alembic upgrade head`。当前 Alembic head 为 `008`：`002` 统一 ToolPackage
与 UI 工具包；`003` 将线上 Skill 与
ToolPackage 分离；`004`清理已废弃的`builtin:attachment`；`005`和`006`依次删除已下线的
预制MCP记录，并从显式Agent能力列表中清理对应引用；`007`增加独立Provider层、模型硬外键并删除
模型表中的重复连接字段；`008`将会话域重建为`chat_sessions + chat_session_events`。

`008`不在线转换旧会话：它删除旧会话历史并建立空 Event Store，但保留所有配置表。共享环境升级前
必须先用 Revision 007 代码分别执行配置备份和会话归档，停掉全部旧实例后才能迁移；旧版本不能与
008并行运行。完整备份和切换边界见[`maintenance/README.md`](maintenance/README.md)。

配置备份会复制全部配置表并只保留会话表结构；上传Skill的原始ZIP不塞入数据库备份。需要完整
留存或跨环境迁移时，同时在Console Skill页面执行“导出全部”。备份程序会校验每张ORM业务表都已
明确归类，避免后续新增表时静默漏备份；完整边界见[`maintenance/README.md`](maintenance/README.md)。

## 工具包类型

DeepAgent 当前支持：

- 内置工具：
  - `builtin:general`（通用工具）：默认启用，只包含 `get_current_time`。
  - `builtin:sandbox`（Python 沙箱）：默认关闭，只包含 `execute_python`。
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
- `ui:builtin`：代码内置的确认和候选选择交互，默认关闭。
- `ui:<package>`：数据库管理的手工 UI 工具包，Agent 按包启用。

产品配置单位是 Tool Package，每个包严格包装一个 Pydantic AI Toolset。Agent 的
`capabilities` 字段保存工具包编码；当前不提供包内逐工具授权。Output Tool 是最终输出契约，
不属于工具包，也不进入 `capabilities`。

Skill 管理接口和包格式见
[`docs/design/12-agent-skills/skill-system-design.md`](docs/design/12-agent-skills/skill-system-design.md)。

## 大工具结果

v0.4.0 使用 Harness `ToolOutputLimits` 在工具执行后、结果进入下一次模型请求前识别超大结果。
完整内容保存到当前FileStore（部署默认平台；显式本地开发模式为本地Blob），模型历史只保留首尾预览、结构草图和 File ID；模型需要细节时
通过系统工具 `read_tool_result` 按行或关键词读取。该机制不增加 Artifact 表，句柄权限只从当前
会话的原始 Tool Result Event 恢复；结构化摘要中的 Evidence 只提供来源索引，不能扩大访问范围。完整边界见
[`docs/design/05-tool-optimization/tool-optimization-design.md`](docs/design/05-tool-optimization/tool-optimization-design.md#92-tooloutputlimits-与大工具结果外置)。

## 文件发布到下载中心

v0.4.18自动注入系统工具`export_file_to_download_center`。当请求具备可信的用户、App和Space上下文时，
模型可以把新生成的UTF-8文本内容上传为平台文件并登记到当前用户下载中心，也可以复用当前会话附件或
已外置大工具结果的File ID，仅新增下载记录而不重复上传。工具只返回文件与下载记录元数据；最终正文
提示用户前往下载中心获取，不把它伪装成结构化最终输出或聊天附件协议。完整边界见
[`docs/design/08-artifact-export/`](docs/design/08-artifact-export/)。

## 测试

```bash
python3.12 -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend/console/*.test.mjs

# 压缩核心覆盖率门槛（开发依赖，不进入生产镜像）
python3.12 -m pip install -r requirements-dev.txt
python3.12 tests/check_context_compaction_coverage.py
```

具体开发和测试要求见根目录及子目录的 `AGENTS.md`。

设计课题和后续待办见 [`docs/design/README.md`](docs/design/README.md) 与
[`docs/design/TODO.md`](docs/design/TODO.md)。
