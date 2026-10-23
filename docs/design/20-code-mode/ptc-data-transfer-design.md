# PTC 工具扩展与数据交接

> 2026-10-24；v0.14.7 实现，验收结果另列测试报告。依赖不变，数据库保持 013。

## 1. 工具和权限

| 工具 | 普通入口 | PTC |
| --- | --- | --- |
| 已授权、已披露 MCP | 保留 | 保留 |
| web_search / web_fetch | 保留 | 新增 |
| execute_python | 保留 | 新增，现有 code / inputs / files |
| execute_skill_script | 保留 | 新增，原参数不变 |
| export_file_to_download_center | 保留 | 新增，内容或授权 File ID |
| load_file_data | 无 | 保留，代码内原语 |

只纳入当前 Agent 已装配、当前身份可用的工具。MCP 仍先通过普通搜索披露参数；需要模型理解或选择的中间结果继续普通调用。Builtin 标记只在宿主准确装配入口添加，MCP 不能伪造；普通参数校验、prepare 和权限检查共用。

不增加 save_file_data、read_file、parse_document、web_image_view、UI/Deferred、发现、计划、历史、任意 Shell 或宿主目录访问。Skill 不增加会话文件输入或发布参数。旧草案的变量保存工具及额外辅助函数已撤回；不合并结果引用或沙盒分支。

## 2. 两条结果链路

普通：模型 → 原工具 → 现有 Token 大结果保护 → 模型。必要时外置为授权文件，后续分页读取或交 Python 的 files；大结果不一定报错。

PTC：run_code → 原工具 → 返回硬限检查 → 完整变量 → Python/Skill → 最终摘要和文件引用。内部所有开放函数统一绕过给模型使用的外置、截断和分页包装，普通调用及外层 run_code 返回保持保护。由当前执行对象中的真实父子记录识别内部调用，不靠 ID 前缀；状态按任务隔离，退出释放。

数据源正式分页、Web 供应商/下载安全上限、原工具输入输出限制不变。不自动翻页；已有 truncated / is_preview 不能当完整数据。

## 3. Python、Skill 与文件

JSON 变量直接交现有 inputs；自定义 Python 代码直接作为字符串传入，不要求生成 .py。Skill 使用已选择、已索引的脚本及同 Skill 资源。两者仍每次独立进程和临时目录，不共享 Python 变量或目录。

```text
MCP 完整明细 → execute_python(inputs=完整数据)校验聚合
  → execute_skill_script(inputs=计算结果)渲染 HTML
  → export_file_to_download_center(source.type=content)
  → 真实发布回执与摘要
```

Python 仍可读取授权文件，通过 deepagent_files.publish_path 登记成果，成功后宿主上传并返回 File ID。已有文件以 platform_file 发布，不重复上传。中间变量不自动存平台；未来工作区交接另见 [07课题](../07-python-sandbox/session-sandbox-design.md)。

## 4. 固定资源限制

MiB 即 1024×1024 字节，不是 Token。不新增部署、API 或 Console 配置项。

| 项目 | 限制 |
| --- | --- |
| Monty 内存 | 256 MiB |
| 单个内部返回 | 32 MiB |
| Python/Skill inputs 紧凑 UTF-8 JSON | 16 MiB |
| Monty 执行预算 | 120 秒，同一 Run 的 REPL Session 共享 |
| 每段 run_code 总时间 | 120 秒，包含外部工具等待 |
| 每段内部调用 | 100 次，失败同样计入 |
| Python 进程通信帧 | 64 MiB，含代码、输入与编码文件的整包 |

返回按 Monty 实际接收的值计量，ToolReturn 取 return_value，不把 metadata 或审计摘要当正式返回。容器有界遍历、字符串分块计 UTF-8 和转义，保持 SDK 二进制、日期等转换语义。超限明确失败，不把预览伪装全量；32 MiB 不是宿主进程内存保证，也不保证该尺寸的所有对象都能放入 Monty。

当前 SDK 同步 feed/resume 会阻塞主循环，单套 asyncio.timeout 不能保证总截止。适配同一已安装 Monty 的官方 AsyncMontySession.feed_run，复用 SDK ToolManager、类型签名、参数校验、调用预算、返回转换和执行关联。超时/取消中止 worker，并取消、等待所有已启动宿主回调，再清理 session/pool。正常完成同 Run 复用 REPL；restart 清变量，既有副作用不回滚。

Python 原超时、内存、代码 64 KiB、正式结果 512 KiB、日志和文件限制不变。64 MiB 通信帧不放大这些上限；16 MiB inputs 对原生 Python/Skill 同样生效。

## 5. 事实、诊断和兼容

外层仍一个 run_code 调用/返回，保持现有 Calls UI 和 ptc_diagnostics v1：单步输入/输出各 8 KiB、字符串 2 KiB、总诊断 128 KiB。全量内部参数/结果不保存成嵌套历史，不送下一轮模型。

可信 Python/导出执行完成后，真实文件引用汇总到外层现有 generated_files metadata。只接受宿主已授权或已登记文件，不信任 MCP 同名字段。后续失败/取消保留已成功成果，沿既有失败回执/取消修复路径持久化，不新增事件类型。失败不代表写入回滚；副作用顺序执行，未知结果先查实，不自动重跑整段。

HTTP、SSE、Live、History、安装格式不变；不改旧事件、计划、输出契约或业务上下文。旧 PTC 历史照常读取；新记录只增加开放工具的有界步骤和真实成果引用。

## 6. 验收

定向验证：双入口与授权、内部大结果/外层保护、32/16/64 MiB 边界、真实大输入跨进程、100/101 次、等待后 CPU 截止、取消和回调清理、同 Run 变量/restart、文件失败后保留、MCP 伪造拒绝。

执行 Python/Console 全量、适用数据库集成、安装导出、暂停恢复、本地 8000 真实 Provider 与浏览器验收。GLP 素材使用独立 Agent/Skill 和合成 MCP，至少一万条由 MCP 实际返回，在一段 PTC 中生成并发布可打开报告，独立核对行数、金额与摘要。Test MCP 仅小范围只读查询；合成链路不等于 GLP 业务回源验收。

原生对照允许既有外置文件交 Python，不封锁文件能力、不强迫抄写一万条。三组配对记录实际 Provider Token、缓存、请求数、耗时、文件读写与结果等价性；失败不计算同任务节省率，标题和估算量单列。
