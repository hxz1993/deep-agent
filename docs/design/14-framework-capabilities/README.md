# 14 · 框架能力采用基线

> 当前固定审计：Pydantic AI 2.35.1、Pydantic AI Harness 0.26.0、Monty 0.0.21、DeepAgent v0.6.4。

- [`framework-capabilities-baseline.md`](framework-capabilities-baseline.md)：Provider、Core、Harness和
  DeepAgent职责，已采用能力、明确不采用项与升级审计清单。

当前核心原则：尽量使用Pydantic AI公共Part、History、Deferred和Cancellation原语；使用Harness的
Planning、Skills与ToolOutputLimits；由DeepAgent只补产品级Event Store、权限、Compaction、SSE与Console。
