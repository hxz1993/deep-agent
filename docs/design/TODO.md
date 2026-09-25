# DeepAgent 设计与研发台账

本文件只保留尚未完成或需要外部条件的事项。已交付能力不再重复拆成待办，历史决策从Git记录恢复。

2026-09-22：用户确认已在线下完成平台附件扩展名白名单（`ATT-CONFIG-001`）与搜索供应商真实 Key 联调（`WEB-PROVIDER-VERIFY-001`），已移出待办；本次未重复执行环境验收。

## 当前交付外事项

| ID | 事项 | 当前边界 | 启动条件 |
| --- | --- | --- | --- |
| `CLIENT-EVENTS-001` | 电子表格助手与DeepCube助手适配Event API | 本仓只审计和交接，不跨仓修改 | 客户端维护方按v0.6.0契约排期 |
| `SCHEDULE-001` | [平台自动化 Agent 任务](26-scheduled-tasks/README.md) | 2026-09-16 调整为平台触发＋后台身份＋Agent 执行＋业务投递；会话为执行记录，新建/复用按场景评估，跨周期去重与聊天记忆分开；未开发 | 以工单清单发群为候选核验触发、凭据与主动发送链路，再决定独立定时入口及任务状态范围 |
| `SANDBOX-001` | [会话级 Sandbox](07-python-sandbox/session-sandbox-design.md) | 活跃会话复用工作目录，支持 Python/Shell；允许到期清理并明确告知；长期目录保留、内存恢复非硬要求；尚未开发 | 验证候选运行时的部署、隔离及生命周期成本，确定有效期与最小验收；不要求用户预先锁定永久存储方案 |
| `OPS-SSE-001` | 部署核对：反向代理不要提前断开或缓冲聊天 SSE | 源于 2026-08-26 / 1f0e9e1（v0.6.0）。应用侧已有约 10 秒压缩进度心跳，摘要默认超时 300 秒；历史建议网关读取超时 330～360 秒并关闭流式缓冲。不是企微 Channel Gateway，也不是新增功能 | 2026-09-22 核对：本仓不含线上生效的反向代理配置，尚不能独立确认部署状态；确认入口网关的超时与流式转发已处理后移除此项 |

## Pydantic AI C 组接入顺序（2026-09-22）

C02 已在 v0.10.18 完成，缓存统计展示已在 v0.10.19 完成。C04 与 C12 已完成；C09 于 2026-09-25 移至后续潜力方向，暂不实施。下表保留其他尚未实施的功能组，不代表已安排开发。

| 顺序 / ID | 功能组 | 实施边界 | 联调与验收 |
| --- | --- | --- | --- |
| 7 / `C10-CODEX` | [Codex 订阅 Provider](28-codex-provider/README.md) | 每个部署环境独立绑定一个授权账号，复用 Provider／模型表关系，Provider 主表认证字段与凭据刷新、模型选择、失效处理；不跨环境共享凭据。远程部署授权另验收 | 登录、重启、刷新、额度不足、模型不可用、工具循环与续跑 |

2026-09-25 最新决定：C09 思考强度暂不实施，现有供应商默认行为已够用，模型与 SDK 持续变化带来的适配维护成本暂不值得投入；移至后续潜力方向，研究归档见 [29 思考强度](29-reasoning-controls/README.md)。C10 已完成本机实验开发，使用者已完成真实账号授权；自然到期刷新、远程部署与多进程仍未验收，客户部署默认不开放。C11 图片生成同属后续潜力方向，不进入当前排期。保留下方已完成的隔离验证记录，后续扩展时重新核验授权方案。

2026-09-24 C10 隔离可行性验证：本机 Pydantic AI 2.46.0 复用现有 Codex CLI 登录态，以 `openai-codex:gpt-6-luna` 完成真实文本调用；随后完成一次 `add(2,3)` 工具循环，返回 `5`，该 Run 报告模型请求 2 次、输入 140／输出 26 Token。此验证未改 DeepAgent 配置和业务数据库，也未覆盖凭据刷新、多进程、平台用户隔离或订阅余量查询；正式接入仍按 C10 行的边界实现。

2026-09-24 C10 独立浏览器 OAuth 验证：本机通过 `OpenAICodexOAuthFlow` 打开官方授权页，在 `localhost:1455` 收到回调；显式将本次凭据传给 `OpenAICodexProvider`，不读取 Codex CLI 登录文件。`gpt-6-luna` 真实请求返回“OAuth连接成功”，请求数为 1。凭据仅保存在该验证进程内，尚未接入 DeepAgent 配置或验证重启后的刷新与复用。

远程部署授权候选方案：Console 发起 OAuth 后保留本次 PKCE verifier 与 state；管理员在自己浏览器完成登录，复制重定向到 `localhost:1455` 后地址栏里的完整回调 URL，粘贴回 Console；后端核对回调地址和 state，再调用 SDK 的 `exchange_code(code)`。这是一次性人工连接步骤，不需要服务器监听 1455 或建立端口转发。若要求免粘贴，可另行验证 Codex 官方设备码授权（Beta，需账号或工作区启用）；当前 DeepAgent 尚未实现或端到端验证。数据结构与实施边界见 [C10 课题](28-codex-provider/README.md)。

2026-09-24 手动回填与持久化隔离验证：本机确认 1455 无监听，在独立验证页手动回填真实回调 URL；错误 state 返回 400，正确回调成功兑换凭据。凭据以 Fernet 加密写入临时 SQLite，随后另起全新 Python 进程，经 `OpenAICodexCredentialSource` 读取并完成 `gpt-6-luna` 真实调用，返回“持久化验证成功”，请求数 1，验证进程正常退出。测试凭据和密钥已删除。该结果验证了无本地回调监听的手动交接，以及跨进程持久化读取；尚未验证真实远端部署的网络、Token 到期刷新、多进程并发刷新，也尚未实现正式 Console/数据库接入。

正式接入的持久化要求：每环境的账号标识及加密 OAuth 凭据保存到该环境的持久数据库，通过 `OpenAICodexCredentialSource.load/save` 读取及写回刷新后的凭据；解密密钥来自独立部署 Secret，不随镜像或容器重建而更换。仅重发代码、重建容器时保留数据库和密钥即可复用授权；凭据丢失、密钥丢失或上游撤销时需重新授权。多进程刷新需应用层协调，SDK 的进程内锁不能替代跨进程互斥；断开账号需阻止新调用、清除数据库凭据及各进程缓存，避免刷新任务重新写入已删除凭据。上述为待实现设计，不能把本机内存验证视为已交付配置功能。

- C01 指令 ID 与 C08 官方 `WarnOnCacheBusts` 暂不接入；`CACHE-STATS` 是独立的产品统计展示，不代表已接入这两项官方能力。
- 本轮排除：C03 单独重接 Harness 事件、C05 标准运行快照导出、C07 参数 JSON 修复、TypeSafe、Copilot、vLLM 专用入口和 C13 远程请求头；未来有明确需求另行决定。
- C06 截断保尾已在 v0.10.17 / 5ad532d 交付，不再排期。
- C04 价格、费用、限额暂不排入上述顺序；用量明细可靠对账后单独设计，金额估算与调用限额分开验收。

## v0.11.0 C04-USAGE 已完成本地审计（2026-09-24）

- 管理员“用量”页与诊断接口已接入，统计聊天、标题、压缩的真实单请求 Usage；按日期、用户、模型、Agent、用途汇总，支持贡献会话下钻及 CSV 导出。网络搜索、图片生成、价格和配额不在本轮范围。
- 事件表新增 `usage_model_id`、`usage` 两列，Alembic 007 只增加列与索引；历史可验证的聊天响应通过独立 dry-run／分批／幂等脚本回填，不增加计量事件行。
- 本地 SQLite 已升级到 007，保留会话和附件；真实标题、主动压缩及压缩后续聊已验收。用量页已完成视觉与交互验收、全量审计和本地提交；本次按用户要求推送至开发分支，不打 tag。接口与验收口径见 [用量诊断指南](../guides/diagnostic-usage.md)。

## v0.10.19 CACHE-STATS 已完成（2026-09-23）

- 展示采用紧凑排版：概览复用上下文区的三列指标；有有效 Usage 数据的轨迹事件在 Summary 后展示“Usage”页签，以文字标签展示该请求的输入／输出／合计、缓存及命中率；Summary 仅保留时间、耗时和 Token 合计的一行摘要。
- 会话／Run 的 `cache_usage` 使用唯一模型响应汇总：缓存读取合计 ÷ 输入合计，不平均各次百分比，缓存不再加到输入分母中。读取、写入分别标记完整性；缺失读取或无效分母时命中率为“—”，部分已知值标注“部分”。
- 新响应 metadata 保存 `deepagent_cache_usage={version:1,read_reported,write_reported}`，只记录缓存字段是否报告；原始 Usage 不改写，不新增表。历史零值无来源证据时按未知处理；已知 Provider 扩展仅按确认过的语义补充映射。
- 现有会话 `1cc83a91ee63448a8721c4bca6ecbda3` 浏览器核对：概览读取 `313,216`／命中率 `66.9%`；最后一次助手“验收完成”（00:55:10，Run `62c51f0b`）输入 `13,658`、输出 `3`、读取 `13,440`、命中率 `98.4%`，未报告写入。普通 Console 与管理员详情一致。
- 用户完成页面评审，按确认后的 Usage 页签与紧凑概览形式发布 v0.10.19。

## v0.10.18 C02 已完成

- `C02-DISPLAY`：保留现有页面布局，修正普通Console与管理员会话详情的数值、来源和进度条；Run累计消耗与上下文预计占用分开，拆分Part不重复累加，缺失值不冒充零。普通外部客户端未跨仓修改。
- `C02-CALIBRATION`：复用Harness公开估算器，以可验证的Provider Usage基准加新增内容估算；修复指令重复计数，模型／历史／参数变化时保守回退。自动、手动和超窗恢复共用计数与压缩链路，输出预留、安全余量及80%／55%规则保持不变；SDK窗口与项目配置对齐。
- 原始Usage和基准指纹随响应保留，不新增表或迁移历史。只读历史重开缺少最终请求参数，仍为`history_only`估算；有效历史基准在此处以`request_parameters_unavailable`回退，真正新请求可重新验证复用。字段与边界见[预算指南](../guides/context-compaction-budget.md)。
- 真实主动压缩验收：预计占用`19,714 → 7,604`，目标`10,701`，实际摘要调用1次（输入`10,268`／输出`1,936`）；压缩后续聊正确保留验收口令、收入／成本／毛利及附件名，真实Usage为输入`13,587`／输出`51`并建立新基准。另验证受保护内容过大时拒绝且不调用摘要模型；日常压缩阈值未修改。

## 后续潜力方向／需要真实需求后再设计

| ID | 方向 | 不预埋的原因 | 重新启动条件 |
| --- | --- | --- | --- |
| `C09-THINKING` | [会话思考强度与模型能力范围](29-reasoning-controls/README.md) | 2026-09-25 用户确认先不做：现有默认行为够用；各供应商、型号的开关／档位／预算及 SDK 映射持续变化，暂不新增接口、UI、字段或适配逻辑 | 出现明确的效果、延迟或成本调节需求，且用户明确恢复后，重新核验 SDK、供应商协议及 models.dev 数据再设计 |
| `C11-IMAGE` | 图片生成与连续编辑 | 2026-09-24 暂不实施：财务与图表优先代码/结构化生成，生成式图片主要用于低频设计探索，暂不新增图片Provider、配置与计费链路 | 出现明确高频业务需求后再恢复；届时独立验证模型配置、文件权限、版本关系与显式参考图编辑 |
| `WEIXIN-CHANNEL-001` | [个人微信 Channel 与 AstrBot 调研](22-wecom-channel/weixin-astrbot-research.md) | 2026-09-23 已核验 AstrBot 微信连续对话落库与渠道查看入口；仅归档，用户明确暂不开发，不新增字段、迁移或 Adapter | 用户明确恢复课题后，确认扫码凭证、平台会话归属、发送边界与游标恢复，再设计实施 |
| `USAGE-LIMITS-001` | [Harness SpendLimits 配额控制](27-personal-usage/README.md) | 2026-09-23 用户确认暂不接；官方能力按 run/会话/日/月等窗口及用户 scope 累计，在下一模型请求前超限抛错，不是每次调用重置，也不是模型选择权限 | 出现明确用户额度需求后，优先验证官方共享计数/并发/时区/取消缺失用量，不在 C04 统计页迭代中启用 |
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
