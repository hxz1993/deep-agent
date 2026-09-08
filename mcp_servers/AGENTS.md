# AGENTS.md — 内置 MCP Servers

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端加载逻辑：[`../src/deep_agent/AGENTS.md`](../src/deep_agent/AGENTS.md)

`mcp_servers/` 存放**随仓库发布的 stdio MCP server**。每个子目录是独立子进程，与 `deep_agent` 进程隔离。

---

## 发现与注册

```
mcp_servers/<name>/server.py  →  capability mcp:<name>  →  工具前缀 <name>_
```

1. 每次构建 Agent / 列举工具时，`resolve_mcp_toolsets`（`src/deep_agent/capabilities/mcp/resolver.py`）从 DB 读取 enabled 行
2. `load_builtin_mcp_capabilities`（`builtin.py`）扫描本目录作兜底：**DB 无同名 enabled 记录**时启用 builtin
3. 连接懒加载：`async with agent` 时才拉起子进程
4. `MCP_STDIO_SESSION_SCOPE=true`（默认）时，stdio 子进程按 chat session 保活
5. MCP CRUD API 更新数据库，后续请求按最新配置解析

未显式配置 DBMS 时应用使用默认文件 SQLite，不存在“无数据库”的正常启动模式。builtin
实现是程序兜底，是否进入模型工具列表仍取决于 Agent 是否选择对应 `mcp:<name>`。

---

## 新增 builtin server

1. 创建 `mcp_servers/<name>/server.py`
2. 使用 `FastMCP`，`if __name__ == "__main__": mcp.run()`
3. **禁止** `import deep_agent` 或依赖后端包（须能独立 `PYTHONPATH=src:. python3.12 mcp_servers/<name>/server.py` 运行）
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
PYTHONPATH=src:. python3.12 mcp_servers/<name>/server.py
```

命令在仓库根执行；按文件启动时同时提供 `src` 与仓库根，使平台支撑包和 `mcp_servers._run`
都可导入。Windows PowerShell 对应设置 `$env:PYTHONPATH = 'src;.'`。

---

## 现有 server

| 目录 | capability | 说明 |
| --- | --- | --- |
| `deepcube/` | `mcp:deepcube` | 启动已安装的 `deepcube-mcp` 包 |

---

## DeepCube

薄启动器，委托 `deepcube.mcp.server:main`。**所有运行时参数由 `deepcube-mcp` 从 `DEEPCUBE_MCP_*` 环境变量读取**（见包内 `deepcube.mcp.config`）；launcher 仅设置 stdio 默认值，并在未配置时把 `deepfos_platform` 的 `EXPORT_SERVICE_DISCOVERY_*` / `EXPORT_EUREKA_*` / `EXPORT_NACOS_*` / `EXPORT_K8S_DISCOVERY_NAMESPACE` 桥接为 `DEEPCUBE_MCP_*`。

DeepCube由发布流水线先注入内部Wheel，公开PyPI不提供该包，因此`requirements.txt`不锁定公开版本号。
DeepAgent在外层统一增加`deepcube_`协议前缀，包内原始工具必须使用
`list_cubes`、`resolve_members`等短名，不能再次携带`deepcube_`。模型最终看到的稳定名称仍为
`deepcube_list_cubes`、`deepcube_resolve_members`等。

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
| `DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY` | Cube 初始化维度最大并发数；deep-agent 默认 `4` |

未单独提供 env 的 discovery 子项（如 `nacos.cluster`、`take_over`）请写入 `DEEPCUBE_MCP_DISCOVERY` JSON，或依赖上述 `EXPORT_*` 桥接。

DB 配置示例（`mcp_servers.config.env`）：

```json
{
  "DEEPCUBE_MCP_TRANSPORT": "stdio",
  "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
  "DEEPCUBE_MCP_HEADERS": "{\"app\":\"demo\",\"space\":\"demo\",\"token\":\"...\"}",
  "DEEPCUBE_MCP_DISCOVERY": "{\"enabled\":true,\"implementation\":\"eureka\",\"take_over\":true,\"eureka\":\"http://eureka:8761/eureka\"}",
  "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY": "4"
}
```

当前种子已并入生产基线 `migrations/versions/001_initial_schema.py`；旧 `004_seed_deepcube_mcp.py`
仅属历史迁移，不是当前可执行入口。

---

## 生产配置（DB 优先）

通过 `POST /api/v1/mcp` 或 `/console/#/configuration` 注册，**同名时 DB 覆盖 builtin 发现**。`transport=builtin` 的记录由程序管理，Console 不提供删除入口，`DELETE /api/v1/mcp/{name}` 也会拒绝；移除内建 MCP 必须随代码和程序配置一起发布。

| transport | 用途 |
| --- | --- |
| `builtin` | 指向 `mcp_servers/<name>/server.py` |
| `stdio` | 历史配置兼容；Console 不再允许新建或编辑 |
| `url` | 远程 SSE/HTTP MCP |

`config` 可含 `command`、`args`、`env`、`cwd`、`url`、`headers`。

HTTP 请求 header（`authorization` 等）经 `DEEP_AGENT_MCP_FORWARD_REQUEST_HEADERS` 白名单转发到 url 型 MCP。

---

## 陷阱

| 现象 | 检查 |
| --- | --- |
| 工具列表空 | `mcp_servers.enabled` 与 Agent capability 是否包含对应 `mcp:<name>` |
| 子进程启动失败 | 独立运行 `server.py`；Python 环境是否含依赖 |
| DeepCube 无 metadata | `DEEPCUBE_MCP_HEADERS` 是否配置 |
| 改了 server.py 不生效 | DB 有同名记录时用 DB config，非 builtin 发现 |

更多示例：[`README.md`](README.md)。
