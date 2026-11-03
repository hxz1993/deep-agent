# 41 · 工单助手委派本地仓库排查

2026-11-03 整理。当前为方案研究，尚未实施或完成联调。

## 接入方式

用户在现有 DeepAgent 工单助手里发起排查、补资料、看结果。工单助手通过一个仓库排查 MCP 委派任务。持有代码仓库的机器负责运行 Coding Agent，首版建议复用已配置的 Codex，DeepSeek Harness 可作为第二个执行后端。

```text
DeepAgent 工单助手（现有服务和用户会话）
  → 仓库排查 MCP（提交、查询、补资料、取消）
  → 执行机（代码仓库 + Codex App Server 或 DeepSeek Harness SDK）
```

部署上分两侧。DeepAgent 所在侧提供 MCP 和任务入口，仓库所在侧安装执行器。云端无法直接访问执行机时，优先让执行机主动连接任务入口并领取任务。两侧都在同一受控网络时，可评估直连。首版按白名单仓库和固定 Git revision 做只读诊断，返回结论、代码路径、行号及证据。会话归属由可信调用方绑定，查询、续跑和取消都要核对任务归属。

## 原会话与人工介入

- **新工单**。提交后立即返回 `task_id`。执行服务保存 DeepAgent 会话、工单、提交者与执行会话 ID 的映射。独立工单分别建执行会话，同一个工单可以续跑多轮。
- **补资料**。例如执行器发现缺少失败请求日志，返回 `needs_input` 和具体问题「请补一条失败请求的 trace ID」。DeepAgent 在原对话里问用户。用户补充后，按原 `task_id` 恢复 Codex thread 或 DeepSeek Harness session。按任务归属和关联 ID 定位，避免按问题文字相似度串单。
- **人工审核**。首版只读诊断，用户可以在原工单确认结论。若后续需要修改代码并开 PR，正式批准在 GitHub PR 完成，DeepAgent 可展示待审核状态和链接。
- **结果回到原会话**。首版在当前轮有结果时直接展示，长任务后续可在原会话查询。如果需要用户离开后主动推送，另行设计后台事件到 Console／原渠道的投递。现有 `channel_message` 用于当前渠道运行内发送。[渠道 Message 设计](../32-new-channels/channel-tools-abstraction.md)

## 两个项目能复用什么

| **路径** | **现有机制** | **适用条件与接入工作** |
| --- | --- | --- |
| 直接接 Codex App Server 或 DeepSeek Harness SDK | 可程序化启动或续跑本地执行会话 | DeepAgent 是主要入口时优先采用。复用上游执行器，MCP 侧实现任务协议、身份映射和结果回读。[Codex App Server](https://github.com/openai/codex/tree/main/codex-rs/app-server) · [DeepSeek Harness SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.md) |
| Symphony＋Codex | 从外部工单系统取任务，管理工作区、重试和 Codex 生命周期。收到 `turn/completed` 后再查工单状态，决定是否在同一个 thread 续跑 | 已有 GitHub Issue、Linear 等工单入口时更容易复用。DeepAgent 直接发起任务时，需要接任务来源和结果回传。上游为工程预览。[部署与 Tracker](https://github.com/openai/symphony/blob/main/elixir/README.md) · [Runner](https://github.com/openai/symphony/blob/main/elixir/lib/symphony_elixir/agent_runner.ex) |
| SuperAgent＋DeepSeek Harness | Worker 用 `orchestrator_report` 上报业务状态。`human_action` 通过 `worker_attention` 进入主调度会话，再回到原话题。关联 PR 后可等待 GitHub 审核事件 | 飞书／GitHub 已作为任务入口时能复用完整插件。改接 DeepAgent 时需补入站 `Source` 和回传 `Channel`。上游为研究原型。[执行机制](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/003-execution.md) · [沟通规则](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/004-prompts.md) · [渠道适配](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/008-cookbook.md) |

SuperAgent 自己的 `<stateDir>/orchestrator.sqlite` 保存入口事件、话题与 Ticket 的绑定、执行分派、Worker 报告、投递队列和回执。主会话 ID 也在插件状态中。Worker 的完整聊天历史由 DeepSeek Harness 管理。若直接接 Harness SDK，任务状态和执行会话映射由仓库排查服务管理。[SuperAgent 持久化结构](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/007-architecture.md#persistence)

### Symphony 接入点

- **用 GitHub Issues 快速验证**。DeepAgent 通过 MCP 把已确认的排查任务同步到受控的 GitHub Issue，Symphony 沿用内置 GitHub Tracker。结果由适配层按任务映射回读，DeepAgent 负责向用户回复。GitHub Issues 是任务入口，代码仓库由执行机的工作区准备流程决定，两者可分开配置。首轮用脱敏工单验证。[GitHub Issues 适配器](https://github.com/openai/symphony/blob/main/elixir/README.md#github-issues-adapter) · [工作区准备](https://github.com/openai/symphony/blob/main/SPEC.md#9-workspace-management-and-safety)
- **接自有工单或任务服务**。按 Symphony `Tracker` 增加适配器，提供按状态取候选任务和按 ID 刷新，复用原调度循环、工作区与 Codex Runner。当前 Elixir 实现需要在代码中注册新适配器。结果、补料和完成状态要另接回写接口。等待补料时用任务源的状态控制是否继续调度，补料后重新开放执行。是否能恢复同一个 Codex thread 需要单独验证，特别是调度器重启后。[Tracker 源码](https://github.com/openai/symphony/blob/main/elixir/lib/symphony_elixir/tracker.ex) · [重启恢复规范](https://github.com/openai/symphony/blob/main/SPEC.md#143-partial-state-recovery-restart)

### SuperAgent 的调度职责

DeepAgent 决定是否派单、给哪些资料、何时向用户补问以及怎样呈现结论。SuperAgent 可作为执行侧任务管理服务，负责排队、工作区、Worker 会话、暂停续跑和状态上报。仓库分析由 Worker 执行。

原版 SuperAgent 的主调度会话会参与判断接单、归并任务和发消息。按飞书示例只增加一个 `Source` 和 `Channel`，仍会走这套路由。若由 DeepAgent 统一调度，建议适配器把已授权的请求转成明确的提交、补料、查询和取消命令，复用插件的 Ticket 和执行机制，并回传结构化状态。插件内部有显式请求的接单逻辑，仍需实现对 DeepAgent 的调用入口。这样用户补资料可以按 `task_id` 送回指定 Worker，DeepAgent 统一回复。[Engine 职责](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/007-architecture.md) · [Source 与 Channel 接法](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/008-cookbook.md)

## 首版验证口径

用一条只读工单验证完整路径。DeepAgent 发起任务后拿到任务 ID，执行机在指定 revision 排查。模拟缺少 trace ID，用户从原 DeepAgent 会话补上，原执行会话续跑并返回可复核的文件与行号。同时验证另一个用户无法查询或续跑该任务，以及执行机重启后任务状态仍可查询。研发状态见[设计台账](../TODO.md)。
