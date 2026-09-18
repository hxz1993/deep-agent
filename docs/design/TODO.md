# DeepAgent 设计与研发台账

本文件只保留尚未完成或需要外部条件的事项。已交付能力不再重复拆成待办，历史决策从Git记录恢复。

## 当前交付外事项

| ID | 事项 | 当前边界 | 启动条件 |
| --- | --- | --- | --- |
| `CLIENT-EVENTS-001` | 电子表格助手与DeepCube助手适配Event API | 本仓只审计和交接，不跨仓修改 | 客户端维护方按v0.6.0契约排期 |
| `SCHEDULE-001` | [平台自动化 Agent 任务](26-scheduled-tasks/README.md) | 2026-09-16 调整为平台触发＋后台身份＋Agent 执行＋业务投递；会话为执行记录，新建/复用按场景评估，跨周期去重与聊天记忆分开；未开发 | 以工单清单发群为候选核验触发、凭据与主动发送链路，再决定独立定时入口及任务状态范围 |
| `SANDBOX-001` | [会话级 Sandbox](07-python-sandbox/session-sandbox-design.md) | 活跃会话复用工作目录，支持 Python/Shell；允许到期清理并明确告知；长期目录保留、内存恢复非硬要求；尚未开发 | 验证候选运行时的部署、隔离及生命周期成本，确定有效期与最小验收；不要求用户预先锁定永久存储方案 |
| `OPS-SSE-001` | 网关SSE Read Timeout至少330秒、建议360秒并关闭缓冲 | 应用已每约10秒发送压缩进度；网关由部署平台维护 | 发布0.6.0 Test镜像时同步调整并验证 |
| `ATT-CONFIG-001` | 平台附件扩展名白名单补充MD/HTML/XML/WEBP/GIF/WAV | 只改环境配置，不扩展`read_file`或Provider模态 | 各环境完成六种最小文件上传验证 |
| `WEB-PROVIDER-VERIFY-001` | Exa、You.com真实Key联调 | 现有适配保留；DeepSeek/Kimi改版后管理接口真实检索通过，Tavily 已通过用户本地会话验证，Exa/You.com尚未真实Key联调 | 获得各Provider Key后补真实搜索验收 |

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
| `WECOM-HA-001` | 企微Channel多副本、主动消息与用户级身份映射 | v0.7.0只承诺单Gateway副本、被动回复和管理员配置的会话用户，不预埋Lease/Outbox/Identity表 | 出现多副本、可靠主动通知或企微用户差异权限的真实需求 |

## v0.7.0 已完成

- 企微 AIBot WebSocket Channel，使用官方 Python SDK 承接认证、心跳、重连、媒体解密和流式回复。
- 可选独立 Gateway 进程、Account/Session Binding/Message Run 三表、管理员配置的会话用户与同Peer串行。
- 公共 Gateway/ChannelService 与 WeCom Adapter 分离；仅 Adapter 引用官方 SDK，飞书适配仍需另行实现。
- 五个渠道命令 `/help`、`/new`、`/stop`、`/status`、`/whoami`；停止独立控制路径与15秒确认，
  `/new` 停止后原子换绑，旧历史保留；不实现 `/reset`，普通客户端不增加全局拦截。
- text、mixed、图片、文件、AMR语音和视频进入现有Chat/FileStore链路，回复只投影Markdown文本。
- Console渠道配置、连接状态和测试入口；Gateway未部署或不可达时仍可维护配置并明确显示状态。
- Console只读快捷命令入口与 `/help` 共用目录；网关与机器人状态分开；Session标题事件保存渠道来源，
  支持本地旧数据幂等修复，数据库仍为002。

## v0.6.0 已完成

- `chat_sessions + chat_session_events`成为唯一会话事实源；模型历史、Event API、Conversation History、
  Console轨迹和Plan从同一Event Store投影；实时SSE只承载当前Run增量与终态。
- Turn/Run边界、Deferred跨Run、完整Pydantic Message逐行往返、Part Offset工具定位和未知Part失败闭合。
- 单Event上下文压缩、固定八段摘要、Tool/Context Evidence、DSH式超大结果裁剪、300秒预算、SSE
  心跳、取消、Stale校验和安全Provider Overflow重试。
- `search_conversation_history`、`read_conversation_event`与中文检索；不做透明业务缓存。
- Harness Planning只保留`write_plan`，同Turn Deferred恢复完整快照，新Turn不注入旧计划。
- Revision 007会话归档工具与Alembic 008破坏性会话迁移；配置表不受影响。

## v0.6.2 已完成

- 显式`builtin:code-mode`，已授权且已披露MCP的原生Schema与`run_code`双通道。
- Code-only `load_file_data`、Run内不透明文件变量、MCP边界延迟Base64和脱敏嵌套调用审计。
- Shell、Host FileSystem、沙箱生成二进制文件、通用Artifact CRUD和Tool Approval仍不在本次范围。

## v0.6.3 已完成

- Code Mode SDK作用域说明、`load_file_data`签名一致性、可信返回Schema和嵌套审计总量上限。
- DeepSeek v4 Vision当前轮图片按像素尺寸估算上下文Token；未知Provider/模型继续保守回退。
- Console轨迹的`CODE`标签、Python Code页签，以及附件与Context Reference分区预览。

## v0.6.5 已完成

- 默认不关联的`builtin:web`，固定包含`web_search`和`web_fetch`；未授权Agent完全没有网络工具。
- 当时交付全局唯一Web Search配置，选择当前模型或六种固定Provider；2026-09-19由`WEB-SEARCH-002`取代此模式设计，改版纳入 v0.10.14。
- Pydantic AI原生Search、DuckDuckGo/Tavily和SSRF-safe Fetch，Harness Exa/You.com，以及
  DeepSeek/Kimi薄协议适配；Code Mode只可编排显式授权函数，Monty和`execute_python`不获得任意网络。

框架能力是否采用的长期原则见
[`14-framework-capabilities/framework-capabilities-baseline.md`](14-framework-capabilities/framework-capabilities-baseline.md)。
