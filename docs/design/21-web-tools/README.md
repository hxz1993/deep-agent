# 21 · Web 工具

> 状态：DeepAgent v0.6.6 已实现；Alembic head 为 `012`。

- [`web-tools-research.md`](web-tools-research.md)：Codex、DeepSeek Harness、Deep Agents、
  Dify、Pydantic AI/Harness 与搜索供应商的横向核验。
- [`web-tools-design.md`](web-tools-design.md)：`builtin:web`、全局配置、运行、安全、审计与测试契约。

当前产品交付公开互联网的`web_search`、`web_fetch`，并向图片输入模型条件注入
`web_image_view`。Internal HTTP、任意 HTTP Request、`execute_python`联网、Monty
Socket/Shell/环境变量均不在本课题范围。
