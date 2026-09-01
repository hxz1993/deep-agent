# PTC 程序编排设计

## 1. 目标与非目标

本能力解决的是工具之间的中间值编排：依赖链、并发、循环、过滤、聚合和文件载荷中转。它不替代
MCP 原生调用，也不替代完整 CPython 沙箱。

| 能力 | `run_code` | `execute_python` |
| --- | --- | --- |
| 运行时 | Harness CodeMode + Monty Python 子集 | 独立 CPython 子进程 |
| 状态 | 同一 Pydantic Run 内 REPL 可复用 | 每次调用全新进程 |
| 可调用工具 | 当前已授权、已披露 MCP + `load_file_data` | 不可调用 MCP |
| 文件/网络/Shell | 无宿主文件、任意Socket、环境变量、Shell、pip；文件只经`load_file_data`中转 | 受控FileStore输入、公开网络和预装包 |
| 适用 | MCP编排与文件原样中转 | 文件下载、解析、转换、生成与数据计算 |

本期不实现 Shell、Playwright、Host FileSystem、Monty Mount、通用 Artifact CRUD、
Tool Approval 或外部工作流事实表。

## 2. 装配

```text
Agent.capabilities 包含 builtin:ptc
  → 注册 code-only load_file_data Toolset
  → 已授权 MCP Toolset 外包 FilePayloadResolvingToolset
  → HybridCodeMode(outermost) 包装 ToolSearch
  → 模型同时看到普通 MCP Schema 与 run_code
```

未关联该包时不注入`run_code`，也不包装MCP。旧`builtin:code-mode`只在读取、运行和配置导入时
映射为`builtin:ptc`；新保存和导出统一写新编码。关联PTC不会自动关联任何MCP；权限仍只来自
`mcp:<name>`。MCP Resource、Planning、History、文件读取、Sandbox、UI/HITL/Deferred、Approval、
导出、Web工具与Capability/Search控制工具均不会成为Monty函数。

`HybridCodeModeToolset`复用官方执行器、类型检查、调用预算和资源限制，只调整：

1. 只选择已授权MCP与code-only helper；不依赖Web工具metadata。
2. 将已披露 MCP 重新加入普通原生工具目录。
3. 动态目录只列函数名和别名，不复制外层 JSON Schema。
4. 将官方完整嵌套调用 metadata 投影为有界、脱敏摘要。

目录中的 Python 名称只是 `run_code` 内 SDK，不是外层直接工具；只有普通工具列表中另有完整
Schema 的协议名才可直接调用。Code-only `load_file_data` 的手写目录签名必须由测试与真实函数
默认值保持一致，示例只使用签名实际接受的参数。

第三方 MCP 的 Output Schema 不自动成为 Monty 类型事实。只有已有证据白名单中的稳定 Schema
生成结构化返回类型；其他返回统一退化为通用 JSON/Any，不根据工具名猜测字段。普通原生 MCP
Schema 与返回内容不因此改写。

## 3. ToolSearch 与名称

ToolSearch 未披露的工具不会进入任一模型可调用路径。工具被搜索揭示后的下一模型步骤，同时获得
普通 Tool Schema 和对应 Python 函数。`dynamic_catalog=true` 将变化目录放到动态 Instructions，
不改变 `run_code` 的缓存前缀。

协议名规范化为 Python 标识符，例如 `wecom-doc_media_upload` 变为
`wecom_doc_media_upload`。非字母数字字符替换为下划线，关键字和数字开头安全改名。两个协议名若
规范化后碰撞，则二者都保留原生调用但不进入PTC，并记录不含参数的告警。

## 4. 文件变量

```python
load_file_data(
    file_id: str | None = None,
    url: str | None = None,
    encoding: Literal["base64", "text"] = "base64",
    text_encoding: str = "utf-8",
    max_chars: int = 65536,
) -> FileData
```

`file_id`与`url`必须且只能提供一个。File ID授权来源限当前会话附件、大工具结果与Python生成文件，
复用FileStore、User/App/Space、原始Event授权、Run缓存与Hash校验。URL只抓取公开HTTP(S)文件，
复用安全下载、重试和代理兼容，阻断凭据URL、私网、回环、链路本地、云元数据与重定向绕过。
两种来源都拒绝超过20 MiB；URL字节不写FileStore。

`base64` 模式返回 `deepagent-file-data://<run-token>`，真实字节只在 `AgentDeps` 的 Run 内存中保留。
MCP Wrapper 递归扫描参数，仅在字符串完整等于有效 Token 时替换；局部嵌入不替换，伪造、过期或
跨 Run Token 失败关闭。`text` 模式严格解码，默认 64K、最多 256K 字符，超限不截断。

## 5. 审计与协议

外层仍只有一个普通 `run_code` Tool Call/Result；嵌套 MCP 不增加聊天 Item、Event Type、SSE 字段
或数据库表。DeepCube、DeepTable 和 Console 均按现有普通工具协议展示。

轨迹 metadata 只保留嵌套顺序、工具名、Call ID、状态、耗时、安全小参数和受限结果摘要。投影必须
移除文件 Token、Base64、bytes、大字符串、敏感键、完整嵌套返回、Header、Cookie 和凭据。外层大
结果继续由 `ToolOutputLimits + Spill` 处理；嵌套审计本身还有固定总字符上限，超过后只记录剩余
调用数量，不拆成新的会话 Event。

Console仍把它作为普通工具，但轨迹使用独立`PTC`标签，并提供只读、可复制、Python高亮的
Code 页签。该展示直接读取既有 Tool Call 参数，不增加客户端协议字段。

## 6. 资源与失败

- 每次 `run_code` 最多 100 次嵌套工具调用。
- Monty Session 最长 30 秒、最大 256 MiB；同一 Run 可用 `restart=true` 重置状态。
- 不配置OS Access、Mount、任意Socket、Shell或第三方包；公开URL只经受控`load_file_data`下载。
- 正式取消向 Monty 和正在执行的 MCP 传播。
- 参数、语法、类型和运行错误沿用 Harness ModelRetry；不自动重跑整个代码片段。
- 副作用工具默认顺序执行；不确定网络失败后先读取目标状态，不盲目重放。
- Deferred/Approval不在PTC内往返，继续使用外层原生工具协议。
