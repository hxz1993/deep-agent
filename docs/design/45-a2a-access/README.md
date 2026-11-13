# 45 · A2A 接入

2026-11-12 · 基于 [Dot 开发交接详稿](https://chatgpt.com/space/page_dd86da26ca8c8191b5c07d19ef7ff85f) 的独立课题。当前一期在独立工作树实施，接入和部署见 [A2A 服务端指南](../../guides/agent-service.md)。

[阅读两阶段实施总案](server-implementation-plan.md) · [四种接入方式与功能对照](server-entrypoints.md) · [发布选路、协议操作与联调](server-routing-and-testing.md) · [查阅实测证据与验收矩阵](server-validation.md)

## 页面与交付范围

首期 Console 增加「集成」页，A2A 是首个对外调用协议。后续如需让 DeepAgent 主动委托外部 Agent，再另设客户端页。首期先让外部 Agent 和普通程序使用已发布的业务能力，例如提交预算差异报告任务、跟踪状态、领取 XLSX。

| 区域 | 操作 | 交付顺序 |
| --- | --- | --- |
| 服务端 · 被调用 | 选已有 Agent，填写发布编码和用途，选择固定应用或调用方指定。调用方携带平台凭据，选择调用方指定时还需携带 App ID。保存后可按需启停。 | 阶段 1 实施 A2A 1.0 和服务端 Console；阶段 2 再实现 MCP、CLI、Python SDK，三者复用同一任务服务。 |
| 客户端 · 调用外部 Agent | 登记远端 Agent Card，核验协议、接口和认证方式，授权指定内部 Agent，测试连接，查看远端任务状态与产物。 | 后续单独成页。以第一个真实外部 Agent 为联调目标。 |

原型右侧有四张接入卡。A2A Agent Card 随所选发布切换，MCP、CLI 和 Python SDK 按环境接入。首版用官方 A2A CLI 联调标准接口。DeepAgent 自有 CLI、MCP 和 Python SDK 属于阶段 2。发布范围、执行权限、任务归属和产物授权共用。

| 入口 | 页面展示 | 调用方拿到什么 |
| --- | --- | --- |
| A2A API | Agent Card、协议版本、鉴权声明、提交与查任务示例 | 标准消息、Task、状态流和 Artifact。 |
| MCP | 连接配置与授权范围、已发布工具、提交／查询／取消示例 | Task ID、任务状态、公开结果及授权文件引用。 |
| DeepAgent CLI（阶段 2） | 安装与凭据配置、提交／查询／等待／取消／下载命令 | 可供 shell 和业务脚本处理的结果及退出状态。建议命令复用 Python SDK。 |
| Python SDK | 安装与最小代码、提交／查询／等待／取消／下载示例 | 结构化任务与结果、异步状态及可下载产物。 |

**业务验收**用同一份脱敏预算数据，从 Python 和一个真实 MCP 客户端分别提交。客户端可在断开后通过 Task ID 查询，最终可下载并打开 XLSX。这是阶段 2 的业务验收；阶段 1 先用官方 A2A 客户端验证补材料续跑、文件和重启清空近期任务，并回归现有 Console 和聊天入口。

## 首期服务端 HTML 原型

[打开集成原型](<Prototype A2A服务端接入/index.html>)。上方左侧管理发布，右侧横排四张接入卡。下方近期任务默认左右分栏，右侧详情可拖拽调整宽度。发布弹框右侧切换应用范围，指定应用按标签显示。A2A 提供 Card 地址和简要用法，其他三种入口先占位。

[服务端 Console、任务与产物](server-console-design.md)说明发布配置和近期任务如何展示，以及 FileStore 和联调工具如何接入。

## 早期布局备选

| 方案 | 布局 | 适用情况 |
| --- | --- | --- |
| [A · 上下分区](assets/a-stacked.png) | 上部是发布、权限和接入方式，下部是外部 Agent 连接。 | 首期以服务端为主，后续能力在同一页有清晰位置。 |
| [B · 左右双栏](assets/b-two-columns.png) | 左侧发布及四个入口，右侧外部连接列表与表单。 | 两种角色都需要频繁配置时，便于并排操作。 |
| [C · 任务工作台](assets/c-task-workbench.png) | 主区放发布详情、四个入口与任务列表，右侧放外部连接。 | 运营需要持续查看任务、失败状态和产物时。 |

以下 PNG 保留作为早期布局对照，首期以服务端 HTML 为准。三个 PNG 均为 ImageGen 静态设计稿，图中的 URL、命令、认证值、输出格式、版本和已发布状态仅作视觉示意。对接以锁定的协议和正式配置为准。

## 实施与待确认

- [服务端与客户端实施方案](service-and-client-design.md)涵盖协议选型、现有 Runner 复用、Task 与会话映射、去重、流式订阅、取消、重启边界、产物和分期验收。
- [鉴权与数据访问设计](access-control-design.md)涵盖发布授权、调用身份、下游授权、文件、出站连接和安全验收。
- 补材料续跑纳入阶段 1；独立人审按首个业务确认。其余待决项汇总在[总案](server-implementation-plan.md)。
- [42 号课题](../42-enterprise-auth-adapters/README.md)负责下游 MCP 绑定 App 与凭据适配。[44 号课题](../44-agent-invocation/README.md)继续维护 Python 智能输出及短请求调用。[研发台账](../TODO.md)统一登记评审与实施状态。
