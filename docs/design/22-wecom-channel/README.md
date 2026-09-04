# 22 · 企微 Channel

> 状态：DeepAgent 0.7.0 已实现企微 AIBot WebSocket Channel；数据库 Alembic `002`。

- [`wecom-channel-research.md`](wecom-channel-research.md)：企微机器人、OpenClaw Channel Plugin、
  官方 AIBot SDK 的调用链、能力边界和已知缺口。
- [`wecom-channel-design.md`](wecom-channel-design.md)：当前已实现的 Gateway、三表、会话映射、
  SSE 转换、媒体和单副本边界。

当前认可的核心边界：

1. 企微双向聊天是 **Channel**，不是 MCP，也不是 Skill。
2. DeepAgent 保持渠道无关；企微协议、连接、回调和消息格式由独立 Channel Gateway 承接。
3. 首版只实现 WebSocket；API 固定部署，Gateway 按环境选择是否部署一个副本。
4. Account 固定使用配置者平台账号；企微 Peer 只映射 Session，不冒充平台用户。
5. 企微业务 MCP/CLI 的授权、历史查询窗口与机器人实时聊天凭证相互独立。

研发事项统一登记在 [`../TODO.md`](../TODO.md)，本课题不维护第二份待办列表。
