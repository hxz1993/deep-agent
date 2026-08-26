# DeepAgent 设计与研发台账

本文件只保留尚未完成或需要外部条件的事项。已交付能力不再重复拆成待办，历史决策从Git记录恢复。

## 当前交付外事项

| ID | 事项 | 当前边界 | 启动条件 |
| --- | --- | --- | --- |
| `CLIENT-EVENTS-001` | 电子表格助手与DeepCube助手适配Event API | 本仓只审计和交接，不跨仓修改 | 客户端维护方按v0.6.0契约排期 |
| `OPS-SSE-001` | 网关SSE Read Timeout至少330秒、建议360秒并关闭缓冲 | 应用已每约10秒发送压缩进度；网关由部署平台维护 | 发布0.6.0 Test镜像时同步调整并验证 |
| `ATT-CONFIG-001` | 平台附件扩展名白名单补充MD/HTML/XML/WEBP/GIF/WAV | 只改环境配置，不扩展`read_file`或Provider模态 | 各环境完成六种最小文件上传验证 |

## 需要真实需求后再设计

| ID | 方向 | 不预埋的原因 | 重新启动条件 |
| --- | --- | --- | --- |
| `SESSION-BRANCH-001` | Branch From | 父链读取与复制前缀会产生不同DDL和删除语义 | 产品确定交互、数据保留和分支删除规则 |
| `SESSION-RESTORE-001` | Revision 007少量会话恢复 | 通用导入器会扩大旧格式兼容面 | 用户给出明确Session ID后编写一次性Dry Run脚本 |
| `SEARCH-INDEX-001` | SQLite FTS5、PostgreSQL GIN或MySQL FULLTEXT | 当前有界应用层中英文评分已可用，三种方言索引方案不同 | 真实事件量和查询计划证明64K Search Text扫描成为瓶颈 |
| `MEMORY-001` | 跨会话长期记忆 | History Search不是可编辑长期记忆；namespace也不是授权 | 产品明确查看、编辑、删除、审计和User/Agent/App/Space边界 |
| `RUN-RECOVERY-001` | 长任务崩溃恢复与副作用账本 | Event Store恢复会话，不保证外部写操作可重放 | 长工具确需跨进程继续且业务幂等已定义 |
| `PLAN-GLOBAL-001` | 跨Turn可编辑全局计划 | 当前Plan是给用户看的Turn内完整快照，不是执行调度事实源 | 产品需要跨消息人工编辑、继续或共享计划 |
| `TOOL-APPROVAL-001` | 写入/删除/外发工具审批 | 需要业务权限、操作人、TTL、审计和幂等共同设计 | 出现明确高风险工具与审批责任方 |
| `DOCUMENT-001` | PDF/PPT/PPTX页面级解析 | 不塞入普通文本`read_file` | 出现需要页面、图片、版面或OCR的真实业务样本 |

## v0.6.0 已完成

- `chat_sessions + chat_session_events`成为唯一会话事实源；模型历史、Event API、持久化SSE、Console
  轨迹和Plan从同一Event Store投影。
- Turn/Run边界、Deferred跨Run、完整Pydantic Message逐行往返、Part Offset工具定位和未知Part失败闭合。
- 单Event上下文压缩、固定八段摘要、Tool/Context Evidence、DSH式超大结果裁剪、300秒预算、SSE
  心跳、取消、Stale校验和安全Provider Overflow重试。
- `search_conversation_history`、`read_conversation_event`与中文检索；不做透明业务缓存。
- Harness Planning只保留`write_plan`，同Turn Deferred恢复完整快照，新Turn不注入旧计划。
- Revision 007会话归档工具与Alembic 008破坏性会话迁移；配置表不受影响。

框架能力是否采用的长期原则见
[`14-framework-capabilities/framework-capabilities-baseline.md`](14-framework-capabilities/framework-capabilities-baseline.md)。
