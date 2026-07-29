# DeepAgent AI Lab 学习结论

> 状态：持续维护的学习结论入口
> 最后更新：2026-07-27
> 适用范围：DeepAgent 架构、配置、上下文、Memory、Artifact、Planning、SubAgent 与框架选型

## 1. 文档用途

本文件集中记录 `deepfos-AI/agent-labs/` 中已经完成的学习和实验结论。

后续讨论 DeepAgent 架构时先读本文件，不需要默认重新通读 AgentOS、Pi、Hermes、
OpenCode、DeerFlow、Deep Agents、Learn Claude Code 和 Harness Engineering 源码。

只有出现以下情况时，才回到 AI Lab 或上游源码继续调研：

- 当前结论缺少实现证据。
- 上游版本发生明显变化。
- 准备正式引入某项能力，需要重新核对 API 和兼容性。
- 新需求超出本文档已经覆盖的范围。

本文件记录的是可复用结论和当前 DeepAgent 决策。完整实验数据、源码入口和过程证据仍保留在
AI Lab，不重复搬入产品仓库。

## 2. 总体结论

DeepAgent 当前不应整体迁移到 AgentOS 或其他 Agent 框架。

推荐路线：

```text
保留 DeepAgent 产品层与 Pydantic AI Core
  → 先补请求预算和 Token 可观测性
  → 选择性引入 Harness 上下文治理
  → 建设 DeepFOS 自己的 Artifact
  → 有明确场景后再做 Memory
  → 最后评估 Planning 与 SubAgent
```

需要长期保留的 DeepAgent 产品资产：

- Agent、模型、MCP、UI 工具和辅助模型配置。
- 平台用户身份、App、Space 与请求 Header 边界。
- SQL 会话、完整有序历史、历史分支与取消恢复。
- Deferred HITL、Pending 刷新恢复和 UI 工具协议。
- Console、SSE、附件和现有前后端数据契约。

上游项目适合提供运行机制和设计参考，不能替代这些产品责任。

## 3. 上游项目分别值得学习什么

| 项目 | 最值得学习 | 不应直接照搬 |
| --- | --- | --- |
| AgentOS / Agno | Session、Run、HITL、Memory、Registry、平台 API | 直接替换现有会话、SSE、配置中心和 DeepFOS 权限模型 |
| Pydantic AI Harness | Compaction、长工具结果治理、Memory 原语 | 用 StepPersistence 替换现有 SQL 会话 |
| Deep Agents | 上下文分层、工具结果外置、Skill 渐进加载、受限子代理 | 与 Pydantic AI 并存成第二套运行时 |
| DeerFlow | Workspace、Artifact、Sandbox、上传和长任务 | 把其全栈结构整体迁入 DeepAgent |
| Hermes | 上下文压缩、跨会话记忆、Skill 学习、Markdown 计划 | 个人 Agent 的单租户和本地配置假设 |
| Pi Agent | AgentSession、树形会话、事件、Compaction、Extension Hook | TypeScript Coding Agent 运行时 |
| OpenCode | Client/Server、SSE、权限、错误恢复、配置分层 | Coding Agent 的工具和权限模型 |
| Learn Claude Code | Agent Loop、工具边界、权限、压缩、错误恢复的最小机制 | 重新自研 Pydantic AI 已有的 Agent Loop |
| Harness Engineering | 上下文、能力、权限、证据和反馈闭环的工程纪律 | 把方法论文档整体塞入 Prompt |

## 4. 配置管理学习结论

### 4.1 上游项目的共同做法

成熟项目普遍采用分层配置，并不存在“全部使用环境变量”或“全部使用数据库”的统一答案：

```text
基础设施和密钥
  → 环境变量或 Secret Source

全局运行策略
  → YAML、JSON、代码参数或平台配置

Agent 个性化行为
  → Agent 定义、项目配置或 Registry

每轮请求身份和动态状态
  → Run Context

会话、Memory、Run 和 Trace
  → 数据库
```

项目差异主要来自产品形态：

| 项目 | 配置方式 | 原因 |
| --- | --- | --- |
| AgentOS | Python/YAML/环境变量 + Registry Component | 同时支持代码定义和平台管理 |
| Pi | 全局与项目级 `settings.json` 合并 | 个人开发工具，不需要共享数据库配置 |
| Hermes | `config.yaml` + 独立 Secret Source | 个人 Agent，运行策略多且需要本地可编辑 |
| OpenCode | 系统、组织、全局、项目和环境变量分层合并 | 需要同时承接组织治理和项目覆盖 |
| DeerFlow | 类型化 YAML + 环境变量 | 运行策略复杂，需要校验和热加载 |
| Deep Agents | Middleware 参数 + Run Config | 它是 Harness 库，不是配置中心产品 |

配置缓存主要用于避免重复解析文件、加载远程配置或支持热更新，不是运行策略不能放数据库的原因。

### 4.2 DeepAgent 当前决定

DeepAgent 现阶段不新增数据库全局配置表，运行策略继续使用环境变量。

原因：

- 当前最急迫的是解决请求预算和上下文治理，不值得同时引入新的配置表、Store、API 和迁移。
- 环境变量天然是部署实例级全局配置，符合当前 Test/线上交付方式。
- 只读管理入口可以先展示环境变量的最终生效值，不要求它们支持在线修改。
- 等配置数量、在线调整或多租户差异形成真实需求后，再重新评估数据库或外部配置中心。

当前已确认：

- 删除 Agent 级 `max_requests`，所有 Agent 统一服从 `DEEP_AGENT_MAX_REQUESTS`。
- 全局代码默认值采用 `30` 次/单轮，部署环境仍可通过环境变量覆盖。
- Console 只读展示运行、MCP、上传、图片和标题等安全配置；数据库连接、密钥、Token、代理地址和本地路径不返回。
- 配置保持环境变量/现有配置加载机制，不新增数据库配置表和在线编辑入口。

## 5. AgentOS 实验结论

AgentOS 2.8.2 已验证：

- 真实 DeepSeek 与 Go MCP Tool/Resource。
- SQLite/MySQL 跨实例 Session。
- 数据库 Pending HITL 恢复。
- Redis 分布式取消。
- Memory 更新、召回、删除和范围隔离。
- Markdown Artifact 版本化。
- Registry Component 的草稿、发布、运行和删除。

当前不迁移的决定性原因：

1. 迁移成本主要在 DeepAgent 已有产品协议，不在 Agent Loop。
2. MySQL 默认 `REPEATABLE READ` 并发样本出现 Run 返回完成但持久化失败。
3. 同一个 Pending Run 被两个实例继续时会重复执行副作用，需要额外的 claim/lease 机制。
4. AgentOS 默认历史窗口不是完整的语义压缩。
5. 官方完整 Control Plane/Studio 当前依赖 hosted 页面，开源 Agent UI 只是聊天客户端。

AgentOS 保留为 DeepAgent 2.0 或新组件的候选，不进入当前产品主链。

## 6. 上下文与 Harness 结论

DeepAgent 当前每轮加载完整 `chat_messages` 和旧 Tool Result，真正的主要压力来自历史工具结果。

Harness 0.10 隔离实验得到：

- 真实 DeepAgent 夹具执行 `ClearToolResults` 后估算 Token 减少约 `60.9%`。
- 合成长工具历史减少约 `74.3%`。
- `ClampOversizedMessages` 对单条异常超长内容减少约 `99%`。
- Core 升级到 `pydantic-ai-slim 2.14.1` 后，DeepAgent 全量测试 `428` 项通过、`4` 项跳过。

推荐顺序：

```text
模型窗口与 Token dry-run
  → ClampOversizedMessages
  → ClearToolResults
  → 持久化 LLM 上下文投影
  → Tiered Summary
  → OverflowingToolOutput
```

关键边界：

- 不用 SlidingWindow 作为默认策略，避免永久丢失旧业务约束。
- Harness 摘要不会自动进入 DeepAgent 当前保存的 `result.new_messages()`。
- 启用摘要前必须单独保存给模型使用的压缩投影，不能覆盖原始历史。
- Harness 默认只读取旧 Tool Return 的前 500 字符用于摘要，不能依赖它保存完整财务结果。
- Tool Call/Result 配对、Pending HITL、历史分支和取消恢复必须纳入回归。

## 7. Artifact、Memory、Planning 与 SubAgent

### Artifact

Artifact 应作为 DeepFOS 自己的业务实体，独立于聊天 Session。

适合承载：

- Markdown 编制计划。
- 填充计划。
- 可版本化中间产物。
- 需要跨 Session 引用的大型结果。

### Memory

Memory 只保存明确可跨会话复用的认知，不复制聊天历史或压缩摘要。

至少需要考虑：

- `user / agent / app / space` 范围。
- 来源、更新时间和审计。
- 冲突更新。
- 用户查看和显式删除。
- 哪些内容允许自动写入。

### Planning 与 SubAgent

当前不是优先级。只有长任务和专业分工形成稳定场景后再引入。

引入时必须由 DeepAgent 控制：

- 模型和请求预算。
- 工具白名单和权限。
- 超时、并发和取消。
- Artifact 读写范围。
- 父子 Run 关系。
- 子 Agent 只返回必要结论和产物引用。

## 8. 工程方法结论

后续改进 DeepAgent 时遵循：

```text
先观察真实用户任务
  → 判断缺口属于上下文、能力、权限、证据还是反馈
  → 修改最早的责任边界
  → 跑项目原生测试
  → 重跑真实用户链路
  → 保留、修正或撤销
```

额外约束：

- `AGENTS.md` 是项目地图，Skill 是按需能力，Runbook 是可重复操作，不应全部堆进 Prompt。
- 用户身份、凭据和执行权限留在后端，不能交给模型自行决定。
- 一次成功回答不能证明机制稳定，测试证据必须匹配实际声明。
- 新机制优先使用 Pydantic AI Core/Harness 的扩展点，不继续扩大 `AgentRunner` 主循环。

## 9. 证据入口

需要查看完整证据时按主题进入：

| 主题 | 文档 |
| --- | --- |
| 上游项目能力总览 | `../../../agent-labs/upstream-agent-references/indexes/comparison.md` |
| AgentOS 最终决策 | `../../../agent-labs/deepagent-framework-evaluation/docs/decision-report.md` |
| AgentOS 真实验证 | `../../../agent-labs/deepagent-framework-evaluation/docs/live-validation-results.md` |
| Harness 压缩实验 | `../../../agent-labs/deepagent-context-compaction-evaluation/docs/test-results.md` |
| Learn Claude Code 对照 | `../../../agent-labs/learn-claude-code/DEEPAGENT_STUDY.md` |
| Harness Engineering 对照 | `../../../agent-labs/harness-engineering/DEEPAGENT_STUDY.md` |
| DeepAgent 原生能力审计 | `pydantic-ai-native-capabilities-and-deepagent-usage-audit.md` |
| 上下文压缩实施方案 | `context-compaction-implementation-plan.md` |

## 10. 维护规则

以后新增 AI Lab 调研时：

1. 实验代码、原始源码索引和详细数据继续放在 `deepfos-AI/agent-labs/`。
2. 对 DeepAgent 有稳定价值的结论同步补到本文件。
3. 已经写入本文件的结论，不因新会话重新做同一轮宽泛调研。
4. 若新证据推翻旧判断，直接更新原结论并记录日期，不并排保留相互冲突的口径。
5. 尚未确认的产品选择明确标记“待确认”，不写成当前实现。
