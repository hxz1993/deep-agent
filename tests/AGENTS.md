# AGENTS.md — 测试

> 父索引：[`../AGENTS.md`](../AGENTS.md)

自动化测试位于仓库根 `tests/`，使用 **`unittest`**（非 pytest 作为主入口）。在仓库根运行，需已安装 `requirements.txt` 依赖。

---

## 运行命令

```bash
# 全部
python3.12 -m unittest discover -s tests -p "test_*.py" -v

# 排查失败：首个报错即停（推荐配合 tests/run.ps1 的 30s 超时）
python3.12 -m unittest discover -s tests -p "test_*.py" --failfast -v

# 仅单元（无网络、无外部子进程）
python3.12 -m unittest discover -s tests/unit -p "test_*.py" -v

# 仅集成（HTTP、MCP 子进程、header 转发）
python3.12 -m unittest discover -s tests/integration -p "test_*.py" -v
```

---

## 目录结构

```
tests/
├── unit/                    # 纯逻辑，快速
├── integration/             # 需启动 app 或 MCP 子进程
│   ├── fixtures/          # 测试用 MCP server（header_echo、pid_echo）
│   └── helpers/           # chat.py、mcp.py 公共 helper
└── README.md
```

---

## 单元测试（`unit/`）

| 文件 | 覆盖 |
| --- | --- |
| `test_agent_max_requests.py` | 全局 request 上限、旧 Agent 字段 422 兼容提示 |
| `test_code_mode.py` | PTC新旧编码兼容、MCP双通道、Monty调用、URL/File ID文件变量与脱敏 |
| `test_sandbox_*.py` | Python沙箱公网边界、FileStore输入、`deepagent_files`产物发布、回滚、依赖探针与子进程清理 |
| `test_global_settings.py` | 运行设置安全白名单、Web Search唯一配置、六Provider校验、Key掩码/保留和草稿测试 |
| `test_web_tools.py`、`test_web_proxy_compat.py` | `builtin:web`目录、视觉模型条件注入、HTML图片位置与URL规范化、固定Provider无降级、SSRF-safe Fetch/Image边界及HTTP代理fake-IP/SNI兼容 |
| `test_console_admin_auth.py` | 企业管理员放行、普通用户与固定admin拒绝、401/503、凭证缓存、管理路由审计和运行接口开放边界 |
| `test_builtin_mcp_config.py` | builtin MCP 配置规范化 |
| `test_database_url_resolution.py` | DSN 构建 |
| `test_dbms_config.py` | `DEEP_AGENT_DBMS_*` 解析 |
| `test_deepcube_server.py` | DeepCube 启动器 argv |
| `test_forward_headers.py` | MCP header 白名单 |
| `test_request_context.py` | App 推导 Space、默认中文和上下文接口校验 |
| `test_mcp_lease_manager.py` | MCP lease 管理 |
| `test_title_generator.py` | AI 标题清洗与无配置行为 |
| `test_catalog.py` | models.dev Lab/Model/Provider 标准化、白名单、扩展元数据和数据库视图 |
| `test_models_dev.py` | models.dev 严格获取、官方JSON解析与 Lab 可选补充 |
| `test_llm_models.py` | Provider硬外键、Provider内唯一性、模型启停、上下文、最大输出、输入模态与 tags Service/Store/Schema |
| `test_provider_registry.py` | 九种Provider顺序、默认地址、Pydantic AI Provider/Model工厂及配置失败边界 |
| `test_context_compaction.py` | 统一Token估算、八段单次摘要、Tool/Context Evidence、DSH式裁剪、超时/重试/取消、Provider Overflow识别、SSE状态和失败闭合 |
| `test_context_test_scenarios.py` | 管理员测试会话场景、实时预算、消息往返序列化、常驻接口与用户归属 |
| `test_attachment_extractors.py` | 文本编码、DOCX/XLSX 结构读取、分页与 Office 安全 |
| `test_file_tools.py` | 附件与 Skill统一文件工具的来源参数、权限、Run缓存、single-flight、分页和多模态返回 |
| `test_tool_package_registry.py` | ToolPackage Registry 的显式整包选择和空列表语义 |
| `test_sqlite_bootstrap.py` | 生产基线001加Channel增量002的空库创建、默认数据、完整结构及升降往返 |
| `test_baseline_migration_dialects.py` | 当前001→002在SQLite、MySQL、PostgreSQL的离线DDL编译 |
| `test_channel_migration.py` | 确认Head仍为002，Channel物理字段与ORM一致，重复升级保留数据，临时库降升往返 |
| `test_channel_gateway.py` | Account/Binding/Run Store、消息解析、幂等、SSE文本投影与Agent换代 |
| `test_channel_commands.py` | 公共命令识别、非企微测试Adapter、队列与独立停止/新建、超时及投递失败 |
| `test_channel_store_commands.py` | 无Session Binding、命令认领、新建会话原子换绑、来源标题、归属和重复执行保护 |
| `test_wecom_adapter.py` | 官方SDK包装边界、连接生命周期、消息薄转换、媒体和回复 |
| `test_rebaseline_database.py` | 旧012物理结构校验、显式改标、全表行数与内容哈希不变及异常拒绝 |
| `test_configuration_import.py` | 同Revision配置初始化、主键保留、空会话门禁、事务回滚和Skill File ID重写 |
| `test_runtime_dependencies.py` | Pydantic AI/Harness、OpenAI/aiohttp/deepfos联合版本锁定与模型导入 |
| `test_session_model_override.py` | Agent 对话 `model_id` 请求级覆盖（`build_runner_for_agent`） |
| `test_ui_tools_rfc.py` | builtin/manual UI 工具包、工具归属、Agent 整包选择、Schema 校验与 Deferred |

---

## 集成测试（`integration/`）

| 文件 | 覆盖 |
| --- | --- |
| `test_chat_header_forwarding.py` | HTTP → MCP header 透传 |
| `test_mcp_header_forwarding.py` | MCP 层 header 注入 |
| `test_mcp_run_scoping.py` | builtin MCP 按 run lease 隔离 client |
| `test_session_title.py` | 标题回显、AI 生成、兜底、DeepCube fixture |
| `test_llm_model_modalities.py` | 模型运行属性 CRUD 与 DB-only 模态 |
| `test_channel_accounts_api.py` | 渠道账号CRUD、管理员配置的会话用户、删除约束、未部署状态和测试503 |
| `test_channel_session_controls.py` | 可信渠道取消/run-status、真实Session归属、配置切换与旧任务身份隔离 |
| `test_wecom_ingress.py` | 官方SDK→队列→认证HTTP→模型/History引用与逐条发送者闭环；独立纯命令不进入模型 |
| `test_llm_model_catalog.py` | 目录 DB 查询、两种视图、全量刷新及失败保留旧目录 |
| `test_session_model_override.py` | Agent 流式对话 `model_id` 覆盖、默认回退、轮次切换 |
| `test_chat_attachment_tools.py` | 附件引用 → 模型调用工具 → SSE/历史持久化 → 同会话续读 |
| `test_configuration_import_api.py` | 企业管理员multipart预检/确认执行及配置DB不进入File Store |

公共fixture：`integration/fixtures/header_echo_server.py`、`pid_echo_server.py`；真实Builtin进程测试使用
`tests/fixtures/mcps/echo_fixture/`，该目录不会被生产Builtin扫描。

---

## 新增测试规范

1. **先分类**：无外部依赖 → `unit/`；需 HTTP/MCP/子进程 → `integration/`
2. **命名**：`test_<主题>.py`
3. **helper**：集成测试复用逻辑放 `integration/helpers/`，勿复制
4. **fixture MCP**：放 `integration/fixtures/`，保持自包含（同 `mcps/` 规则，不 import `deep_agent`）

---

## 陷阱

- 在 `deep_agent/` 子目录运行 discover 会找不到测试；**始终在仓库根**
- 集成测试可能依赖环境变量；勿把 `.env` 密钥写进测试
- 改 `capabilities/mcp/` 或 `agent/headers.py` 后跑 integration 相关用例
- 改内置 UI 工具时同时覆盖：`ui:builtin` 整包选择、空 `capabilities` 不加载、内置包/工具修改删除保护，以及 choice 的单选、多选、自由输入和空提交阻止。
- `builtin__confirm`、`builtin__choice` 不兼容旧 `confirm_action`、`ask_user_choice` pending 会话；测试夹具和断言不得继续把旧名称当作内置工具。
- 上下文压缩测试场景必须使用正式 Pydantic 消息类型并验证 serialize/deserialize 往返；不要直接
  插 JSON。场景随当前模型 trigger 动态扩展，断言不能写死消息条数或 Token 值。
- 附件集成测试必须验证 `file_id` 只来自当前原始会话、同页去重键包含
  `tool/file/sheet/offset/limit`，并至少用真实生成的 TXT、DOCX、XLSX 字节覆盖解析器。
- 配置备份按源Alembic Revision自动复制全部业务表，只排除`chat_sessions`和
  `chat_session_events`；新增配置表无需维护白名单，但必须在`test_config_backup.py`覆盖新Revision
  与旧Revision兼容，防止源库尚未升级时误要求未来表。
- 配置初始化只允许备份/目标Revision完全一致且两侧会话为空；测试必须断言主键原样保留、失败事务
  回滚、Skill只改目标File ID，并确认数据库上传文件不进入平台File Store。

更多：[`README.md`](README.md)、[`unit/README.md`](unit/README.md)、[`integration/README.md`](integration/README.md)。
