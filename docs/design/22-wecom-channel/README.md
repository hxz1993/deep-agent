# 22 · 渠道网关与企微 Channel

> 状态：DeepAgent 0.7.0 已实现公共 Gateway、WeCom Adapter 与五个渠道命令；当时数据库为 Alembic `002`；当前版本 v0.11.6 / 数据库 `007`。

- [`wecom-channel-research.md`](wecom-channel-research.md)：企微机器人、OpenClaw Channel Plugin、
  官方 AIBot SDK 的调用链、能力边界和已知缺口。
- [`wecom-channel-design.md`](wecom-channel-design.md)：当前已实现的 Gateway、三表、会话映射、
  SSE 转换、媒体、快捷命令、来源标题和单副本边界。
- [`message-lifecycle-review.md`](message-lifecycle-review.md)：逐条消息与命令如何改变 Binding/Run，
  以及固定版本 OpenClaw、LangBot 的取舍依据。
- 个人微信与 AstrBot 的原研究及渠道扩展复盘已移至 [32 新渠道接入](../32-new-channels/README.md)，本课题只保留企微与公共 Gateway 基线。

当前认可的核心边界：

1. 企微双向聊天是 **Channel**，不是 MCP，也不是 Skill。
2. Gateway 管理 Adapter 生命周期，公共 channels 管理队列和命令；WeCom Adapter 只包装官方 Python SDK。
3. 首版只实现企微 WebSocket；一个 API worker 固定部署，Gateway 按环境选择是否部署一个副本。
4. Account 由管理员配置“会话用户”；企微 Peer 只映射 Session，不冒充平台用户。
5. 企微业务 MCP/CLI 的授权、历史查询窗口与机器人实时聊天凭证相互独立。
6. `/help`、`/new`、`/stop`、`/status`、`/whoami` 仅在渠道入口处理；不实现 `/reset` 或全局命令拦截。
7. 后续飞书复用公共队列、三表、命令和 API Client，但必须新增其 Adapter 与配置，当前不声称已支持。

研发事项统一登记在 [`../TODO.md`](../TODO.md)，本课题不维护第二份待办列表。
