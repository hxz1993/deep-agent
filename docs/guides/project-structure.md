# DeepAgent 项目结构与职责

本文说明当前仓库的目录职责与兼容入口。此次整理只调整工程位置，业务接口、数据库结构、依赖安装
和源码目录发布方式保持原有约定。具体实现细节以各目录的 `AGENTS.md` 为准。

## 目录总览

```text
deep-agent/
├── src/                       Python 源码
│   ├── deep_agent/            DeepAgent 产品后端
│   │   ├── channels/gateway/  渠道网关实现
│   │   └── channel_gateway/   旧 Python 模块导入与命令兼容
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
├── docs/                      当前说明、历史设计与文档内原型
├── data/                      稳定本地数据库与文件，保留原地
└── tmp/                       标记为 TMP_to_delete 的临时产物
```

根目录保留 `requirements.txt`、`alembic.ini` 等工程入口以及下文列出的兼容链接。
`src/` 是源码归类，不代表改为安装 wheel；本轮不引入 `pyproject.toml`、Python 包构建或前端构建。

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
- `capabilities/`：工具包目录以及 Native、MCP、UI Toolset 适配。
- `files/`：附件与 Skill 文件统一读取、权限校验、分页和 Run内缓存。
- `skills/`：线上 Skill 包校验、目录、运行时物化和只读资源工具。
- `builtin_skills/`：随代码发布、只读的内置 Skill 包。
- `db/`：ORM、Store、会话协调和数据库连接。
- `schemas/`：API 输入输出结构。
- `core/`：应用上下文、日志和通用异常。
- `channels/`：公共消息/Adapter契约、注册表、Peer队列、五个渠道命令与帮助目录。
- `channels/gateway/`：可选渠道网关进程、运行配置、Account轮询和内部API/DeepAgent Client。
- `channels/adapters/wecom/`：企微SDK的唯一引用边界，负责SDK事件转换、连接状态、原生媒体和回复调用。
- `channel_gateway/`：旧模块的逐模块别名与 CLI 委托；实现统一维护在 `channels/gateway/`。

渠道入站链路为“官方SDK → WeCom Adapter → 公共ChannelService → 现有Chat API”，出站反向调用
Adapter的原生SDK回复。Gateway不是MCP工具，也不包含模型执行引擎；新增飞书需实现其Adapter与配置，
而不是另建一套Gateway或重写共享Chat接口。当前单API worker/单Gateway和部署说明见
[`wecom-channel.md`](wecom-channel.md)。

## 工具包边界

当前工具包来源只有：

| 名称 | 来源 | 用途 |
| --- | --- | --- |
| `builtin:general` | `src/deep_agent/capabilities/native/` | 内置“通用工具”，提供 UTC 时间 |
| `builtin:sandbox` | `src/deep_agent/sandbox/` | 内置“Python 沙箱”，提供无状态 Python 计算 |
| `builtin:ptc` | `src/deep_agent/code_mode/` | 程序编排，运行时只调用已授权且已披露的 MCP |
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

### `src/deepfos_platform/`

平台运行时依赖，不是 DeepFOS Platform 产品源码，也不是 Agent 能力。它承载
DeepAgent 已经依赖的配置、平台客户端、服务发现和部署适配。

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

## 旧路径兼容与开发命令

旧文件路径由相对符号链接指向唯一真实目录，后续修改直接使用右列路径，不复制第二份实现。

| 旧入口 | 唯一真实位置 |
| --- | --- |
| `deep_agent/` | `src/deep_agent/` |
| `deepfos_platform/` | `src/deepfos_platform/` |
| `maintenance/` | `src/maintenance/` |
| `deepagent_files.py` | `src/deepagent_files.py` |
| `frontend/console/` | `console/` |
| `frontend/vendor/` | `console/vendor/` |
| `mcps/` | `mcp_servers/` |
| `alembic/` | `migrations/` |
| `deep_agent/gunicorn.conf.py` | `deploy/gunicorn.conf.py`（经 `src/deep_agent/gunicorn.conf.py` 链接） |
| `docker-compose.yml` | `deploy/docker-compose.yml` |
| `docs/guides/jenkins/` | `deploy/jenkins/` |

Python 的旧 `deep_agent.channel_gateway` 导入路径由兼容模块转交给
`deep_agent.channels.gateway`，`python -m deep_agent.channel_gateway` 继续委托相同入口执行。
这层 Python 兼容与目录符号链接用途相同，均不维护另一套网关业务代码。

依赖安装、启动、维护和测试命令继续从仓库根执行：

```bash
python3.12 -m pip install -r requirements.txt
python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/sqlite.env --port 8000
```

Test 联调改用 `~/.config/deep-agent/test.env`。需要同时运行本地渠道网关时，使用仓库根
`AGENTS.md` 中的 `deep_agent.combined_runtime` 命令；不要同时启动独立 Gateway。
部署仍使用同一源码目录，要求归档和解压同时保留符号链接及其目标；Windows 检出环境也必须支持
真实符号链接，不能把链接检出为普通文本文件。完整发布要求见 [`deploy/README.md`](../../deploy/README.md)。

## 本地数据与临时产物

稳定 SQLite `data/deep-agent-local.db` 与文件目录 `data/deep-agent-local-files/` 保留原地，
继续由仓库外 Profile 引用，不因目录整理重新初始化、覆盖或提交 Git。
已明确标记 `TMP_to_delete` 的旧根目录产物收拢到 `tmp/TMP_to_delete-prior-artifacts/`。
现有原型、未标记的 `output/` 和根目录 `skills/` 不因名称推断为垃圾，也不在本轮移动或删除。
