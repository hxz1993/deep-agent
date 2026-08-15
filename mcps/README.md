# 仓库内置 MCP

`mcps/` 保存随 DeepAgent 一起发布的 MCP Server。它们与进程内 `native`、`attachment`
和 `skill` 工具不同，运行时仍通过 MCP 协议接入。

## 当前内置服务

| 目录 | capability | 用途 |
| --- | --- | --- |
| `text_tools/` | `mcp:text_tools` | 文本统计和转换示例 |
| `deepcube/` | `mcp:deepcube` | DeepCube SDK/MCP 薄启动器 |
| `doc_search/` | `mcp:doc_search` | 文档搜索 |

运行时由 `deep_agent/capabilities/mcp/builtin.py` 发现仓库实现，并与数据库
`mcp_servers` 配置合并。外部 URL MCP 只通过数据库和 Console 配置，不再使用目录 JSON。

内置记录使用 `transport=builtin`，由程序管理：

- Console 不显示删除按钮。
- `DELETE /api/v1/mcp/{name}` 返回 `403`。
- 移除内置 MCP 必须同时删除实现和程序配置，并随正式版本发布。

## 新增内置服务

1. 创建 `mcps/<name>/server.py`。
2. 使用 `FastMCP` 注册短工具名。
3. 保持服务自包含，不导入 `deep_agent` 业务包。
4. 增加单元与集成测试，并通过 Agent capability 验证实际工具名。

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")


@mcp.tool()
def greet(name: str) -> str:
    """返回问候语。"""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run()
```

本地直接运行时，stdio 服务会等待 MCP 客户端连接：

```bash
python3.12 mcps/<name>/server.py
```

## DeepCube

`mcps/deepcube/server.py` 委托已安装的 `deepcube-mcp` 包。常用配置通过
`mcp_servers.config.env` 或部署环境注入：

| 变量 | 默认/说明 |
| --- | --- |
| `DEEPCUBE_MCP_TRANSPORT` | `stdio` |
| `DEEPCUBE_MCP_ENV_URL` | DeepFOS 服务基址 |
| `DEEPCUBE_MCP_HEADERS` | 静态 Header JSON |
| `DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY` | `4` |

凭据、Token 和真实 Header 不得写入仓库示例。详细维护边界见 [`AGENTS.md`](AGENTS.md)。
