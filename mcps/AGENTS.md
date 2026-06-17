# AGENTS.md — 内置 MCP Servers

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端加载逻辑：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

`mcps/` 存放**随仓库发布的 stdio MCP server**。每个子目录是独立子进程，与 `deep_agent` 进程隔离。

---

## 发现与注册

```
mcps/<name>/server.py  →  capability mcp:<name>  →  工具前缀 <name>_
```

1. 启动时 `load_builtin_mcp_capabilities`（`deep_agent/capabilities/mcp/builtin.py`）扫描本目录
2. 有数据库时：`load_and_register_mcp` 以 DB `mcp_servers` 为主；**DB 无同名 enabled 记录**时 builtin 兜底
3. 连接懒加载：`async with agent` 时才拉起子进程
4. `MCP_STDIO_SESSION_SCOPE=true`（默认）时，stdio 子进程按 chat session 保活

无数据库 → MCP 整段不加载（builtin 也不注册）。

---

## 新增 builtin server

1. 创建 `mcps/<name>/server.py`
2. 使用 `FastMCP`，`if __name__ == "__main__": mcp.run()`
3. **禁止** `import deep_agent` 或依赖后端包（须能独立 `python mcps/<name>/server.py` 运行）
4. 工具名在 MCP 内用短名即可，运行时自动加 `<name>_` 前缀

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
```

本地验证：

```bash
python mcps/<name>/server.py
```

---

## 现有 server

| 目录 | capability | 说明 |
| --- | --- | --- |
| `text_tools/` | `mcp:text_tools` | 示例：`word_count`、`char_count`、`slugify`、`reverse` |
| `deepcube/` | `mcp:deepcube` | 启动已安装的 `deepcube-mcp` 包 |
| `doc_search/` | `mcp:doc_search` | 文档搜索 |

---

## DeepCube（`deepcube/`）

薄启动器，委托 `deepcube.mcp.server:main`。stdio 模式**不**读 per-request `_meta.headers`，须在环境变量或 DB `config.env` 配置静态 headers。

| 变量 | 默认 |
| --- | --- |
| `DEEPCUBE_MCP_TRANSPORT` | `stdio` |
| `DEEPCUBE_MCP_ENV_URL` | `https://test.deepfos.com/deepfos-server/` |
| `DEEPCUBE_MCP_HEADERS` | JSON，如 `{"app":"demo","space":"demo","token":"..."}` |

DB 配置示例（`mcp_servers.config.env`）：

```json
{
  "DEEPCUBE_MCP_TRANSPORT": "stdio",
  "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
  "DEEPCUBE_MCP_HEADERS": "{\"app\":\"demo\",\"space\":\"demo\",\"token\":\"...\"}"
}
```

种子迁移：`alembic/versions/004_seed_deepcube_mcp.py`。

---

## 生产配置（DB 优先）

通过 `POST /api/v1/mcp` 或 `/admin` 注册，**同名时 DB 覆盖 builtin 发现**。

| transport | 用途 |
| --- | --- |
| `builtin` | 指向 `mcps/<name>/server.py` |
| `stdio` | 任意命令行 MCP |
| `url` | 远程 SSE/HTTP MCP |

`config` 可含 `command`、`args`、`env`、`cwd`、`url`、`headers`。

HTTP 请求 header（`authorization` 等）经 `DEEP_AGENT_MCP_FORWARD_REQUEST_HEADERS` 白名单转发到 url 型 MCP。

---

## 陷阱

| 现象 | 检查 |
| --- | --- |
| 工具列表空 | DB 是否配置；`mcp_servers.enabled` |
| 子进程启动失败 | 独立运行 `server.py`；Python 环境是否含依赖 |
| DeepCube 无 metadata | `DEEPCUBE_MCP_HEADERS` 是否配置 |
| 改了 server.py 不生效 | DB 有同名记录时用 DB config，非 builtin 发现 |

更多示例：[`README.md`](README.md)。
