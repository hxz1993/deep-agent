# 09 · 结构化最终输出

> 首次研究：2026-08-03
>
> 当前状态：完成 Pydantic AI 原始语义、开源项目实践和 DeepAgent 适用边界梳理；尚未实施

本课题专门讨论 Agent 如何交付稳定、可校验、可由程序消费的最终结果，以及它与普通工具、
UI Deferred 工具、MCP 结构化 Tool Result 的区别。

- [`structured-output-design.md`](structured-output-design.md)：当前设计结论、运行链路、适用边界和实施影响。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：工具体系总览；结构化最终输出的详细设计以本课题为准。
- [`../TODO.md`](../TODO.md)：跨课题唯一待办清单。

首版推荐方向是使用 Pydantic AI Core `ToolOutput`，不修改框架源码；但只有确实需要模型交付
机器可读结论的 Agent 才启用。Output Tool 不加入现有 `agents.capabilities`，而是由独立的
“输出契约”决定本次 Run 可选择的终止结果类型。

DeepCube 助手是典型适用场景：模型查询并综合多个工具结果后，最终交付脚本、页面配置、说明和
风险。现有 Markdown `config_fill` 只是文本模拟的结构化输出，未来可迁移为一组严格的 Output
Tool。后端业务工具已经能够返回权威结构化事实时，前端仍应直接消费 `tool_result`；只有需要模型
综合判断时，Output Tool 才返回引用事实形成的结论，不复制原始证据。
