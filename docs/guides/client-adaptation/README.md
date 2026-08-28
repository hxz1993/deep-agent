# 外部客户端适配

DeepAgent 的 Event Store 是内部事实源，普通客户端统一消费 Conversation/History 投影。

- [DeepCube 助手改造清单](deepcube-conversation-history.md)
- [DeepTable 助手改造清单](deeptable-conversation-history.md)
- [通用 Conversation/History 迁移说明](../session-event-client-migration.md)
- [SSE 协议](../sse-protocol.md)
- [History JSON Fixture](../fixtures/conversation-history-v1.json)
- [SSE NDJSON Fixture](../fixtures/conversation-sse-v1.ndjson)

这些文档只记录外部仓库的改造范围，不授权 DeepAgent 任务直接修改外部客户端。
