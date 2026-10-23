# 20 · PTC 程序编排

> 2026-10-24：v0.14.7增量实现。依赖不变，数据库保持013；实现状态不代替回归报告。

PTC是需要显式关联、默认关闭的Agent能力包`builtin:ptc`，Console名称为“程序编排”。
模型使用`run_code(code, restart=false)`在Monty Python子集中编排工具；旧编码`builtin:code-mode`
仅作兼容，新保存和导出使用`builtin:ptc`。

当前实现的工具范围：

- 已授权、已披露MCP保留普通Schema与PTC双入口。
- 当前已装配的`execute_python`、`execute_skill_script`、`web_search`、`web_fetch`和
  `export_file_to_download_center`增加PTC入口，保留原生调用、参数校验和权限过滤。
- `load_file_data`仍仅在PTC中使用，支持授权File ID与公开HTTP(S)文件原样中转。
- 不接入`read_file`、`parse_document`、`web_image_view`、UI/Deferred、发现、计划、历史、
  Resource或任意Shell；不增加`save_file_data`、Skill参数、共享工作区或历史结果引用机制。

内部工具返回完整变量，绕过面向模型的Token外置、截断和分页包装；普通工具及外层`run_code`
继续使用原有保护。数据源正式分页和原工具限制不变，不自动翻页，也不把已有预览当作全量。
JSON通过现有`inputs`直接交给Python或Skill；Python仍使用原`files/input_path/publish_path`。
可信Python/导出产物的安全引用汇总到外层既有`generated_files` metadata，后续失败或取消保留
已完成成果，不保存全量嵌套参数和结果。UI、SSE、History类型与安装格式不变。

当前固定限制：Monty内存256MiB、单个内部返回32MiB、同Run REPL Session执行预算120秒、
每段总截止120秒（包含工具等待）、每段100次内部调用。Python/Skill inputs为16MiB紧凑UTF-8 JSON，
通信帧64MiB；其他沙箱限制不变，不新增配置项。官方AsyncMonty提供异步worker桥，继续复用SDK
工具管理、参数校验、类型签名、预算和返回转换。

- [`ptc-data-transfer-design.md`](ptc-data-transfer-design.md)：本轮最终范围、两条链路、文件授权与验收门禁。
- [`hybrid-code-mode-design.md`](hybrid-code-mode-design.md)：当前架构、权限、原语和失败边界。
- [`monty-diagnostics-design.md`](monty-diagnostics-design.md)：历史诊断设计；本轮沿用现有Calls UI，不扩大界面改造。
- [`../../guides/code-mode.md`](../../guides/code-mode.md)：配置、使用示例、限额与排障。
- [`../07-python-sandbox/session-sandbox-design.md`](../07-python-sandbox/session-sandbox-design.md)：独立的后续工作区课题，不属于本轮已实现能力。
