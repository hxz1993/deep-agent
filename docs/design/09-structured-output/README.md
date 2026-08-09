# 09 · 结构化最终输出

> 首次研究：2026-08-03
>
> 当前状态：设计结论已收口；`feat-ruge-0805` 实验实现已归档，当前明确暂缓正式实施

本课题专门讨论 Agent 如何交付稳定、可校验、可由程序消费的最终结果，以及它与普通工具、
UI Deferred 工具、MCP 结构化 Tool Result 的区别。

- [`structured-output-design.md`](structured-output-design.md)：当前设计结论、运行链路、适用边界和实施影响。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：工具体系总览；结构化最终输出的详细设计以本课题为准。
- [`../TODO.md`](../TODO.md)：跨课题唯一待办清单。

推荐方向仍是使用 Pydantic AI Core `ToolOutput`，不修改框架源码；但只有确实需要模型交付
机器可读结论的 Agent 才启用。Output Tool 不加入现有 `agents.capabilities`，而是由独立的
“输出契约组”提供多种可选终止结果类型。默认模式仍允许普通文本，模型只在需要格式化成果时
选择 Output Tool；需要 API 级强约束的场景再显式选择严格模式并禁止文本绕过。

DeepCube 助手是典型适用场景：模型查询并综合多个工具结果后，最终交付脚本、页面配置、说明和
风险。现有 Markdown `config_fill` 只是文本模拟的结构化输出，未来可迁移为一组可选的 Output
Tool。后端业务工具已经能够返回权威结构化事实时，前端仍应直接消费 `tool_result`；只有需要模型
综合判断时，Output Tool 才返回引用事实形成的结论，不复制原始证据。

0805 实验已经证明动态 Schema、SSE、历史、Deferred 和 Checkpoint 可以接通，也暴露了“固定一个
严格契约”和 `end_strategy=early` 静默忽略同响应其他工具调用的问题。实验代码及 Alembic `003`
不进入当前主线；保留的证据和重新实施边界见设计正文。
