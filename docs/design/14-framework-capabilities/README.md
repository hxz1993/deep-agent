# 14 · 框架能力采用基线

> 最近核验：2026-10-16。产品实装 Core 2.46.0、Harness 0.32.0、Monty 0.0.23；上游研究快照 Core 2.57.0／Harness 0.57.0。研究更新不代表产品依赖升级。

- [38 · Pydantic AI / Harness升级与能力采用](../38-pydantic-ai-harness-upgrade/README.md)：2026-10-16按用户要求独立建题，集中记录本轮 A～E 分档与逐项推进依据；本页继续维护采用基线。

- [`framework-capabilities-baseline.md`](framework-capabilities-baseline.md)：Provider、Core、Harness和
  DeepAgent职责、早期2.35.1／0.26.0历史采用状态及升级审计清单；当前版本差异以上述新研究为准。

当前核心原则：尽量使用Pydantic AI公共Part、History、Deferred和Cancellation原语；使用Harness的
Planning、Skills、ToolOutputLimits及Web Provider适配；由DeepAgent只补产品级Event Store、权限、
搜索路由、Compaction、SSE与Console。
