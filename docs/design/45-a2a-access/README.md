# 45 · A2A 接入

2026-11-12 · 基于 [Dot 开发交接详稿](https://chatgpt.com/space/page_dd86da26ca8c8191b5c07d19ef7ff85f) 的独立课题。当前一期在独立工作树实施，接入和部署见 [A2A 服务端指南](../../guides/agent-service.md)。

[阅读两阶段实施总案](server-implementation-plan.md) · [四种接入方式与功能对照](server-entrypoints.md) · [发布选路、协议操作与联调](server-routing-and-testing.md) · [查阅实测证据与验收矩阵](server-validation.md) · [FastA2A 与官方 SDK 对照](server-fasta2a-comparison.md)

## 页面与交付范围

Console 的「发布 Agent」提供 A2A 和 MCP 接入，外部调用方可以提交任务、跟踪状态、领取产物。「远程 Agent」提供 A2A、Codex 和 CodeBuddy 连接，供本地 Agent 委派任务。

| 区域 | 操作 | 入口 |
| --- | --- | --- |
| 服务端 · 被调用 | 选已有 Agent，填写 Agent 编码和用途，选择全部应用或指定应用。客户端通过路径或 Header 提供本次 App，调用需携带平台凭据。保存后可按需启停。 | 发布 Agent |
| 客户端 · 调用外部 Agent | 配置 Agent Card 或 CLI 的执行环境。指定本地 Agent 可用的连接，直连调试并查看近期调用。 | 远程 Agent |

A2A Agent Card 随所选发布切换。MCP 按环境接入。A2A 和 MCP 共用发布范围、执行权限、任务归属和产物授权。DeepAgent 自有 CLI 和 Python SDK 的设计留待后续评审。

| 入口 | 页面展示 | 调用方拿到什么 |
| --- | --- | --- |
| A2A API | Agent Card、协议版本、鉴权声明、提交与查任务示例 | 标准消息、Task、状态流和 Artifact。 |
| MCP | 连接配置与授权范围、已发布工具、提交／查询／取消示例 | Task ID、任务状态、公开结果及授权文件引用。 |
| DeepAgent CLI（阶段 2） | 安装与凭据配置、提交／查询／等待／取消／下载命令 | 可供 shell 和业务脚本处理的结果及退出状态。建议命令复用 Python SDK。 |
| Python SDK（设计） | 安装与最小代码、提交／查询／等待／取消／下载示例 | 结构化任务与结果、异步状态及可下载产物。 |

**业务验收**用同一份脱敏预算数据，从 Python 和一个真实 MCP 客户端分别提交。客户端可在断开后通过 Task ID 查询，最终可下载并打开 XLSX。这是阶段 2 的业务验收；阶段 1 先用官方 A2A 客户端验证补材料续跑、文件和重启清空近期任务，并回归现有 Console 和聊天入口。

## 发布 Agent

上方左侧管理发布，右侧查看接入方式。下方近期任务默认左右分栏，右侧详情可拖拽调整宽度。发布弹框可切换应用范围，指定应用按标签显示。A2A 提供通用与指定 App 的两条 Card 地址，MCP 提供环境级地址。

[服务端 Console、任务与产物](server-console-design.md)说明发布配置和近期任务如何展示，以及 FileStore 和联调工具如何接入。

## 远程 Agent

首页同屏显示连接和本地 Agent 的近期调用，支持按远程 Agent 和任务状态筛选。打开配置后，可在调试区直接验证连接。

Revision `016` 创建 `agent_publications` 和 `remote_agent_connections`。连接保存后即可调试，在 Agent 编辑页选用，引用存入 `agents.capabilities`。身份、凭据、调用记录和联调见[远程 Agent 实施方案](client-implementation-plan.md)。

## 实施与待确认

- [下一轮开发：最终产物与 MCP 回复](next-iteration-deliverables-and-mcp.md)记录最终文件的明确选择、MCP 追问和输出格式的待确认契约。
- [服务端与客户端实施方案](service-and-client-design.md)涵盖协议选型、现有 Runner 复用、Task 与会话映射、去重、流式订阅、取消、重启边界、产物和分期验收。
- [鉴权与数据访问设计](access-control-design.md)涵盖发布授权、调用身份、下游授权、文件、出站连接和安全验收。
- 补材料续跑纳入阶段 1；独立人审按首个业务确认。其余待决项汇总在[总案](server-implementation-plan.md)。
- [42 号课题](../42-enterprise-auth-adapters/README.md)负责下游 MCP 绑定 App 与凭据适配。[44 号课题](../44-agent-invocation/README.md)继续维护 Python 智能输出及短请求调用。[研发台账](../TODO.md)统一登记评审与实施状态。
