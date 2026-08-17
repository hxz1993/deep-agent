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

检测快照保存到 `mcp_servers.inspection`。除 Server、Resource、时间和耗时元数据外，v0.3.19
还保存完整工具名称、说明、输入/返回 Schema、Annotations、Task Support 与 Server Instructions。
快照不保存 Header、Token、Cookie 或 MCP `_meta`，MCP 列表 API 也不返回完整工具 Schema。

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

系统不做定时检测。URL MCP 的普通工具由检测快照负责模型披露和 Tool Search，聊天 Run 不再先
连接所有远程 MCP。只有模型真实调用某个工具时才按当前请求 Header initialize，并在该 Run 内复用
连接；Resource List/Read 仍在模型实际调用宿主代理工具时实时连接和读取。

旧记录只有数量、没有明细快照时，Agent 配置仍保留，但对应工具暂不进入模型列表，Console 会提示
“缺少工具快照，请检测并更新”。修改 URL、Header 或请求上下文会立即使快照失效；同一配置检测失败
则保留上次成功快照，并标记当前连接异常。

## URL MCP 重命名

数据库管理的 URL MCP 可通过 Console 独立“重命名”操作或以下接口修改名称：

```http
POST /api/v1/mcp/{old_name}/rename
Content-Type: application/json

{"new_name": "new_name"}
```

名称同时决定 Agent 工具包编码 `mcp:<name>`、后续模型工具前缀 `<name>_` 和 Resource
Server 名称。后端会在一个事务内更新 MCP 名称和所有 Agent 引用，失败时整体回滚。URL、
Header、启用状态及检测快照保持不变，因此无需重启或重新检测。Builtin 和历史 stdio MCP
不可重命名；历史 Tool Call/Result、Checkpoint 与正在执行的 Run 也不会被改写。

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
- 普通 MCP 初始化或连接失败会转换为脱敏 `ToolFailed`；模型可以改用其他工具或解释暂时不可用，
  不会让未使用该 MCP 的普通聊天也失败。Run 取消信号不转换为工具错误。
- MCP SDK或Pydantic AI可能把请求超时先包装成`ModelRetry`。DeepAgent只在异常链能确认请求超时、
  连接中断或5xx不可用时重新分类为`ToolFailed`；参数、对象选择和普通业务校验错误仍允许模型修正。
- 检测错误只保存阶段和异常类型，不保存 Token、Cookie、Header、URL 查询参数或服务返回的敏感正文。
- 文本 Resource 最大允许 `256 KiB`；超限时要求读取更窄的 URI。
- 二进制 Resource 只返回媒体类型和大小，不把原始字节放入模型上下文。

Builtin MCP 第一版不主动启动进程做检测，因此管理页显示“内置运行时”，也不会仅因存在本地 MCP 目录而获得 Resource 代理工具。内建 MCP 由程序管理：Console 不显示删除按钮，删除 API 返回 `403`；需要移除时应修改仓库内 MCP 实现及其程序配置后随版本发布。
