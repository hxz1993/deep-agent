# 设计课题

本目录按课题首次研究时间排序。编号越大，表示课题越新。每个课题只保留当前有效设计、
必要研究依据和少量历史决策，跨课题未完成事项统一登记在 [`TODO.md`](TODO.md)。

| 序号   | 课题                                              | 首次研究 | 当前状态 |
|------|-------------------------------------------------| --- | --- |
| `01` | [`UI 工具`](01-ui-tools/README.md)                | 2026-06 | builtin/manual 已落地；MCP 自动发现方案已关闭 |
| `02` | [`运维控制台`](02-operations-console/README.md)      | 2026-07 | 一体化 Console 已落地，保留交互原型 |
| `03` | [`上下文压缩`](03-context-compaction/README.md)      | 2026-07 | v0.6.0单Event压缩、八段摘要、Evidence、超大结果裁剪和历史回读已落地 |
| `04` | [`模型目录`](04-model-catalog/README.md)            | 2026-07 | 数据库目录与手工刷新已落地，结构并入 v0.3.0 基线 001 |
| `05` | [`工具优化`](05-tool-optimization/README.md)        | 2026-07 | 工具体系映射、错误语义、Return Schema与v0.4.0大工具结果外置已落地；大型 MCP 检索见 13 课题 |
| `06` | [`附件解析`](06-attachment-parsing/README.md)       | 2026-07 | 第一版按需读取与压缩协同已落地 |
| `07` | [`Python Sandbox`](07-python-sandbox/README.md) | 2026-07 起 | A 已落地（subprocess-dev + soft guardrails）；B 探针代码已有、节点选型未完成；C 阻塞于 B |
| `08` | [`Artifact 与出站产物`](08-artifact-export/README.md) | 2026-08 | v0.4.0实现会话内大工具结果Artifact；v0.4.18支持主动发布文件到下载中心 |
| `09` | [`结构化最终输出`](09-structured-output/README.md) | 2026-08 | 0805 实验实现已归档；产品边界未锁定，当前暂缓正式实施 |
| `10` | [`上下文引用`](10-context-reference/README.md) | 2026-08 | v0.3.5 已完成公共协议；DeepCube/DeepTable业务前端已迁移并验证 |
| `11` | [`工具包与工具目录`](11-tool-packages/README.md) | 2026-08 | v0.3.2 已完成工具包目录、UI 工具包、运行 Toolset 与 Console 统一改造 |
| `12` | [`Agent Skill`](12-agent-skills/README.md) | 2026-08 | v0.3.18 已完成 ToolPackage 分离、上传安装、Harness 渐进披露与资源治理 |
| `13` | [`工具检索与渐进披露`](13-tool-discovery/README.md) | 2026-08 | v0.3.12 已完成词法 Top 3/Top 5、附件条件过滤和 Pydantic AI 渐进披露 |
| `14` | [`框架能力采用基线`](14-framework-capabilities/README.md) | 2026-08 | 当前固定审计 Pydantic AI 2.35.1 与 Harness 0.26.0；记录已采用、暂缓和不采用边界 |
| `15` | [`浏览器与页面操作`](15-browser-and-page-actions/README.md) | 2026-08 | 区分当前页面 UI Tool、Harness BrowserUse 与 Provider Computer Use；旧 Web 分支不整体合并 |
| `16` | [`PDF 与演示文稿解析`](16-pdf-and-presentation-parsing/README.md) | 2026-08 | 已完成行业与AI Labs调研；独立文档系统工具尚未实施 |
| `17` | [`Console UI`](17-console-ui/README.md) | 2026-08 | 轻量企业控制台视觉体系持续落地；保留真实功能、分栏交互和四主题边界 |
| `18` | [`会话事件与轨迹`](18-session-trajectory/README.md) | 2026-08 | v0.6.0统一Event Store、Event API、持久化SSE、Plan与Console轨迹 |
| `19` | [`Provider与模型分层`](19-model-provider-architecture/README.md) | 2026-08 | v0.5.6已完成两层归一化、007迁移与统一运行门禁 |
| `20` | [`PTC程序编排`](20-code-mode/README.md) | 2026-08 | v0.6.7规范化`builtin:ptc`，保留MCP原生+`run_code`双通道，并支持URL/File ID不透明中转 |
| `21` | [`Web 工具`](21-web-tools/README.md) | 2026-09 | v0.6.6显式`builtin:web`、搜索路由、SSRF-safe Fetch与按需Web图片读取已实现 |
| `22` | [`渠道网关与企微 Channel`](22-wecom-channel/README.md) | 2026-09 | v0.7.0公共Gateway/官方SDK Adapter、三表、五个渠道命令与来源标题；002不变 |
| `23` | [`诊断中心与日志`](23-diagnostics-and-logs/README.md) | 2026-09 | 四种导航布局与日志、会话排查、会话统计的可混搭 HTML 原型；管理态会话能力待确认 |
| `24` | [`业务助手初始化与升级`](24-assistant-installation/README.md) | 2026-09 | 讨论稿：固定 ID、业务发行包、管理员安装升级与 Skill File Store；未实施 |

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
