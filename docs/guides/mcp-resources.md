# MCP Resource 接入说明

DeepAgent 使用 Pydantic AI `MCPToolset` 的 Resource API，向模型提供受控的 MCP 静态资源读取能力。Resource 与 MCP Tool、UI 工具相互独立。

## 注册与探测

创建或修改 URL MCP 时，DeepAgent 使用本次配置请求的上下文 Header 连接 MCP，并调用：

```text
list_resources()
list_resource_templates()
```

探测结果保存到 `mcp_servers.resource_capabilities`：

| 状态 | 含义 |
| --- | --- |
| `supported` | 至少存在一个 Resource 或 Resource Template |
| `unsupported` | 连接成功，但两类列表均为空 |
| `unknown` | 尚未探测，或连接、鉴权、协议调用失败 |

探测失败不会阻止 MCP 配置保存。Console 可点击“检测资源”重新探测，也可调用：

```http
POST /api/v1/mcp/{name}/resources/probe
```

系统不做定时探测。MCP 后续新增 Resource 后，需要重新保存或手工检测。

## 模型工具

Agent 至少关联一个 `enabled=true`、探测状态为 `supported` 的 URL MCP 时，DeepAgent 才动态加入三个宿主代理工具：

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
- Resource 能力标记只表示 MCP 服务声明了资源，不代表每个登录用户都有读取权限。
- 运行时权限、网络或协议失败会作为工具错误反馈给模型。
- 探测错误只保存异常类型，不保存 Token、Cookie、Header 或服务返回的敏感正文。
- 文本 Resource 最大允许 `256 KiB`；超限时要求读取更窄的 URI。
- 二进制 Resource 只返回媒体类型和大小，不把原始字节放入模型上下文。

Builtin MCP 第一版不主动启动进程做 Resource 探测，因此不会仅因存在本地 MCP 目录而获得 Resource 代理工具。
