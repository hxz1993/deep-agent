# A2A 服务端实施与出站边界

2026-11-12 · [45 号课题](README.md)。服务端详细设计以[两阶段实施总案](server-implementation-plan.md)和其子文档为准；本文保留框架选型、项目接口分工及出站后续边界。

## FastA2A 选型记录

早期方案评估了 FastA2A 的 ASGI 和 Worker / Broker / Storage 组织。最终一期采用官方 a2a-sdk 和现有 FastAPI，实际入口见[服务端指南](../../guides/agent-service.md)。业务执行由现有 DeepAgent Runner 承担，不使用默认 `agent_to_a2a` 启动第二套 Agent 或会话。只允许经项目 Task 服务进入 Worker。

| 层 | 复用与自定义 |
| --- | --- |
| 协议网关 | 使用 FastA2A／Starlette ASGI 的挂载和生命周期组织。为 1.0 严格报文、版本/租户验证、受理与等待、SSE 快照、错误响应提供自己的协议路由与转换。优先用 SDK 类型/规范校验对照；不假定 FastA2A 私有方法可稳定覆盖。若没有可扩展的公开接口，自管薄 ASGI 路由，不改 site-packages、不双重注册受保护路由。 |
| Storage | 使用有界的进程内 Task/Context 仓储，补齐作用域、重试去重和清理规则。普通会话事件继续为内部 Runner 历史。 |
| Broker | 同进程内有界队列，支持状态订阅和取消；终态和超时用原子更新处理。重启后内存任务失效。 |
| Worker | 按 FastA2A Worker 契约接入现有 Runner；自管有界执行组与取消处理，不能把取消排在同一条长执行队列后。取消和终态写入检查代次。父应用显式接管 ASGI 子应用生命周期。 |
| 官方 SDK | `a2a-sdk` 是独立验收对照与阶段 2 Python 调用基座。生产服务若不需要官方 ASGI 运行时，不引入 `[all]` 或 `[fastapi]` extras。 |

这轮重新核对的 [FastA2A 2.1.1](https://pypi.org/project/fasta2a/2.1.1/) 已提供部分 1.0 方法名/枚举适配。旧结论「只能旧方法」撤回；实测仍存在等待、任务续跑、分页、取消、时间戳和授权缺口，见[验证记录](server-validation.md)。官方 SDK、PyPI 和当前源码版本分别锁定。实现阶段首先跑完整依赖解析、父应用生命周期和基础镜像对照；不能因为隔离试验能安装就承诺原镜像可用。

## 第二阶段的边界

- DeepAgent Python SDK 调标准 A2A 服务，再封装自己的授权上传、等待、补答和下载。CLI 同 SDK 分发，不新做后台服务或私有生命周期。
- MCP 作为供调用方 Agent 使用的接口，按发布授权接入同一个 Task 服务；普通工具优先，MCP Tasks 扩展另验能力协商。详情见[客户端交付](server-clients.md)。
- 现有组件的短请求智能输出继续见 [44 号课题](../44-agent-invocation/README.md)；[42 号课题](../42-enterprise-auth-adapters/README.md)处理下游 MCP 身份和目标应用。三者各自有边界，不能用公开 A2A Card 代替真实下游授权。

## DeepAgent 作为客户端（后续）

单独页面和实施项：登记远端 Agent Card、连接别名、允许的协议/版本、可信目标地址、凭据引用、远端租户和使用授权。普通管理员或模型不能临时发起任意地址连接；工具只暴露已授权远端。客户端需跟踪本地 Run 与远端 Task/Context，记录提交和撤销，不把本地停止当成远端已经取消。远端文件进入本地前要单独核验来源、文件类型、大小和作用域。以一个真实远端服务的发现、续跑、取件、失权和重启证据启动该项目；不并入本轮两阶段服务端交付。

背景资料：[Dot 交接详稿](https://chatgpt.com/space/page_dd86da26ca8c8191b5c07d19ef7ff85f)、[A2A 1.0 规范](https://a2a-protocol.org/latest/specification/)、[官方 Python SDK](https://github.com/a2aproject/a2a-python)。
