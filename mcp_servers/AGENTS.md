# AGENTS.md — 内置 MCP Servers

> 父索引：[`../AGENTS.md`](../AGENTS.md) · 后端加载逻辑：[`../src/deep_agent/AGENTS.md`](../src/deep_agent/AGENTS.md)

`mcp_servers/` 存放**随仓库发布的 MCP Server 启动器**。每个子目录是独立子进程，与
`deep_agent` 的 Agent 执行进程隔离；生产 builtin 使用 Pod 内共享的 streamable-http 进程，
直接启动仍可使用 stdio。

---

## 发现与注册

```
mcp_servers/<name>/server.py  →  capability mcp:<name>  →  工具前缀 <name>_
```

1. `resolve_mcp_toolsets`（`src/deep_agent/capabilities/mcp/resolver.py`）从 DB 读取 MCP 配置；没有资源级 enabled 字段
2. `load_builtin_mcp_tool_packages`（`builtin.py`）扫描本目录作兜底：**DB 无同名记录**时补充 builtin 工具包
3. 只有 Agent 显式关联的 `mcp:<name>` 才参与 Run，目录扫描本身不启动进程
4. builtin 在首次真实调用时取得 Run 租约并按需启动或复用 Pod 共享 HTTP 进程；TTL/租约由 `shared_registry.py`、`lease_manager.py` 管理
5. URL MCP 使用检测时保存的工具快照，真实调用才连接；MCP CRUD 修改影响后续请求，不改已构建 Run

未显式配置 DBMS 时应用使用默认文件 SQLite，不存在“无数据库”的正常启动模式。builtin
实现是程序兜底，是否进入模型工具列表仍取决于 Agent 是否选择对应 `mcp:<name>`。

---

## 新增 builtin server

1. 创建 `mcp_servers/<name>/server.py`
2. 使用 `FastMCP`，`if __name__ == "__main__": mcp.run()`
3. 不依赖 Agent/API/数据库业务运行时；可复用 `_run.py` 与轻量的 `deep_agent.core.mcp_constants`。须能独立 `PYTHONPATH=src:. python3.12 mcp_servers/<name>/server.py` 运行
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

未显式设置 `DEEPCUBE_MCP_DISCOVERY` 且启用 Nacos 时，launcher 将全部十个 Nacos 字段写入 discovery JSON 的 `nacos` 对象：`server`、`cluster`、`namespace`、`group`、`user`、`password`、`access_key`、`secret_key`、`ram_role_name`、`auth_mode`。未设置的值省略；密码和 AK/SK 原样传递，其他字段沿用空值清理规则。launcher 只负责发送配置，不直接修改 SDK OPTION、不处理 SDK 字段兼容性，也不输出凭据日志。新鉴权字段的接收、SDK 配置应用和每请求上下文重建由 DeepCube 负责，需要使用支持这些字段的接收端和 DeepFOS SDK。

启用 DNS 时，launcher 调用平台 `get_dns_records()`，将已有 `EXPORT_DNS_*` 的全部 50 个服务地址写入 discovery JSON 的 `dns.records`，服务名和 URL 原样传递；沿用平台缺少或空地址即报错的语义。DeepCube 接收 `implementation="dns"`，在启动及每请求上下文中先通过 `safe_set` 设置 `dns.records`，再选择后端和启用发现。实际 DNS 发现仍要求 SDK 支持相应选项。

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
| `DEEPCUBE_MCP_DISCOVERY` | JSON，含 `enabled` / `implementation` / `take_over` / `eureka` / `nacos` / `k8s` / `dns` |
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
| 工具列表空 | Agent 是否关联 `mcp:<name>`；URL MCP 是否已有有效检测快照 |
| 子进程启动失败 | 独立运行 `server.py`；Python 环境是否含依赖 |
| DeepCube 无 metadata | `DEEPCUBE_MCP_HEADERS` 是否配置 |
| 改了 server.py 不生效 | DB 有同名记录时用 DB config，非 builtin 发现 |

更多示例：[`README.md`](README.md)。
