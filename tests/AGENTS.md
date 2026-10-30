# AGENTS.md — 测试

> 父索引：[`../AGENTS.md`](../AGENTS.md)

自动化测试位于仓库根 `tests/`，使用 **`unittest`**（非 pytest 作为主入口）。在仓库根运行，需已安装 `requirements.txt` 依赖。

源码统一从 `src/` 导入；本地和镜像都显式设置 `PYTHONPATH`，不再依赖根目录兼容链接。
测试子进程若需要同时导入 `tests` 或根目录的 `mcp_servers`，使用绝对 `src + 仓库根` 路径；
Sandbox Worker 仍只使用受控的 `src`，禁止继承宿主的任意 `PYTHONPATH`。

---

## 0.15.0 Python／Skill 执行回归

- `test_skill_execution_layout.py`：真实子进程验证原入口、同目录／包内相对导入、嵌套模块、入口身份、bundle边界、超时取消与临时目录清理，不保留main.py旧执行夹具。
- `test_skill_files.py`：Skill与普通Python共用附件授权、显式上传、文件引用、版本指纹及失败补偿；输入模块、选中资源和平台附件分别计量。
- Skill脚本返回内部ToolReturn，业务断言读取return_value；HTTP／SDK返回仍是执行信封，不据此增加History类型。PTC需用真实Monty和Python子进程验证文件事实回读，不以伪造MCP metadata代替。
- 五接口验收使用Test只读数据；OCR为明确标注的本地PDF Mock，不算客户接口／准确率验收。通过真实8000上传、Agent普通与PTC路径、文件下载并核验摘要，保留会话。全量测试与平台不支持的skip分别记录。

## 运行命令

运行中持久化回归入口：`unit/test_message_persistence.py`、`test_message_part_store.py`、
`integration/test_incremental_message_persistence.py`、`test_message_persistence_process_exit.py`、`test_channel_message_persistence.py`。
真实 SDK 需在模型静默/并行工具中途设屏障，从独立 HTTP/SQLite 连接读 History，不能先 flush 后宣称实时。
强杀验证只能使用隔离子进程及临时库；禁止杀稳定 8000 或重放真实业务工具。
Console 跟随验证包含请求串行、同 Sequence 状态更新、切页迟到数据及运行工具只读。正常/取消不新增 RUN 或异常回执；执行失败仅一条 execution/error，不能进入模型正文；半段文本不定时落库。

```bash
# 全部
PYTHONPATH=src python3.12 -m unittest discover -s tests -p "test_*.py" -v

# 排查失败：首个报错即停
PYTHONPATH=src python3.12 -m unittest discover -s tests -p "test_*.py" --failfast -v

# 仅单元（无网络、无外部子进程）
PYTHONPATH=src python3.12 -m unittest discover -s tests/unit -p "test_*.py" -v

# 仅集成（HTTP、MCP 子进程、header 转发）
PYTHONPATH=src python3.12 -m unittest discover -s tests/integration -p "test_*.py" -v
```

---

## 目录结构

```
tests/
├── unit/                    # 单模块与隔离依赖回归
├── integration/             # HTTP、Agent、MCP 等边界协作
│   ├── fixtures/             # 集成场景专用 MCP server 与历史样本
│   └── helpers/              # HTTP/SSE、MCP 生命周期与会话准备
├── helpers/                 # 跨测试组共用的身份、SQLite、模型等辅助代码
├── fixtures/                # 共用样本与测试专用 builtin MCP 扫描目录
│   ├── mcps/echo_fixture/    # 不进入生产 mcp_servers/ 扫描
│   └── sessions/             # 会话 JSON 样本
├── frontend/console/        # Console 的 Node.js 测试
├── check_context_compaction_coverage.py  # 上下文压缩覆盖率门槛
└── README.md
```

---

## 单元测试（`unit/`）

诊断日志定向验证：`test_diagnostics_runtime.py`（轮转、预算、并发、故障隔离）、
`test_diagnostics_query.py`（受控文件、管理员权限、游标及扫描预算）、
`test_runtime_diagnostics.py` / `test_channel_diagnostics.py`（真实 SDK/Runner、PTC、UI 等待、取消、持久化及投递失败），
`test_diagnostic_runner_equivalence.py`（真实 Runner + SQLite，日志启用/关闭/写入失败时请求、调用、SSE 与历史等价）。
使用隔离临时日志目录；日志开关或写入失败不能改变模型/工具次数、参数、返回、异常、SSE 和落库。
不要向真实群发送日志验收消息，也不能把真实凭证、模型正文或日志原文写进夹具。

| 文件 | 覆盖 |
| --- | --- |
| `test_agent_max_requests.py` | 全局 request 上限、旧 Agent 字段 422 兼容提示 |
| `test_ptc.py` | PTC新旧编码兼容、MCP双通道、Monty调用、URL/File ID文件变量与脱敏 |
| `test_ptc_stream_failures.py` | 真实 SDK/Monty + 隔离 SQLite：PTC 连续失败后原生恢复、嵌套等待取消、Provider异常及普通工具重试耗尽的 SSE/History/Registry 闭环 |
| `test_event_persister.py`、`test_runner_history.py` | 写入失败回执与超时释放、显式关闭 SDK 流后部分历史保存、终态写入失败不覆盖原始错误 |
| `test_sandbox_*.py` | Python公网及内网HTTP可达性、FileStore输入、`deepagent_files`产物发布、回滚、依赖探针与子进程清理 |
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
| `test_llm_models.py` | Provider逻辑引用、Provider内唯一性、模型启停、上下文、最大输出、输入模态与 tags Service/Store/Schema |
| `test_provider_registry.py` | 九种Provider顺序、默认地址、Pydantic AI Provider/Model工厂及配置失败边界 |
| `test_qwen_schema.py` | Qwen 3.8 出站可空类型兼容、Schema约束等价、引用／注解保护、其他模型不变、真实SDK普通／流式及错误入参仍被拒绝 |
| `test_context_compaction.py` | 统一Token估算、同轮闭合前缀、交接摘要和确定性用户保护、Tool/Context Evidence、有界超大材料、超时/重试/取消、Provider Overflow识别、SSE状态和失败闭合 |
| `test_context_test_scenarios.py` | 管理员测试会话场景、实时预算、消息往返序列化、常驻接口与用户归属 |
| `test_attachment_extractors.py` | 文本编码、DOCX/XLSX 结构读取、分页与 Office 安全 |
| `test_file_tools.py` | 附件与 Skill统一文件工具的来源参数、权限、Run缓存、single-flight、分页和多模态返回 |
| `test_document_providers.py`、`test_document_tools.py`、`test_document_uploads.py` | 专业文档配置、三家受控HTTP、授权复核、Run内单飞/恢复/清理、原生正文和24K/48K分页、显式上传策略 |
| `test_tool_package_registry.py` | ToolPackage Registry 的显式整包选择和空列表语义 |
| `test_sqlite_bootstrap.py` | 生产基线001、Channel增量002及逻辑引用003的空库创建、默认数据、完整结构及升降往返 |
| `test_baseline_migration_dialects.py` | 历史001→002离线DDL编译，以及003必须在线迁移 |
| `test_channel_migration.py` | 确认当前Head，Channel物理字段与ORM一致，重复升级保留数据，临时库降升往返 |
| `test_channel_gateway.py` | Account/Binding/Run Store、消息解析、幂等、SSE文本投影与Agent换代 |
| `test_channel_commands.py` | 公共命令识别、非企微测试Adapter、队列与独立停止/新建、超时及投递失败 |
| `test_channel_store_commands.py` | 无Session Binding、命令认领、新建会话原子换绑、来源标题、归属和重复执行保护 |
| `test_wecom_adapter.py` | 官方SDK包装边界、连接生命周期、消息薄转换、媒体和回复 |
| `test_jenkins_deploy.py`、`test_jenkins_api_only.py` | 网关/仅API流水线Shell、按名覆盖启动命令、失败中断、API探针及原始归档完整性 |
| `test_rebaseline_database.py` | 旧012物理结构校验、显式改标、全表行数与内容哈希不变及异常拒绝 |
| `test_configuration_import.py` | 同Revision配置初始化、主键保留、空会话门禁、事务回滚和Skill File ID重写 |
| `test_runtime_dependencies.py`、`test_upstream_code_mode.py` | 联合版本锁定、模型导入；真实 Hybrid/Monty 金额/UUID/日期规范化及跨代码片段累计交互上限 |
| `test_project_layout.py` | 旧入口已移除、唯一源码目录、规范命令与资源路径、Gateway导入和Sandbox子进程路径 |
| `test_session_model_override.py` | Agent 对话 `model_id` 请求级覆盖（`build_runner_for_agent`） |
| `test_ui_tools_rfc.py` | builtin/manual UI 工具包、工具归属、Agent 整包选择、Schema 校验与 Deferred |

---

## 集成测试（`integration/`）

`test_web_fetch_upstream.py` 用真实回环 HTTP 字节响应验证字符集、HTML 转换、图片及完整正文；
病态未闭合标签放入有超时和 finally 清理的隔离子进程，避免解析回归卡住整个套件。
升级 Pydantic AI 时同时跑 MCP 服务说明/SDK Toolset.id、前端回传、40 次预算和 PTC 回归。


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
| `test_document_runner.py` | 真实装配的供应商快照、下一Run配置、SDK流与失败/取消清理、已有压缩和不二次外置 |
| `test_configuration_import_api.py` | 企业管理员multipart预检/确认执行及配置DB不进入File Store |

公共fixture：`integration/fixtures/header_echo_server.py`、`pid_echo_server.py`；真实Builtin进程测试使用
`tests/fixtures/mcps/echo_fixture/`，该目录不会被生产Builtin扫描。

---

## 新增测试规范

1. **先分类**：无外部依赖 → `unit/`；需 HTTP/MCP/子进程 → `integration/`
2. **命名**：`test_<主题>.py`
3. **helper**：跨测试组共用逻辑放 `helpers/`，包括 `models.py`；HTTP/SSE、MCP 生命周期等集成专用逻辑放 `integration/helpers/`，勿复制
4. **fixture**：跨测试组样本放 `fixtures/`，集成场景专用样本放 `integration/fixtures/`；Builtin扫描替身固定放 `fixtures/mcps/`，不得进入生产 `mcp_servers/`。测试MCP保持自包含（同 `mcp_servers/` 规则，不 import `deep_agent`）

---

## 陷阱

- 渠道累加展示必须断言工具/思考等待时的当前投递帧仍含全部公开正文，不能只断言曾经发过累加文本。
  首段前企微原生加载，有正文后保留正文加文字等待提示；临时投递失败不重跑，final只保留最终正文。
- 在 `src/deep_agent/` 子目录运行 discover 会找不到测试；**始终在仓库根**
- 集成测试可能依赖环境变量；勿把 `.env` 密钥写进测试
- 改 `capabilities/mcp/` 或 `agent/headers.py` 后跑 integration 相关用例
- 改内置 UI 工具时同时覆盖：`ui:builtin` 整包选择、空 `capabilities` 不加载、内置包/工具修改删除保护，以及 choice 的单选、多选、自由输入和空提交阻止。
- `builtin__confirm`、`builtin__choice` 不兼容旧 `confirm_action`、`ask_user_choice` pending 会话；测试夹具和断言不得继续把旧名称当作内置工具。
- 上下文压缩测试场景必须使用正式 Pydantic 消息类型并验证 serialize/deserialize 往返；不要直接
  插 JSON。场景随当前模型 trigger 动态扩展，断言不能写死消息条数或 Token 值。
- 附件集成测试必须验证 `file_id` 只来自当前原始会话、同页去重键包含
  `tool/file/sheet/offset/limit`，并至少用真实生成的 TXT、DOCX、XLSX 字节覆盖解析器。
- 配置备份按源 Alembic Revision 复制配置表，会话与渠道运行表只保留空结构；`006` 新增的
  `wecom_applications` 包含凭据及配置，`wecom_user_profiles` 属于可重建缓存，只保留空结构。
  分类以 `db/configuration_transfer.py` 的 `CONFIGURATION_TABLE_ORDER/SESSION_TABLES/CACHE_TABLES`
  为准，新增表必须显式归类，不能把未知表自动当配置导出。`test_config_backup.py` 验证新旧
  Revision、配置主键及 JSON 引用保留、缓存排除；旧源库不得被要求包含尚未迁移的新表。
- 配置初始化只允许备份/目标Revision完全一致且两侧会话为空；测试必须断言主键原样保留、失败事务
  回滚、Skill只改目标File ID，并确认数据库上传文件不进入平台File Store。

更多：[`README.md`](README.md)、[`unit/README.md`](unit/README.md)、[`integration/README.md`](integration/README.md)。

### 无外键真实事务验收

`integration/test_logical_references.py`、`test_channel_reference_transactions.py`、`test_reference_migration_databases.py`、`test_configuration_import_databases.py`提供隔离MySQL/PostgreSQL的opt-in回归。环境变量及防误用数据库名见各文件首部。未提供URL必须报告skip，不能当作真实数据库通过；SQLite无法替代记录锁验证。MySQL真实schema通过Alembic创建，不使用metadata.create_all替代部署结构。

集成配置回归：`unit/test_integration_configuration.py`覆盖空库目录、JSON值、首次保存/并发与清空，`integration/test_integration_runtime_api.py`覆盖登录/管理权限、未配置错误及弹窗与OpenAPI示例一致性。`004`只建表不种数据；迁移往返与配置备份/导入均使用隔离库。


### 企微应用与成员资料验收（006）

- `unit/test_wecom_store.py`：应用身份不可变、成员 User ID 去重与密文补全、缓存统计和失效、引用删除及迟到写入保护。
- `unit/test_wecom_migration.py`：隔离库 `005 → 006` 数据保留、受限降级及 DDL 方言编译。方言编译不等于真实 MySQL/PostgreSQL 事务验收。
- `unit/test_wecom_profiles.py`：Token 缓存／失效、默认关闭、密文明文兼容、负缓存、姓名失效和并发查询。
- `unit/test_wecom_identity_channel.py`、`integration/test_wecom_identity_events.py`：标题及私聊首轮／群聊每轮身份字段、关闭兼容和真实持久化事件；页面出现姓名不能代替模型上下文验证。
- `integration/test_wecom_applications_api.py`：管理员与内部鉴权、配置 CRUD、掩码、不变身份、缓存任务 API。
- `frontend/console/wecom-applications.test.mjs` 与 `channels.test.mjs`：默认关闭和 Settings 保留、应用编辑／缓存操作、迟到请求与结果待确认。

刷新及重置只针对已知用户，不触发全通讯录同步。测试必须区分 Token 获取、ID 转换和姓名读取三个环节；可信 IP／权限错误不可被当作明文 User ID 的证据。真实凭据不写夹具、日志或提交。

发布前非超管真实链路结果单独记录，待当前本地 8000 验收填入；超管真实机器人尚未提供，真实验收待补，不能用隔离分支测试冒充。真实机器人消息由用户触发，不主动向他人或群发送。浏览器只做必要桌面流程，不扩展移动端或 Agent 安装全组合矩阵。迁移和备份破坏性验证使用隔离库，既有本地 SQLite 和文件不清空、不另建 8002 验收环境。
