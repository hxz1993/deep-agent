# DeepAgent 设计与研发台账

本文件只保留尚未完成或需要外部条件的事项。已交付能力不再重复拆成待办，历史决策从Git记录恢复。

## 近期发布验收

| ID | 事项 | 待验证 |
| --- | --- | --- |
| `OPS-SSE-001` | Test／Alpha 的 0.17.5 SSE 联调 | 部署后从实际网关入口验证双端能收到提问与流式回复，发起端刷新后原 Run 继续并可重连。核对网关转发与缓冲、读超时及 Run 所在实例。8000 本机已通过双端和刷新验收。 |
| `AGENT-PUBLICATION-CODEBUDDY-A2A-001` | CodeBuddy 原生 A2A 互通 | 验证直连跨轮补答时待补材料 Task 能否保留并续接，以及客户端能否读取业务 JSON。用本机适配器已走通附件、同 Task 补答和下载。[实施验收记录](<../reports/Report [DeepAgent 发布 Agent 实施验收]/README.md>) |

## Agent 运行护栏（2026-11-20）

| ID | 事项 | 状态与下一步 |
| --- | --- | --- |
| `GUARDRAIL-UI-001` | [渠道 UI 工具过滤与文字澄清](46-agent-guardrails/README.md#渠道-ui-工具的兜底) | 已实现。渠道执行时过滤 UI 工具，通过原会话的文字回复澄清。 |

## 原生搜索与原生生图（2026-11-19）

暂缓产品接入。已记录 DeepSeek 搜索、URL 读取与 OpenAI 搜索的直连结果，并保留只读能力展示原型。后续扩大供应商和模型覆盖后，再决定轨迹细节、实际能力维护方式和生图产物链路。详见 [21 课题](21-web-tools/native-search-and-image-generation-research.md)。

## 结构化输出的业务验收

BOM、租赁的完整生产 Schema、客户端适配、人审、业务状态、权限、幂等及业务写入仍待客户端维护方验收。两方已完成设计确认，由用户安排实施。生产发布还需核对 DDL、锁影响和实际部署镜像。

## 框架升级与能力采用：38号课题（2026-10-16）

用户确认独立建题并按分档后续逐项推进。入口：[38 · Pydantic AI / Harness升级与能力采用](38-pydantic-ai-harness-upgrade/README.md)；[完整A～E研究](38-pydantic-ai-harness-upgrade/upstream-review-20261016.md)。基础依赖已升级到 Pydantic AI 2.70.0／Harness 0.70.0／Monty 1.6.0。以下跟踪专题适配及验收的剩余项。

| **顺序 / ID** | **分档与事项** | **状态** | **前置条件 / 下一步及验收** |
| --- | --- | --- | --- |
| 2 / `UP26-SKILLS` | B2～B4：Skill Workspace、异步安装校验与调用语义 | 待核对 | 在当前依赖基线上，适配受信只读Skill库、显式异步校验；验证安装、首Run、缺失/畸形文件及选中范围，保留按需加载 |
| 3 / `UP26-PTC` | B5：HybridCodeMode与Monty1.x兼容 | 待核对 | 在当前依赖基线上，核对自定义包装、动态指令、资源预算与文件不透明中转；不开启提前执行、宿主访问或跨Run存储 |
| 4 / `UP26-HOOKS-HISTORY` | B6～B8：Hook生命周期、消息往返与外围行为 | 待实现 | 核验真实请求计时、请求视图/持久历史分离、SSE/History、取消、搜索固定路由及Provider默认行为 |
| 5 / `UP26-UPGRADE-ACCEPTANCE` | A组＋B组：基础升级整体验收 | 待实现 | 确认第 2～4 项的剩余覆盖，重点回归各 Provider 的流式工具续跑、UI 重复回传和 80 次预算 |
| 6 / `UP26-REPL-STATE` | C1：PTC跨Run变量与草稿小样 | 有潜力，优先验证 | 基础升级通过后，验证repl_store、会话隔离、过期/损坏/保存失败阻断、修订冲突及程序生成最终JSON；不把REPL快照直接当业务文件 |
| 7 / `UP26-DECISION-ROUTING` | C2：有限候选决策路由小样 | 有潜力，待验证 | 基础升级及可用决策端点具备后，对BOM Sheet做独立答案对照，报告误选/漏选、总延迟、请求数及Token；网关404或订阅权限另行核验 |
| 8 / `UP26-CACHE` | C3：Prompt缓存及缓存失效诊断 | 有潜力，待验证 | 在固定Provider/模型上验证连续多轮、Skill/工具目录变化及压缩后的实际缓存数据，不全局发送不受支持字段 |
| 后续 / `UP26-BACKGROUND` | D1：后台工具编排 | 暂缓 | 有独立长只读工作时再设计，先解决完成消息归属、结果限额、文件授权、计时和取消，不直接批量开启 |
| 后续 / `UP26-MCP-APPS` | D3：MCP Apps | 暂缓 | 明确客户端需求后再设计资源代理、iframe/CSP、工具鉴权及历史恢复，不直接替换现有UI工具 |
| 后续 / `UP26-ATTEMPT-DIAGNOSTICS` | D4：失败尝试诊断 | 暂缓 | 有明确回退/重试耗时问题时验证实际请求记录和展示，不因此新增默认Fallback模型 |

D2本地沙盒并入下方既有`SANDBOX-001`，补充Smol/Bubblewrap/SSH候选，不重复立项。E组不列实施任务；此前D02提前执行、D03监督模型、D05持久执行已关闭，保持原决定。

## 优先事项（2026-09-26）

以下两项由用户明确列为后续优先课题；列前不代表已批准立即实施。

| 顺序 / ID | 课题 | 当前状态与下一步 |
| --- | --- | --- |
| 1 / `CREDENTIAL-ENCRYPTION-001` | [敏感凭据数据库加密存储](31-credential-encryption/README.md) | 尚未实施。统一覆盖 API Key、OAuth Token、机器人及应用 Secret；先确认密钥部署、旧数据迁移、轮换和备份恢复。只需服务端与部署配合，无业务客户端升级要求 |
| 2 / `UI-INLINE-WAIT-001` | [UI 工具同 Run 等待与执行连续性](30-ui-tool-inline-wait/README.md) | 已有设计及 SDK/PTC 隔离验证，尚未实施。保留 UI 工具和 Agent 安装定义，改等待/答案回传/取消链路；Console 与业务客户端需同步适配。兼容与协调升级方案仍由用户决策，不默认双模式 |

2026-11-20 补充待评审。复现 UI 工具嵌入工作流时，返回值格式过严、回传缺失或不符合要求导致流程无法继续的场景。核对结果校验、等待状态和客户端回传分别怎样影响流程，再评估普通结果可放宽到什么程度，以及无法回传或结果无效时如何向工作流返回明确结果。真实审批仍须收到合法确认。此项留待后续评审。

## 当前交付外事项

| ID | 事项 | 当前边界 | 启动条件 |
| --- | --- | --- | --- |
| `WEB-DEEPSEEK-TOOLS-001` | [DeepSeek Search 与 Fetch 的 Alpha 部署核验](21-web-tools/web-search-fetch-20261016-validation.md) | 业务链路已验收 | 在目标 Alpha 环境核对 Search 请求和 Fetch 路由 |
| `CLIENT-EVENTS-001` | 电子表格助手与DeepCube助手适配Event API | 本仓只审计和交接，不跨仓修改 | 客户端维护方按v0.6.0契约排期 |
| `SCHEDULE-001` | [平台自动化 Agent 任务](26-scheduled-tasks/README.md) | 2026-09-16 调整为平台触发＋后台身份＋Agent 执行＋业务投递；会话为执行记录，新建/复用按场景评估，跨周期去重与聊天记忆分开；未开发 | 以工单清单发群为候选核验触发、凭据与主动发送链路，再决定独立定时入口及任务状态范围 |
| `SANDBOX-001` | [会话级 Sandbox](07-python-sandbox/session-sandbox-design.md) | 2026-10-24更新：本地／自托管方向，以新版Pydantic AI Workspace规范接入；Python／Skill／Shell共用环境，PTC按需编排。附件导入、输出目录收集由服务端统一负责；中间文件留工作区，明确成果存FileStore，下载中心登记独立；不新增平台save_file_data。候选分支未合入test及验收 | 结合[38课题D2](38-pydantic-ai-harness-upgrade/upstream-review-20261016.md)验证后端和依赖组合；确定执行／PTC收集边界、旧入口兼容、授权、过期、取消和资源成本；长期目录及内存恢复非硬要求，不接Modal云服务，本轮只更新文档 |

## 订阅 Provider 部署验收

- `C10-CODEX`：[Codex 订阅 Provider](28-codex-provider/README.md) 已接入并经本地实测。自然到期刷新、远程环境登录与多进程凭据刷新仍需验证。各环境使用各自授权账号，客户部署默认不开放。

## 渠道下一步（[32 独立课题](32-new-channels/README.md)）

- `FEISHU-CHANNEL-001`：[飞书渠道完整调研](32-new-channels/feishu-channel-research.md)。2026-09-27 已核对官方 SDK/PyPI 包与 OpenClaw、AstrBot：建议企业自建应用＋WebSocket，不依赖公网回调放行；提供手填与官方扫码注册两入口，先验证官方高层 Channel SDK 的持久接纳和生命周期适配。用户已批准实施，已完成官方 SDK 独立适配器、扫码／手填配置和企微回归，见[验收记录](../reports/20260927-feishu-channel/README.md)；真实租户扫码授权与收发待账号验收。

- `PUBLIC-CALLBACK-001`：[公网平台回调与 Gateway 放行](32-new-channels/public-callback-gateway-design.md)。2026-09-27 已核验 Gateway 源码：现有硬编码免登录机制可扩展为受控路由配置；需统一过滤链、固定目标、隔离身份头并脱敏日志。企微自建应用另需接收端验签和企业主体域名。先交平台开发评审，未实施；不做服务自行注册免登录接口。个人微信暂不开发，公众号暂停讨论。

[飞书、微信与 AstrBot 复盘](32-new-channels/channel-expansion-review.md)：企微已有，飞书待真实租户验收，个人微信暂不开发。微信已有专项讨论；飞书 09-27 已批准长连接方案；沿用公共 Gateway，不接入 AstrBot 作为第二套运行时。优先明确会话隔离、使用者身份、凭据生命周期、回复能力和去重恢复。

## 中间产物管理：重点方案评审（2026-11-07）

用户明确希望落地，已授权整理交接和继续研究，尚未批准具体实施或数据库结构。沿用原课题及 ID，不另建重复事项。

| **ID** | **方向** | **已确认／当前状态** | **下一步与门槛** |
| --- | --- | --- | --- |
| `REPORT-PIPELINE-001` | [中间产物管理与报告流水线](43-budget-report-pipeline/README.md) | 财务场景沿用 EPMS 共享 Agent。个人方法经审核进入团队模板或固定业务能力，程序校验正式结果。可选产物模块尚未实施 | 用一份 OPP 脱敏样例评审品牌月结与差异下钻，确认模板维护、数据权限和对账口径。评审草稿跨端读写与修订，数据库结构另行确认 |

## 组件场景与 Agent 扩展配置（2026-11-22）

`AGENT-SCENARIO-CONFIG-001` 处于探索阶段，现阶段维持线上行为。当前 `integration_settings` 保存组件场景与 Agent 的绑定，`integration_scoped_settings` 保存电子表格场景的应用级追加提示词和 Skill 引用。运行时按场景绑定的 Agent 和调用的 App、Space 取配置，因此相关配置也可能被渠道或 MCP、A2A 发布命中。Alpha 已观察到飞书会话加载专项提示词，OP 及其他环境的实际依赖情况待核对。

- **明确配置归属。** 合同导入、BOM 导入、分摊等定制入口可采用固定界面、提示词、Skill 和 MCP。可复用的 Agent 保留稳定基础能力，按入口和任务装配所需扩展，避免把多个助手的业务规则都放进同一份 Agent 提示词。「专用」和「通用」先作为配置与复用方式讨论，Agent 类型字段留待评审。
- **按真实场景定义扩展。** 组件场景负责总入口，可以绑定一个 Agent，也可以封装多个 Agent。按场景决定提示词追加或替换、Skill 和 MCP 组合、规则表及知识库等配置的使用方式。通过集成编码进入既有 Chat 消息与 SSE 契约是候选方向，续聊须能定位原场景。
- **验证跨入口复用。** 评估由组件、飞书等消息渠道及 MCP／A2A 发布显式选用同一业务扩展。复用业务口径与工具配置时，分别核对入口所需的文件、澄清、结果交付和身份权限。先用同一 Agent 绑定两个助手验证规则隔离，再用一个需要跨入口的实际场景验证复用。
- **评审存储和兼容。** 候选方案保留 `integration_settings` 的路由职责，评估能否复用 `integration_scoped_settings` 承载扩展，或需要拆开场景配置与可复用的 Agent 扩展。先按环境盘点现有记录、Skill 引用及命中入口，再比较兼容、保留与迁移方案。任何数据修复或表结构变更另行评审和授权。

## 后续潜力方向／需要真实需求后再设计

| ID | 方向 | 不预埋的原因 | 重新启动条件 |
| --- | --- | --- | --- |
| `TICKET-REPO-001` | [工单助手委派本地仓库排查](41-ticket-repo-investigation/README.md) | 2026-11-04 补充 Autoloop／Symphony／SuperAgent 的部署与复用边界；内部先用、少改造，当前为方案研究 | 先确认执行机系统与网络，选一条只读工单验证 Autoloop 的提交、补料、原 thread 恢复、结果回读及隔离；不适用再评估其他执行服务 |
| `ENTERPRISE-AUTH-001` | [企业集成与鉴权适配器](42-enterprise-auth-adapters/README.md) | [MCP 绑定应用](42-enterprise-auth-adapters/mcp-bound-app.md) 已实现并完成本地验证 | 统一适配器的客户账号映射、换票、配置及执行期通信待单独评审 |
| `AGENT-INVOCATION-001` | [智能输出与 Agent 对外调用](44-agent-invocation/README.md) | 11-09 归档 Python 按次生成、运行固定 Agent、内网发布 MCP 工具三种调用形态。新能力待评审 | 用审批意见与只读分析 Agent 验证接口和返回契约，确认调用身份及授权范围，按接入方实际需求决定 OpenAI 兼容入口与长任务协议 |
| `C11-IMAGE` | 图片生成与连续编辑 | 2026-09-24 暂不实施：财务与图表优先代码/结构化生成，生成式图片主要用于低频设计探索，暂不新增图片Provider、配置与计费链路 | 出现明确高频业务需求后再恢复；届时独立验证模型配置、文件权限、版本关系与显式参考图编辑 |
| `D04-CHOICES` | 动态候选集与结构化选择 | 2026-09-25 用户确认当前不接，保留潜力记录：Choices 是应用显式构造的动态枚举类型，Harness 无法自动推断前后 MCP 工具的业务关联；通用 Agent 不为此增加候选绑定、固定分流或业务工具封装。已有 enum 继续沿用，MCP 入参校验属于独立改进，不随此项实施 | 出现明确、局部、可由程序可靠取得候选集的结构化输出或封装流程，且用户明确恢复后再评估；不对全部 MCP 工具做动态 Choices 改造 |
| `D06-SANDBOX-REFERENCE` | [官方沙盒接口与工具设计参考](07-python-sandbox/session-sandbox-design.md#d06-后续吸收范围2026-09-25) | 2026-09-25 用户确认列入潜力：本地／自托管沙盒沿 `SANDBOX-001` 推进，不接官方 Modal 云后端；参考 Harness 的会话复用、FileSystem/Shell 分工、命令超时/取消、文件事件与读取上限，不另建一套沙盒 | 本地沙盒方案设计时核对接口和生命周期，落地后按实际缺口补齐工具能力；保留会话权限、FileStore 和产物发布，不因复用而开放宿主 Shell |
| `D07-VOICE-REFERENCE` | 实时语音能力的跨项目参考 | 2026-09-25 用户明确本 DeepAgent 项目不做；仅保留 Core Realtime、录音转写与实时通话的研究线索，不预埋接口或配置 | 未来其他项目明确需要语音时，在该项目单独评估；不自动转为本项目研发任务 |
| `CHANNEL-TARGETS-001` | 渠道目标发现与跨会话发送 | 2026-09-28 用户确认暂不做：当前 Message 只发送触发本轮的群或私聊，不建设通讯录、目标选择或授权管理界面 | 出现按名称找人、指定其他群或后台主动通知需求后，先明确目标来源、主体授权和重名处理，再评估独立 targets 工具或配置入口 |
| `CHANNEL-HISTORY-001` | 渠道平台消息读取 | 2026-09-28 用户确认暂缓：已有会话及运行过程从 Agent Event Store 读取，不为现有对话重复建设 history 工具 | 确需读取未进入 Agent 会话的平台消息时，再核验各渠道接口、权限和可见范围；与本地历史明确区分 |
| `CHANNEL-VOICE-STT-001` | 渠道语音消息转文字 | 2026-09-27 用户确认暂缓：启用开关、服务配置与成本仍需设计；现阶段用户可在客户端使用语音输入／转文字后发送文本，不新增接口或配置。自部署涉及硬件和运维，不作为首选 | 用户明确恢复后优先评估在线 ASR，在进入 Agent 前固定转写，不依赖模型调用工具；再确定凭据、费用、时长限制和失败提示。实时通话与语音合成不在本项范围内 |
| `WEIXIN-CHANNEL-001` | [个人微信 Channel 与 AstrBot 调研](32-new-channels/weixin-astrbot-research.md) | 2026-09-23 已核验 AstrBot 微信连续对话落库与渠道查看入口；仅归档，用户明确暂不开发，不新增字段、迁移或 Adapter | 用户明确恢复课题后，确认扫码凭证、平台会话归属、发送边界与游标恢复，再设计实施 |
| `USAGE-LIMITS-001` | [Harness SpendLimits 配额控制](27-personal-usage/README.md) | 2026-09-23 用户确认暂不接；官方能力按 run/会话/日/月等窗口及用户 scope 累计，在下一模型请求前超限抛错，不是每次调用重置，也不是模型选择权限 | 出现明确用户额度需求后，优先验证官方共享计数/并发/时区/取消缺失用量，不在 C04 统计页迭代中启用 |
| `SESSION-BRANCH-001` | Branch From | 父链读取与复制前缀会产生不同DDL和删除语义 | 产品确定交互、数据保留和分支删除规则 |
| `SESSION-RESTORE-001` | Revision 007少量会话恢复 | 通用导入器会扩大旧格式兼容面 | 用户给出明确Session ID后编写一次性Dry Run脚本 |
| `SEARCH-INDEX-001` | SQLite FTS5、PostgreSQL GIN或MySQL FULLTEXT | 当前有界应用层中英文评分已可用，三种方言索引方案不同 | 真实事件量和查询计划证明64K Search Text扫描成为瓶颈 |
| `MEMORY-001` | 跨会话长期记忆 | History Search不是可编辑长期记忆；namespace也不是授权 | 产品明确查看、编辑、删除、审计和User/Agent/App/Space边界 |
| `PLAN-GLOBAL-001` | 跨Turn可编辑全局计划 | 当前Plan是给用户看的Turn内完整快照，不是执行调度事实源 | 产品需要跨消息人工编辑、继续或共享计划 |
| `TOOL-APPROVAL-001` | 写入/删除/外发工具审批 | 需要业务权限、操作人、TTL、审计和幂等共同设计 | 出现明确高风险工具与审批责任方 |
| `DOCUMENT-001` | [复杂文档后续能力](37-document-parsing/README.md) | 长期缓存、语义搜索、新 cursor 和业务金额纠错仍为候选 | 按具体文档场景和质量缺口独立评审 |
| `WECOM-HA-001` | 企微Channel多副本、主动消息与用户级身份映射 | v0.7.0只承诺单Gateway副本、被动回复和管理员配置的会话用户，不预埋Lease/Outbox/Identity表 | 出现多副本、可靠主动通知或企微用户差异权限的真实需求 |

框架能力是否采用的长期原则见
[`14-framework-capabilities/framework-capabilities-baseline.md`](14-framework-capabilities/framework-capabilities-baseline.md)。
