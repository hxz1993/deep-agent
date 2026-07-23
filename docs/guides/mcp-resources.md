# MCP Resource 接入说明

DeepAgent 使用 Pydantic AI `MCPToolset` 的 Resource API，向模型提供受控的 MCP 静态资源读取能力。Resource 与 MCP Tool、UI 工具相互独立。

## 检测并更新

创建或修改启用状态的 URL MCP 时，DeepAgent 使用本次配置请求的上下文 Header 连接 MCP，并依次执行：

```text
initialize
ping
tools/list
resources/list
resources/templates/list
```

检测快照保存到 `mcp_servers.inspection`，包含 Server 名称、版本、协议版本、Instructions 可用性、工具数量、Resource 数量、Resource Template 数量、检测时间和耗时。

| 状态 | 含义 |
| --- | --- |
| `healthy` | 初始化、Ping 和能力清单均正常 |
| `degraded` | 连接成功，但某一项能力清单获取失败 |
| `unreachable` | 连接、鉴权、初始化或 Ping 失败 |
| `unknown` | 尚未检测 |
| `stale` | 连接配置已修改，等待重新检测 |
| `not_applicable` | Builtin MCP，不参与主动检测 |

检测失败不会阻止 MCP 配置保存。Console 可点击“检测并更新”，也可调用：

```http
POST /api/v1/mcp/{name}/inspect
```

系统不做定时检测。检测快照只服务于管理页面和 Resource 代理工具开放判断；普通 MCP 工具仍在每次 Agent Run 中由 Pydantic AI 实时获取，Resource 正文也始终实时读取。

## 模型工具

Agent 至少关联一个 `enabled=true`、整体检测状态为 `healthy/degraded` 且 Resource 状态为 `supported` 的 URL MCP 时，DeepAgent 才动态加入三个宿主代理工具：

```text
list_mcp_resources(server_name)
list_mcp_resource_templates(server_name)
read_mcp_resource(server_name, uri)
```

没有符合条件的 MCP 时，这三个工具不会进入模型工具列表。`server_name` 只能使用当前 Agent 已关联的 MCP，不能越过 Agent 能力配置读取其他服务。

## 请求上下文与安全

- MCP 的服务级静态 Header 继续来自 MCP 配置。
- `inherit` 模式按现有白名单合并当前请求的 App、Space、User、Language 和鉴权上下文。
- `isolated` 模式只使用 MCP 静态 Header。
- 检测快照只表示上次检测时的服务能力，不代表持续在线，也不代表每个登录用户都有读取权限。
- 运行时权限、网络或协议失败会作为工具错误反馈给模型。
- 检测错误只保存阶段和异常类型，不保存 Token、Cookie、Header、URL 查询参数或服务返回的敏感正文。
- 文本 Resource 最大允许 `256 KiB`；超限时要求读取更窄的 URI。
- 二进制 Resource 只返回媒体类型和大小，不把原始字节放入模型上下文。

Builtin MCP 第一版不主动启动进程做检测，因此管理页显示“内置运行时”，也不会仅因存在本地 MCP 目录而获得 Resource 代理工具。
