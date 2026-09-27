# 20 · PTC 程序编排

> 当前状态：DeepAgent v0.6.7 已实现；数据库结构为 Alembic 001生产基线，PTC本身不增加表或字段。

PTC是显式Agent能力包`builtin:ptc`，Console展示名为“程序编排”。启用后，模型获得Harness官方
`run_code(code, restart=false)`，并可在受限 Monty Python 子集中编排该 Agent 已授权且当前已经
披露的 MCP 工具。MCP 原生 Tool Schema 同时保留，因此一次简单调用仍可直接使用普通工具。
旧编码`builtin:code-mode`只作为运行与导入兼容入口；新保存和导出统一使用`builtin:ptc`。

文件原样中转使用PTC专属`load_file_data`：来源可以是当前会话已授权File ID，也可以是公开
HTTP(S) URL。宿主只向Monty返回当前Run内的不透明载荷，真实Base64仅在MCP调用边界延迟生成，
不进入模型、Event、History、SSE、搜索、轨迹或日志。需要解析、转换或生成新文件时，应改用
`execute_python`及`deepagent_files.publish_path()`，再把生成的File ID交给PTC。

PTC目录只包含已授权MCP与`load_file_data`，不包含Web Search、Web Fetch、Web Image View或
Python沙箱。这条边界避免同一网络能力出现多套含义相近的代码入口。

- [`hybrid-code-mode-design.md`](hybrid-code-mode-design.md)：架构、权限、文件变量、审计和失败边界。
- [`monty-diagnostics-design.md`](monty-diagnostics-design.md)：2026-09-24 C12 执行诊断方案，待实现；增强现有 PTC 详情，数据库继续 007，不增加表或列。
- [`../../guides/code-mode.md`](../../guides/code-mode.md)：配置、选择策略、示例和排障。
