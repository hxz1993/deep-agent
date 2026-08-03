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
| `test_global_settings.py` | 全局配置安全白名单、来源和无需额外身份依赖的只读接口 |
| `test_builtin_mcp_config.py` | builtin MCP 配置规范化 |
| `test_database_url_resolution.py` | DSN 构建 |
| `test_dbms_config.py` | `DEEP_AGENT_DBMS_*` 解析 |
| `test_deepcube_server.py` | DeepCube 启动器 argv |
| `test_forward_headers.py` | MCP header 白名单 |
| `test_request_context.py` | App 推导 Space、默认中文和上下文接口校验 |
| `test_mcp_lease_manager.py` | MCP lease 管理 |
| `test_title_generator.py` | AI 标题清洗与无配置行为 |
| `test_catalog.py` | models.dev Lab/Model/Provider 标准化、白名单、扩展元数据和数据库视图 |
| `test_models_dev.py` | models.dev 严格获取与 Lab 可选补充 |
| `test_llm_models.py` | 模型上下文、最大输出、输入模态与 tags Store/Schema |
| `test_context_compaction.py` | 统一 Token 估算、动态输出预测、usage 回读、主模型滚动摘要、SSE 状态、原始历史不改写、脱敏 Checkpoint、CAS 与兜底 |
| `test_context_test_scenarios.py` | 管理员测试会话场景、实时预算、消息往返序列化、常驻接口与用户归属 |
| `test_attachment_extractors.py` | 文本编码、DOCX/XLSX 结构读取、分页与 Office 安全 |
| `test_attachment_tools.py` | 附件工具默认参数、会话范围、缓存、分页和多模态返回 |
| `test_tool_package_registry.py` | ToolPackage Registry 的整包选择、默认和空列表语义 |
| `test_sqlite_bootstrap.py` | Alembic 001/002 结构、工具包数据迁移、降级展开与失败前校验 |
| `test_tool_package_migration_dialects.py` | SQLite、MySQL、PostgreSQL 的 002 DDL 分支 |
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
| `test_llm_model_catalog.py` | 目录 DB 查询、两种视图、全量刷新及失败保留旧目录 |
| `test_session_model_override.py` | Agent 流式对话 `model_id` 覆盖、默认回退、轮次切换 |
| `test_chat_attachment_tools.py` | 附件引用 → 模型调用工具 → SSE/历史持久化 → 同会话续读 |

公共 fixture：`integration/fixtures/header_echo_server.py`、`pid_echo_server.py`。

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
- 改内置 UI 工具时同时覆盖：`ui:builtin` 整包选择、`capabilities=null` 和空数组不加载、内置包/工具修改删除保护，以及 choice 的单选、多选、自由输入和空提交阻止。
- `builtin__confirm`、`builtin__choice` 不兼容旧 `confirm_action`、`ask_user_choice` pending 会话；测试夹具和断言不得继续把旧名称当作内置工具。
- 上下文压缩测试场景必须使用正式 Pydantic 消息类型并验证 serialize/deserialize 往返；不要直接
  插 JSON。场景随当前模型 trigger 动态扩展，断言不能写死消息条数或 Token 值。
- 附件集成测试必须验证 `file_id` 只来自当前原始会话、同页去重键包含
  `tool/file/sheet/offset/limit`，并至少用真实生成的 TXT、DOCX、XLSX 字节覆盖解析器。

更多：[`README.md`](README.md)、[`unit/README.md`](unit/README.md)、[`integration/README.md`](integration/README.md)。
