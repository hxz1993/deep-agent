# 21 · Web 工具

> 状态：DeepAgent v0.6.5 已实现；Alembic head 为 `012`。

- [`web-tools-research.md`](web-tools-research.md)：Codex、DeepSeek Harness、Deep Agents、
  Dify、Pydantic AI/Harness 与搜索供应商的横向核验。
- [`web-tools-design.md`](web-tools-design.md)：`builtin:web`、全局配置、运行、安全、审计与测试契约。

当前产品只交付公开互联网的 `web_search` 和 `web_fetch`。Internal HTTP、任意 HTTP Request、
`execute_python` 联网、Monty Socket/Shell/环境变量均不在本课题范围。
