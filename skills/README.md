# Python Skills

本目录存放 DeepAgent 进程内加载的 Python Skill。每个一级子目录是一个 Python
包，并在 `__init__.py` 中导出模块级 `SKILL = Skill(...)`。

Skill 可同时提供：

- 仅在该 Skill 启用时加入模型的 Instructions。
- 通过 Pydantic AI `FunctionToolset` 注册的 Python 工具。
- Console 展示使用的名称、说明和工具元数据。

运行时能力名称为 `skill:<name>`。Agent 必须使用该名称关联 Skill。

`calculator/` 是当前保留的最小示例，用于验证加载机制和安全表达式求值，不代表
DeepFOS 业务能力。

仓库不再兼容 `.cursor/skills/*/SKILL.md`。如果未来需要纯 Markdown 指令或长
Skill 按需读取，应扩展通用 Python Skill 协议，而不是引入 IDE 专属能力类型。
