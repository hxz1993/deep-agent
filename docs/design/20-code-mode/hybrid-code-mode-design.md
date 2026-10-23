# PTC 程序编排设计

> 2026-10-24：本页描述v0.14.7实现行为。
> 最终范围与验收门禁见[PTC工具扩展与数据交接](ptc-data-transfer-design.md)，不以本文代替执行证据。

## 1. 目标与边界

PTC负责依赖调用、并发只读查询、循环、过滤、聚合和文件原样中转。模型需要理解、核对或选择
中间结果时仍使用原生工具，不强制进入代码。Monty不替代CPython沙箱。

| 内容 | `run_code` | `execute_python` |
| --- | --- | --- |
| 运行时 | Harness CodeMode + 官方AsyncMonty | 独立CPython子进程 |
| 状态 | 同一Run的REPL Session可复用 | 每次调用全新进程与临时目录 |
| 工具 | 已披露MCP、受信builtin白名单、code-only文件原语 | 不直接调用宿主注册的MCP或工具 |
| 文件/网络 | 无宿主目录、Socket、环境变量、Shell、pip；通过受控宿主工具交接 | 原有授权文件输入、公开网络与预装包 |
| 用途 | 编排和变量处理 | 自定义Python计算、文件解析、转换与生成 |

不增加Shell、Playwright、Host FileSystem、Monty Mount、通用Artifact CRUD、`save_file_data`、
共享工作区、历史调用ID回查或外部工作流事实表。Skill脚本参数保持原状。

## 2. 装配与授权

```text
Agent显式关联builtin:ptc
  → 注册code-only load_file_data
  → 授权MCP包经FilePayloadResolvingToolset标记Hybrid
  → 受信builtin装配位置经BuiltinPtcToolset精确标记
  → HybridCodeMode包装当前可用工具
  → 普通Schema与run_code双入口
```

当前实现的builtin白名单为`execute_python`、`execute_skill_script`、
`web_search`、`web_fetch`和`export_file_to_download_center`。只标记当前已装配工具；关联PTC
不会自动选择Sandbox、Web、MCP或Skill。原工具的prepare、参数校验、身份检查和分发继续执行。

`deepagent_ptc_builtin`只由宿主在准确装配入口设置；MCP Wrapper明确剥离伪造标记。不得在最终
合并目录按名称批量扩权。`read_file`、`parse_document`、`web_image_view`、UI/Deferred、
发现、Planning、History和MCP Resource不进入PTC；其原生路径保持。

目录只列当前函数和安全别名，不复制外层JSON Schema。已披露MCP与白名单builtin均保留原生入口；
`load_file_data`始终code-only。新增builtin仅在PTC函数副本中清空复杂返回类型推断，作为通用JSON/Any交接；原生返回Schema保留；MCP只有
既有证据白名单中的稳定返回Schema参与类型生成，不根据工具名猜字段。

## 3. 发现与名称

需要渐进披露的MCP先通过普通ToolSearch发现；未披露工具不会进入PTC。发现后的下一模型步骤
同时得到原生Schema和Python函数。`dynamic_catalog=true`更新动态Instructions，发现通知仅报告
新增名称，不重复加入完整策略，也不重写历史通知。

协议名规范化为Python标识符，例如`wecom-doc_media_upload`变为`wecom_doc_media_upload`。
别名碰撞的工具保留原生调用但不进入PTC；code-only原语不得因此退回外层。

## 4. 数据与文件

普通调用保持`ToolOutputLimits + Spill`；必要时外置文件并续读。PTC内部所有开放函数统一绕过
给模型使用的Token外置、截断和分页包装，返回值通过32MiB硬限检查后完整交给程序。内部身份由
本次执行对象已登记的父子记录判断，不依靠Call ID前缀，状态按任务隔离。外层`run_code`返回继续保护。
数据源分页、Web供应商与下载安全限制、工具自身预览/截断行为不变；不自动翻页或把预览当全量。

变量直接传入现有`execute_python(code=..., inputs=...)`或`execute_skill_script(..., inputs=...)`。
Python代码可直接作为字符串，不要求先生成`.py`。Skill仍只执行已选择且已索引脚本和同Skill资源，
不增加会话文件输入或发布参数。Python继续通过`files`引用授权文件，使用`input_path`读取、
`publish_path`登记产物；成功后由宿主上传并返回File ID。已有文件以`platform_file`导出，不重复上传。

```python
load_file_data(
    file_id: str | None = None,
    url: str | None = None,
    encoding: Literal["base64", "text"] = "base64",
    text_encoding: str = "utf-8",
    max_chars: int = 65536,
) -> FileData
```

`file_id`和`url`必须且只能提供一个。授权来源为当前会话附件和真实工具结果中的文件引用。
公开URL复用安全下载，拒绝凭据URL、私网、回环、云元数据和重定向绕过，两种来源均限20MiB；
URL字节不自动写FileStore。Base64模式在Monty里持有Run内不透明Token，真实字节只在MCP边界展开。
局部嵌入、伪造、过期或跨Run Token失败关闭。文本模式严格解码，默认64K、最多256K字符，超限不截断。

## 5. 事实与协议

外层仍是一个`run_code`调用/返回，不新增嵌套History类型、SSE字段或表。只保留有界、脱敏的
`ptc_diagnostics`：单步输入/输出各8KiB、字符串2KiB、总JSON128KiB。完整内部值不复制进历史或下一轮模型。

可信Python/导出成功后，真实文件引用汇总到外层现有`generated_files` metadata；仅收集宿主已授权
或已登记的文件，不信任MCP同名字段。后续失败/取消保留已完成成果，沿既有失败回执和取消修复
路径保存；下一Run按原始历史metadata恢复文件权限。不把文件正文、临时路径或完整子结果塞进metadata。

现有Console的Calls、Input/Result/Code/Error展示不变；原始代码来自外层Tool Call参数。
HTTP、SSE、Live、History、安装导出格式不变，不重写旧事件、计划和业务上下文。

## 6. 资源与失败

当前实现下列固定限制，不增加API、环境或Console配置项：

| 项目 | 限制 |
| --- | --- |
| Monty内存 | 256MiB |
| 单个内部返回 | 32MiB，按交给Monty的实际值计量 |
| Session执行预算 | 120秒，同一Run REPL Session共享 |
| 每段总截止 | 120秒，包含外部工具等待 |
| 每段内部调用 | 100次，失败也计数 |
| Python/Skill inputs | 16MiB紧凑UTF-8 JSON，原生入口同样适用 |
| Python通信帧 | 64MiB，整包上限；不放大结果和文件限制 |

复用官方`AsyncMontySession.feed_run`与SDK ToolManager、类型签名、校验、调用预算、返回转换及执行关联。
异步桥使等待后CPU执行也受总截止约束；超时/取消停止worker并取消、等待所有已启动宿主回调，
然后清理Session/Pool。正常同Run复用REPL；`restart=true`清变量，不回滚任何已完成副作用。

超大内部返回明确失败，不伪装截断成功，也不自动重跑整段。256MiB不是整个宿主进程树的内存保证，
32MiB返回不保证每一种对象都可在Monty内承载。Python原超时、内存、64KiB代码、512KiB正式结果、
日志及文件限制不变。副作用顺序执行，未知结果先查实；UI/Deferred和Approval继续外层原生协议。
