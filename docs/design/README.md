# 设计课题

本目录按课题首次研究时间排序。编号越大，表示课题越新。每个课题只保留当前有效设计、
必要研究依据和少量历史决策，跨课题未完成事项统一登记在 [`TODO.md`](TODO.md)。

| 序号   | 课题                                              | 首次研究 | 当前状态 |
|------|-------------------------------------------------| --- | --- |
| `01` | [`UI 工具`](01-ui-tools/README.md)                | 2026-06 | builtin/manual 已落地；MCP 自动发现方案已关闭 |
| `02` | [`运维控制台`](02-operations-console/README.md)      | 2026-07 | 一体化 Console 已落地；过期原型已清理 |
| `03` | [`上下文压缩`](03-context-compaction/README.md)      | 2026-07 | v0.11.6 D01：同轮闭合步骤、交接摘要与来源回读，移除55%目标；默认触发仍80% |
| `04` | [`模型目录`](04-model-catalog/README.md)            | 2026-07 | 数据库目录与手工刷新已落地，结构并入 v0.3.0 基线 001 |
| `05` | [`工具优化`](05-tool-optimization/README.md)        | 2026-07 | 工具体系映射、错误语义、Return Schema与v0.4.0大工具结果外置已落地；大型 MCP 检索见 13 课题 |
| `06` | [`附件解析`](06-attachment-parsing/README.md)       | 2026-07 | 第一版按需读取与压缩协同已落地 |
| `07` | [`Python Sandbox 与会话运行环境`](07-python-sandbox/README.md) | 2026-07 起 | 无状态子进程已落地；2026-10-24补充官方Workspace方向、PTC与共享执行、附件导入／输出目录收集／平台保存边界；不新增平台save_file_data，收集时序及后端待定，会话级方案未合入test |
| `08` | [`Artifact 与出站产物`](08-artifact-export/README.md) | 2026-08 | v0.4.0实现会话内大工具结果Artifact；v0.4.18支持主动发布文件到下载中心 |
| `09` | [`结构化输出与 Agent 更新人`](09-structured-output/README.md) | 2026-10-22完整修订 | 默认关闭、单一组合Schema；012一配置表＋Agent开关/更新人；与BOM/租赁两轮确认可接入；0.14.0正式实现及012迁移已完成，安装矩阵/286项逐项验收见课题入口 |
| `10` | [`上下文引用`](10-context-reference/README.md) | 2026-08 | v0.3.5 已完成公共协议；DeepCube/DeepTable业务前端已迁移并验证 |
| `11` | [`工具包与工具目录`](11-tool-packages/README.md) | 2026-08 | v0.3.2 已完成工具包目录、UI 工具包、运行 Toolset 与 Console 统一改造 |
| `12` | [`Agent Skill`](12-agent-skills/README.md) | 2026-08 | v0.3.18 已完成 ToolPackage 分离、上传安装、Harness 渐进披露与资源治理 |
| `13` | [`工具检索与渐进披露`](13-tool-discovery/README.md) | 2026-08 | v0.3.12 已完成词法 Top 3/Top 5、附件条件过滤和 Pydantic AI 渐进披露 |
| `14` | [`框架能力采用基线`](14-framework-capabilities/README.md) | 2026-08 | 框架职责与历史采用边界；本轮Core/Harness升级及A～E分档独立归38课题 |
| `15` | [`浏览器与页面操作`](15-browser-and-page-actions/README.md) | 2026-08 | 区分当前页面 UI Tool、Harness BrowserUse 与 Provider Computer Use；旧 Web 分支不整体合并 |
| `16` | [`PDF 与演示文稿解析`](16-pdf-and-presentation-parsing/README.md) | 2026-08 | 已完成行业与AI Labs调研；独立文档系统工具尚未实施 |
| `17` | [`Console UI`](17-console-ui/README.md) | 2026-08 | 轻量企业控制台视觉体系持续落地；保留真实功能、分栏交互和四主题边界 |
| `18` | [`会话事件与轨迹`](18-session-trajectory/README.md) | 2026-08 | v0.6.0统一Event Store、Event API、持久化SSE、Plan与Console轨迹 |
| `19` | [`Provider与模型分层`](19-model-provider-architecture/README.md) | 2026-08 | v0.5.6已完成两层归一化、007迁移与统一运行门禁 |
| `20` | [`PTC程序编排`](20-code-mode/README.md) | 2026-08 | v0.14.7已接入现有Python／Skill／Web／下载中心双入口，内部完整数据交接与普通大结果保护分开；固定256 MiB／32 MiB／120秒／100次，不加配置项或会话工作区 |
| `21` | [`Web 工具`](21-web-tools/README.md) | 2026-09 | 2026-09-19独立搜索服务、空态兼容及非终止失败结果纳入 v0.10.14；DeepSeek/Kimi管理草稿实测通过 |
| `22` | [`渠道网关与企微 Channel`](22-wecom-channel/README.md) | 2026-09 | v0.7.0公共Gateway/官方SDK Adapter、三表、五个渠道命令与来源标题；002不变 |
| `23` | [`诊断中心与日志`](23-diagnostics-and-logs/README.md) | 2026-09 | 日志、管理员会话查询与统计已发布；保留设计与实施说明，原型已清理 |
| `24` | [`业务助手初始化与升级`](24-assistant-installation/README.md) | 2026-09 | 已由正式 Agent 安装包方案替代；旧稿已清理，保留指南入口 |
| `26` | [`定时任务`](26-scheduled-tasks/README.md) | 2026-09-14 | 上游调研与初步设计；独立配置、Cron、会话归属及停机补跑，尚未开发 |
| `27` | [`个人用量统计`](27-personal-usage/README.md) | 2026-09-23 | C04-USAGE 已在本地实现，版本 0.11.0 / 007；复用事件和 SDK Usage，管理员按人／日期／模型／Agent／用途统计，UI 验收中；价格／配额暂缓 |
| `28` | [`Codex 订阅 Provider`](28-codex-provider/README.md) | 2026-09-24 | C10-CODEX 本机实验版；复用 Provider／模型链路，OAuth 凭据同表 JSON 保存；待真实授权联调 |
| `29` | [`模型原生思考配置与影响面`](29-reasoning-controls/README.md) | 2026-10-31 | C09-THINKING 已恢复研究；原生档位、能力来源、默认层级与接入影响已整理，方案评审中，尚未实施 |
| `30` | [`UI 工具同 Run 等待与执行连续性`](30-ui-tool-inline-wait/README.md) | 2026-09-26 | 已验证官方 handler 保留 PTC 内存；保留 UI 定义与安装格式，运行／SSE／客户端待适配，发布兼容策略待决，尚未实施 |

| `31` | [`敏感凭据数据库加密存储`](31-credential-encryption/README.md) | 2026-09-26 | 高优先级待实施；统一凭据边界、密钥部署、迁移与恢复，无业务客户端升级要求 |

| `32` | [`新渠道接入`](32-new-channels/README.md) | 微信研究始于 2026-09-23；09-26 独立整理 | 微信原调研从 22 移入；企微自建应用公网回调/Gateway 交接；飞书 SDK、扫码与长连接专项调研；个人微信暂缓 |
| `37` | [`文档解析：复杂 PDF 选型与工具化`](37-document-parsing/README.md) | 2026-09-29 | MPDocBench研究；MinerU本地/线上及Paddle官方Demo定向测试；商业API与长文档横评待补，尚未接入产品 |
| `38` | [`Pydantic AI / Harness升级与能力采用`](38-pydantic-ai-harness-upgrade/README.md) | 2026-10-16 | Core 2.46→2.57、Harness 0.32→0.57研究；A～E分档及逐项TODO已记录，升级与新能力尚未实施 |
| `39` | [`Agent 与 Skill 评测`](39-agent-evaluation/README.md) | 2026-11-02 | 三种图片原型已归档：列表工作台、案例与结果双栏、版本对比矩阵；布局待确认，尚未实施 |
| `40` | [图像查看与PDF原页核验](40-image-viewing/README.md) | 2026-11-03 | 纳入 v0.15.2；v0.15.1 基线不含此功能 |
| `41` | [工单助手委派本地仓库排查](41-ticket-repo-investigation/README.md) | 2026-11-03 | 已整理 Codex／DeepSeek Harness 接入、原会话续跑和人工介入方案，尚未实施 |

## 文档命名

- `README.md`：课题入口、当前状态和文档导航。
- `*-research.md`：上游项目、框架能力和实验依据。
- `*-design.md`：当前认可的架构、数据和运行链路。
- `*-history.md`：已废弃方案及废弃原因，不再作为实施依据。
- `TODO.md`：唯一跨课题研发台账，按“待实现、有潜力、暂缓、已关闭、已实现”维护；课题文档不再维护独立待办副本。

## 阅读规则

- 当前使用和接口行为先看 [`../guides/`](../guides/README.md) 与源码。
- 设计文档用于说明决策背景、稳定边界和后续方向，不替代运行说明。
- 历史细节可从 Git 记录恢复，不在当前目录重复保留多份过期全文。
- 框架是否已有等价能力，先查 [`14-framework-capabilities/`](14-framework-capabilities/README.md)；
  只有版本升级或触发条件变化时才增量复审，避免重复调研。
