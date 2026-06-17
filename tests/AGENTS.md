# AGENTS.md — 测试

> 父索引：[`../AGENTS.md`](../AGENTS.md)

自动化测试位于仓库根 `tests/`，使用 **`unittest`**（非 pytest 作为主入口）。在仓库根运行，需已安装 `requirements.txt` 依赖。

---

## 运行命令

```bash
# 全部
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v

# 仅单元（无网络、无外部子进程）
.venv\Scripts\python.exe -m unittest discover -s tests/unit -p "test_*.py" -v

# 仅集成（HTTP、MCP 子进程、header 转发）
.venv\Scripts\python.exe -m unittest discover -s tests/integration -p "test_*.py" -v
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
| `test_agent_max_requests.py` | per-agent / 全局 request 上限 |
| `test_builtin_mcp_config.py` | builtin MCP 配置规范化 |
| `test_database_url_resolution.py` | DSN 构建 |
| `test_dbms_config.py` | `DEEP_AGENT_DBMS_*` 解析 |
| `test_deepcube_server.py` | DeepCube 启动器 argv |
| `test_forward_headers.py` | MCP header 白名单 |
| `test_mcp_session_pool.py` | MCP 会话池 |

---

## 集成测试（`integration/`）

| 文件 | 覆盖 |
| --- | --- |
| `test_chat_header_forwarding.py` | HTTP → MCP header 透传 |
| `test_mcp_header_forwarding.py` | MCP 层 header 注入 |
| `test_mcp_session_scoping.py` | stdio MCP 按 session 隔离子进程 |

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

更多：[`README.md`](README.md)、[`unit/README.md`](unit/README.md)、[`integration/README.md`](integration/README.md)。
