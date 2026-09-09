# DeepAgent 项目结构与职责

本文说明 v0.8.4 的唯一源码布局。本地与线上使用相同目录和 Python 导入方式，旧链接与网关兼容包
已经移除。此次整理不改变业务接口或数据库结构，依赖继续按 `requirements.txt` 安装，发布仍交付
完整源码目录。具体实现细节以各目录的 `AGENTS.md` 为准。

## 目录总览

```text
deep-agent/
├── src/                       Python 源码
│   ├── deep_agent/            DeepAgent 产品后端
│   │   └── channels/gateway/  渠道网关唯一实现
│   ├── deepfos_platform/      平台配置、客户端、发现和运行时支撑
│   ├── maintenance/           人工执行的维护程序
│   └── deepagent_files.py     沙箱文件输入与产物发布辅助模块
├── console/                   后端随包发布的静态 Console，无构建步骤
│   └── vendor/                随仓库保存的第三方静态资源
├── mcp_servers/               仓库内置 MCP 实现与启动入口
├── migrations/                数据库结构迁移
├── deploy/                    Gunicorn、Compose 和 Jenkins 部署配置
├── scripts/                   开发与环境准备脚本
├── tests/                     Python、接口、MCP 和 Console 测试
├── docs/                      当前说明、历史设计、原型与归档
└── data/                      稳定本地数据库与文件，保留原地
```

根目录保留 `requirements.txt`、`alembic.ini` 等工程入口，不保留旧目录兼容链接。
`src/` 是源码归类，不代表改为安装 wheel；本轮不引入 `pyproject.toml`、Python 包构建或前端构建。
`.venv` 与 `.venv-sandbox` 是本地依赖环境，原地保留且不提交。`tmp/` 只在任务需要时创建，
验证结束清理，不作为长期目录保留。

## 运行链路

```text
FastAPI API
  -> 请求身份和上下文
  -> AgentSpec 与工具包解析
  -> Pydantic AI Agent
  -> ToolPackage 选择 -> Function / MCP / External Toolset
  -> SQL 会话与事件持久化
  -> SSE / JSON 响应
```

`src/deep_agent/` 是业务实现主体，Python 包名仍为 `deep_agent`。其内部职责为：

- `api/`：HTTP 接口和依赖组装。
- `agent/`：模型、运行器、消息、附件和事件处理。
- `capabilities/`：工具包目录以及 General、MCP、UI Toolset 适配。
- `models/`：API、配置服务和 Agent 共用的 Provider 工厂、模型适配和 models.dev 目录解析；不是 ORM 表。
- `files/`：附件与 Skill 文件统一读取、权限校验、分页和 Run内缓存。
- `skills/`：线上 Skill 包校验、目录、运行时物化和只读资源工具。
- `builtin_skills/`：随代码发布、只读的内置 Skill 包。
- `db/`：ORM、Store、会话协调和数据库连接。
- `schemas/`：API 输入输出结构。
- `core/`：应用上下文、日志和通用异常。
- `conversation/`：Event 到普通 History 的确定性投影，不是第二份会话存储。
- `services/`：配置导入、模型配置事务与渠道网关客户端等应用服务。
- `security/`：控制台授权与平台用户资料查询。
- `search/`：本地词法检索内核，供工具发现复用，与联网 `web_search` 不同。
- `ptc/`、`sandbox/`、`web/`：分别拥有程序编排、Python 子进程执行、公开 Web 工具的实现。
- `channels/`：公共消息/Adapter契约、注册表、Peer队列、五个渠道命令与帮助目录。
- `channels/gateway/`：可选渠道网关进程、运行配置、Account轮询和内部API/DeepAgent Client。
- `channels/adapters/wecom/`：企微SDK的唯一引用边界，负责SDK事件转换、连接状态、原生媒体和回复调用。

渠道入站链路为“官方SDK → WeCom Adapter → 公共ChannelService → 现有Chat API”，出站反向调用
Adapter的原生SDK回复。Gateway不是MCP工具，也不包含模型执行引擎；新增飞书需实现其Adapter与配置，
而不是另建一套Gateway或重写共享Chat接口。当前单API worker/单Gateway和部署说明见
[`wecom-channel.md`](wecom-channel.md)。

## 工具包边界

当前工具包来源只有：

| 名称 | 来源 | 用途 |
| --- | --- | --- |
| `builtin:general` | `src/deep_agent/capabilities/general/` | 内置“通用工具”，提供 UTC 时间 |
| `builtin:sandbox` | `src/deep_agent/sandbox/` | 内置“Python 沙箱”，提供无状态 Python 计算 |
| `builtin:ptc` | `src/deep_agent/ptc/` | 程序编排，运行时只调用已授权且已披露的 MCP |
| `builtin:web` | `src/deep_agent/web/` | Web Search 与 Web Fetch |
| `mcp:<name>` | 数据库或 `mcp_servers/` | 外部或内置 MCP 工具 |
| `ui:builtin` | 代码 | 确认和候选选择 HITL 工具包 |
| `ui:<package>` | `ui_tool_packages` + `ui_tool_definitions` | Agent 显式选择的手工 HITL 工具包 |

分类只是 ToolPackage 的 `category` 字段；ToolPackage 是 Agent 配置单位，Toolset 是运行时
交给 Pydantic AI 的对象。一个包严格包装一个 Toolset，当前不提供包内逐工具授权；未来可在
该 Toolset 上使用过滤包装器实现包内工具选择。

所有工具包只在 `agents.capabilities` 中显式关联后进入Run；新Agent默认空列表。Skill 不属于
工具包，也不进入 `agents.capabilities`。Agent 使用独立 `agents.skills` 选择
Builtin 或上传 Skill；Harness 只先披露名称和简介，模型按需调用 `load_capability` 加载正文。
Skill 不能扩大 MCP 或其他工具授权。

`read_file`等系统工具也不属于 ToolPackage。它们按本轮附件、已选 Skill或 MCP能力自动注入，
只在 Console“系统工具”目录中只读展示，不能写入 `agents.capabilities`。

## 容易混淆的目录

### 命名与层级约定

目录按真实职责划分，不为每个文件或未来能力预建空目录。Python 包使用 `snake_case`，
文档课题使用编号加 `kebab-case`，Skill 编码和第三方静态资源名沿用各自契约。
各层不必强制同一种单复数：API 路由、ORM 表、产品编码和 Python 包名不是同一件事。

本轮逐层核对后采用以下边界：

| 层级 | 保留或整理后的子目录 | 原因 |
| --- | --- | --- |
| `agent/` | `context/` | Agent 执行和上下文预算、压缩；跨层模型支持已移到 `models/` |
| `api/` | `v1/` | 对外版本化 HTTP 路由，不因目录整理变更 REST/SSE 路径 |
| `capabilities/` | `general/`、`mcp/`、`ui/` | 工具包注册与协议适配；`native/` 改为明确的 `general/`，协议 `builtin:general` 不变 |
| `channels/` | `adapters/wecom/`、`gateway/` | 厂商协议与公共宿主分离；删除 Gateway 内转发企微解析、SDK 日志的旧兼容文件 |
| `db/stores/` | `agent/`、`chat_session/`、`event/`、`llm_model/`、`mcp/`、`model_catalog/`、`skill_package/`、`ui_tool/`、`ui_tool_package/` | 按实体组织 Store，已有方言文件保持在实体内部；单文件 Store 不为了外观一致额外嵌套 |
| `core/` | 无额外子层 | `lib/const.py` 的 MCP 共享常量移为 `core/mcp_constants.py`；保持轻量导入，不触发配置或工具运行时加载 |
| `sandbox/` | `profiles/`、`subprocess_dev/` | 运行配置数据与开发执行后端分离；不是 PTC 的另一个入口 |
| `builtin_skills/` | 每个 Skill 的 `assets/`、`references/` | 可分发 Skill 资产，不与 `skills/` 管理代码混放 |
| `console/` | `configuration/`、`assets/provider-logos/`、`vendor/` | 生产页面、资源与第三方库；全部设计原型归 `docs/design/17-console-ui/` |
| `docs/` | `guides/`、`design/`、`archive/` | 当前指南、课题设计、历史归档；历史课题 `20-code-mode/` 保留原名，当前实现为 `ptc/` |
| `docs/guides/` | `client-adaptation/`、`experience/`、`fixtures/` | 客户端接入、实现经验、配套样例；设计下原型可带自身资源层级 |
| `deploy/` | `jenkins/` | 活动流水线与运行部署配置；归档脚本不混作默认入口 |
| `migrations/` | `versions/` | Alembic 标准结构，Revision 与数据库不变 |
| `mcp_servers/` | `deepcube/` | 独立 Server 启动器，不能并入 Agent 工具实现；当前目录扫描与配置编码保持一致 |
| `scripts/` | `sandbox_venv/` | 开发环境 Shell 脚本，不再保留无用途的 Python 包标记 |
| `tests/` | `unit/`、`integration/`、`frontend/console/`、`fixtures/`、`helpers/` | 按测试执行边界分类；共用模型构造器归 `helpers/`，集成 HTTP/MCP 生命周期辅助仍在 `integration/helpers/` |
| `tests/fixtures/` | `mcps/echo_fixture/`、`sessions/` | 公共 MCP 与会话夹具；`integration/fixtures/` 保存集成专用进程样例，不是重复生产代码 |

其余 `files/`、`models/`、`ptc/`、`schemas/`、`search/`、`security/`、`services/`、`skills/`、
`web/`、`conversation/` 都是当前具体功能的单层实现，职责见上文，不再人为加一层空包装。
`local_security.py` 和 `mcp_request_context.py` 是无运行时依赖的共享策略，当前保留轻量入口，
避免移入带初始化导入的安全或 MCP 包后形成循环依赖。

### `src/deepfos_platform/`

平台运行时依赖，不是 DeepFOS Platform 产品源码，也不是 Agent 能力。它承载
DeepAgent 已经依赖的配置、平台客户端、服务发现和部署适配。

内部 `client/models/` 是平台 API DTO，`conf/` 负责配置源与 Option，`discovery/` 查询服务，
`registry/` 注册服务，`runtime/` 处理进程生命周期，`errors/` 定义错误契约，`lib/` 提供跨层基础实现。
这些是平台支撑包已有的导入边界，不与产品的 `models/`、`core/` 混为一层，也不为统一词形改名。

### `src/maintenance/`

只供开发或运维人员从命令行执行。例如配置数据库备份、`channel_origins.py`的本地渠道来源修复。
来源修复默认dry-run，停服务后apply会先生成敏感SQLite备份，不运行Alembic。这里的程序不会注册为
Pydantic AI Tool，也不会出现在 Console 的 Agent 能力列表。

### `mcp_servers/` 与 `src/deep_agent/skills/`

- `mcp_servers/` 是 MCP Server 的本地实现或启动入口，运行时仍通过 MCP 协议接入。
- `src/deep_agent/skills/` 是线上 Skill 管理和运行适配；Skill 只提供任务方法、References 和 Assets，
  不自动授予工具权限；Agent同时关联含脚本Skill和 `builtin:sandbox`时，可以通过条件式系统工具
  `execute_skill_script`执行已索引脚本。

MCP 提供可执行工具；Skill 提供渐进披露的说明和素材。两者生命周期、权限和协议不同，不应混放。

## 本地与线上统一入口

当前源码、资源和配置只维护下列位置：

| 用途 | 唯一位置 |
| --- | --- |
| Python 产品与支撑包 | `src/deep_agent/`、`src/deepfos_platform/`、`src/maintenance/`、`src/deepagent_files.py` |
| 渠道网关 | `src/deep_agent/channels/gateway/`，模块入口 `deep_agent.channels.gateway` |
| Console 与第三方资源 | `console/`、`console/vendor/` |
| 内置 MCP | `mcp_servers/` |
| 数据库迁移 | `migrations/`，根 `alembic.ini` 将 `src` 加入模块搜索路径 |
| Gunicorn 与 Compose | `deploy/gunicorn.conf.py`、`deploy/docker-compose.yml` |
| 当前 Jenkins 脚本 | `deploy/jenkins/Jenkinsfile.gateway`、`Jenkinsfile.api-only`、`Jenkinsfile.diagnose` |
| 原始 Jenkins 脚本归档 | `docs/archive/jenkins/Jenkinsfile.original`，按原字节保存，不作为发布入口 |

根目录旧包链接、`frontend/` 与旧网关兼容包已移除。依赖安装、启动、维护和测试命令都从仓库根执行；
Python 使用显式 `PYTHONPATH=src`：

```bash
python3.12 -m pip install -r requirements.txt
PYTHONPATH=src python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/sqlite.env --port 8000
```

部署 API、维护命令、测试与本地数据库辅助服务分别使用：

```bash
PYTHONPATH=src gunicorn -c deploy/gunicorn.conf.py deep_agent.main:app
PYTHONPATH=src python3.12 -m maintenance.config_backup --help
PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'test_*.py' -v
docker compose --project-directory "$PWD" -f deploy/docker-compose.yml up -d
```

Test 联调改用 `~/.config/deep-agent/test.env`。需要同时运行本地渠道网关时，使用仓库根
`AGENTS.md` 中的 `deep_agent.combined_runtime` 命令；不要同时启动独立 Gateway。
Windows PowerShell 先设置 `$env:PYTHONPATH = 'src'`，再执行对应 Python 命令，不需要创建符号链接。
Compose 显式保留仓库根作为项目目录，避免配置文件移位后改变默认项目名和数据卷范围。

部署从提交的同一源码树构建，打包应包含完整 `src/`、`console/`、`mcp_servers/`、`migrations/`、
`deploy/` 以及根依赖和配置入口。上线需要使用当前流水线重新构建和发布，目录整理不代表线上已经更新。
完整发布要求见 [`deploy/README.md`](../../deploy/README.md)。

## 本地数据与临时产物

稳定 SQLite `data/deep-agent-local.db` 与文件目录 `data/deep-agent-local-files/` 保留原地，
继续由仓库外 Profile 引用，不因目录整理重新初始化、覆盖或提交 Git。
已确认无业务内容的旧缓存和临时产物清理后，不在项目中保留另一份备份；需要恢复的历史本地调试备份
进入系统废纸篓。后续验证按需创建 `tmp/`，产物标记 `TMP_to_delete`，在测试结束或任务收尾时清理。
稳定数据库、本地文件、依赖环境与用户交付材料不得作为临时产物清理。

保留的侧边栏原型统一放在 `docs/design/17-console-ui/`，包括 0825 单层导航方案和
[`docs/design/17-console-ui/Prototype DeepAgent 统一侧边栏/`](<../design/17-console-ui/Prototype DeepAgent 统一侧边栏/>)。
生产界面只从 `console/` 发布，原型不进入产品运行链路。
