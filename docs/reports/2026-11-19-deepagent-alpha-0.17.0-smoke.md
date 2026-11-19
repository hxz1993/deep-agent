# DeepAgent 0.17.0 Alpha 验收

2026-11-19，Asia/Shanghai。Alpha `/info` 返回 `0.17.0`。本次写入限独立回归会话和新建的验收 Agent、发布、任务及合成文件。原有 Agent 配置与历史原地保留。Cloud 未纳入本次验收。

## 历史功能抽查

| 场景 | 结果 |
| --- | --- |
| 两条既有会话只读 | 正文、事件、运行状态均可读取。分别读到 45／58 条会话项、73／91 条事件，其中包含原有工具结果。 |
| GPT-6.2 Sol 普通对话 | SSE 返回合成标记，第二轮在原会话准确复述，刷新后两轮正文与轨迹可读。[诊断会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/18d7d12d21ea45979e69e90ab008bf93?view=conversation) |
| 智能对账助手结构化结果 | 使用 GPT-6.2 Sol 的两个测试轮次均首轮通过 `recon_workbench_v2` Schema，结果在同一会话可回读。[诊断会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/92514d1dc4b247e4aacf883fdbf17e1a?view=conversation) |
| DeepSeek Flash | 两轮模型请求均获上游 HTTP 402，诊断归类为 `ModelQuotaExceededError`。流式返回错误事件，非流式对外返回 HTTP 500。额度恢复后的正常运行待复验，非流式错误映射另行评估。[诊断会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/c0e26fb6603a496e9b8fa1cc57d2b551?view=conversation) |

## 新发布验收

验收 Agent 为「Alpha 0.17.0 发布验收助手」，发布编码 `alpha-v0170-release-smoke`，范围为应用 `czjniw044`、空间 `czjniw`。模型为 GPT-6.2 Sol，仅关联沙箱工具包，用于生成合成测试文件。

| 场景 | 结果 | 任务 ID |
| --- | --- | --- |
| CodeBuddy Code 2.183.0 直连 MCP | 发现发布、提交和查询成功。服务端任务已完成，返回预期合成标记。[诊断会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/0f074ec731414c13924cf499396381d1?view=conversation) | `21f317ed0bdf4e778a4c6753802fb80e` |
| MCP 文件交付 | 通过沙箱发布 `alpha-v0170-proof.txt`，Base64 返回的 24 字节与预期一致。已知下载路由携原鉴权也返回相同文件。默认链接生成返回 503。[诊断会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/f97e2265e5b24feabe162211194d92a4?view=conversation) | `b4b50b886f4443f981624c445f8e919d` |
| MCP 附件与续跑 | 沿文件任务的 Context 继续，创建了新任务，复用同一个 DeepAgent 会话。内联 TXT 输入成功，返回预期的附件标记。 | `59396070d4214f4e9d5222016cfdfbae` |
| 本机 A2A SDK 直连 | 通用 Card、指定应用 Card、well-known 和 `ListTasks` 都返回 503「请配置 Agent Service 对外地址」。官方 SDK 在 Card 发现阶段收到 503，Alpha A2A 业务执行待复验。 | — |

三项 MCP 任务及其会话均保留。诊断日志能按发布 ID 或任务 ID 查到排队、运行、完成及耗时，并可关联会话和运行。发布任务列表由进程保留 24 小时，重启后由会话诊断链接回看已保存轨迹。本次未在 Alpha 配置出站远程委派或 CLI 执行器。

## 0.17.0 部署问题

1. Alpha 发布列表返回空的 Card URL，`client-config` 返回 `mcp_url: null`，MCP 默认链接模式报告缺少有效对外下载基址。`0.17.0` 依赖 `DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL`，当时的 Alpha 部署未提供有效值。
2. Alpha 实例可按任务定位 MCP 日志，A2A 地址校验抛出的 503 缺少发布诊断。`0.17.1` 的 Card 和 RPC 在地址解析失败时各记录一条脱敏诊断，包含协议、发布 ID、`phase=public_address` 和 HTTP 状态。

## 0.17.1 复测和发版配置

- Alpha DeepSeek Flash 额度恢复后，流式首轮与非流式续聊均完成。首轮结构化候选自动纠错一次后通过，第二轮首试通过。[复测会话](https://alpha.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/99f9732b195448449b088dd7957f3c24?view=conversation)
- Test 线上为 `0.17.0`，库为 `016`。在独立测试发布 `test-v0171-release-smoke` 上复现缺基址时的 A2A Card 和 RPC 503。MCP 只传 `app` 时，Test 网关返回「应用不可用」。同时传正确的 `app` 与 `space` 后，MCP 发现、提交、查询和结果回读均通过，任务 `0d4dbdc5efda4e7c8a33d4e499e33a5f`。[Test 复测会话](https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/75b1f37e4cfe4895b3e57cb01b9aa182?view=conversation)
- Test 用 DeepSeek Flash 执行独立的非流式合成回归，HTTP 200 并返回预期正文。[DeepSeek 复测会话](https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/#/diagnostics/sessions/fd402cb371c245b7b37bd7c7b54f7b95?view=conversation)
- 在 `0.17.1` 隔离实例中，使用 Test 真实账号及应用权限、真实 DeepSeek Flash 和带网关前缀的 HTTP 入口，验证两种 A2A Card、SDK 发起与查询、MCP 提交与查询、默认链接下载和匿名拒绝。新版按请求生成服务基址，无需设置发布地址环境变量。Test 线上仍需在部署新版后复验实际网关转发。[部署说明](../guides/agent-service.md#部署与本地启动)
- 用合成 402 复验流式、非流式、模型连通性测试和事件回读。非流式返回 `provider_quota_exceeded`，SSE 返回额度提示，单条 Run 失败日志含安全堆栈，供应商响应正文与凭据不进入这条日志。
- 开发 Skill 包与 `0.17.1` 一致，相对 `0.17.0` 依赖与数据库结构无变更。Test 和 Alpha 的平台路由使用各自域名和 `/deepfos-server/deep-agent-server1-0` 前缀。
