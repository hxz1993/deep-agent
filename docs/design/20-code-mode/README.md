# 20 · Hybrid Code Mode

> 当前状态：DeepAgent v0.6.6 已实现；数据库结构为 Alembic 012，Code Mode本身不增加表或字段。

Hybrid Code Mode 是显式 Agent 能力包 `builtin:code-mode`。启用后，模型获得 Harness 官方
`run_code(code, restart=false)`，并可在受限 Monty Python 子集中编排该 Agent 已授权且当前已经
披露的 MCP 工具。MCP 原生 Tool Schema 同时保留，因此一次简单调用仍可直接使用普通工具。

文件中转使用 Code Mode 专属 `load_file_data`：宿主读取当前会话已授权文件后只向 Monty 返回
Run 内不透明 Token，真实 Base64 仅在 MCP 调用边界延迟生成，不进入模型、Event、History、SSE、
搜索、轨迹或日志。

- [`hybrid-code-mode-design.md`](hybrid-code-mode-design.md)：架构、权限、文件变量、审计和失败边界。
- [`../../guides/code-mode.md`](../../guides/code-mode.md)：配置、选择策略、示例和排障。
