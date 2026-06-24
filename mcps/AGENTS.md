# AGENTS.md — 内置 MCP Servers

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端加载逻辑：[`../deep_agent/AGENTS.md`](../deep_agent/AGENTS.md)

`mcps/` 存放**随仓库发布的 stdio MCP server**。每个子目录是独立子进程，与 `deep_agent` 进程隔离。

---

## 发现与注册

```
mcps/<name>/server.py  →  capability mcp:<name>  →  工具前缀 <name>_
```

1. 每次构建 Agent / 列举工具时，`resolve_mcp_toolsets`（`deep_agent/capabilities/mcp/resolver.py`）从 DB 读取 enabled 行
2. `load_builtin_mcp_capabilities`（`builtin.py`）扫描本目录作兜底：**DB 无同名 enabled 记录**时启用 builtin
3. 连接懒加载：`async with agent` 时才拉起子进程
4. `MCP_STDIO_SESSION_SCOPE=true`（默认）时，stdio 子进程按 chat session 保活
5. MCP CRUD API 只写库，不做 registry 热重载（多实例下无意义）

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

薄启动器，委托 `deepcube.mcp.server:main`。**所有运行时参数由 `deepcube-mcp` 从 `DEEPCUBE_MCP_*` 环境变量读取**（见包内 `deepcube.mcp.config`）；launcher 仅设置 stdio 默认值，并在未配置时把 `deepfos_platform` 的 `EXPORT_SERVICE_DISCOVERY_*` / `EXPORT_EUREKA_*` / `EXPORT_NACOS_*` / `EXPORT_K8S_DISCOVERY_NAMESPACE` 桥接为 `DEEPCUBE_MCP_*`。

stdio 模式**不**读 per-request `_meta.headers`，须在环境变量或 DB `config.env` 配置静态 headers。

| 变量 | 默认 / 说明 |
| --- | --- |
| `DEEPCUBE_MCP_TRANSPORT` | launcher 默认 `stdio`（包内默认 `http`） |
| `DEEPCUBE_MCP_ENV_URL` | `https://test.deepfos.com/deepfos-server/` |
| `DEEPCUBE_MCP_HEADERS` | JSON，如 `{"app":"demo","space":"demo","token":"..."}` |
| `DEEPCUBE_MCP_DISCOVERY` | JSON，含 `enabled` / `implementation` / `take_over` / `eureka` / `nacos` / `k8s` |
| `DEEPCUBE_MCP_DISCOVERY_ENABLED` | `true` / `false` |
| `DEEPCUBE_MCP_EUREKA` | Eureka 地址 |
| `DEEPCUBE_MCP_CACHE_*` | 元数据缓存 TTL / 开关 / 上限 |

未单独提供 env 的 discovery 子项（如 `nacos.cluster`、`take_over`）请写入 `DEEPCUBE_MCP_DISCOVERY` JSON，或依赖上述 `EXPORT_*` 桥接。

DB 配置示例（`mcp_servers.config.env`）：

```json
{
  "DEEPCUBE_MCP_TRANSPORT": "stdio",
  "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
  "DEEPCUBE_MCP_HEADERS": "{\"app\":\"demo\",\"space\":\"demo\",\"token\":\"...\"}",
  "DEEPCUBE_MCP_DISCOVERY": "{\"enabled\":true,\"implementation\":\"eureka\",\"take_over\":true,\"eureka\":\"http://eureka:8761/eureka\"}"
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
