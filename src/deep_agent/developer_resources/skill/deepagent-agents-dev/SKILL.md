---
name: deepagent-agents-dev
description: 配置、开发和排查 DeepAgent 业务助手，包括模型与MCP、业务Skill、电子表格、结构化流程及会话诊断。没有框架源码时通过服务接口工作；有源码时支持后端、Console及业务客户端开发。
---

# DeepAgent 业务助手开发

本 Skill 在 Coding Agent 中使用。首次安装和连接读 [README](README.md)；资料基线 **{{DEEP_AGENT_VERSION}} / 数据库 015**，目标版本和接口以 `/info`、`/openapi.json` 为准。

先确定目标服务、Agent 和任务范围，再从下表选择资料。只读相关章节，不一次加载整个包。配置和排障无需取得框架源码。

| 任务 | 按需读取 |
| --- | --- |
| 连接、授权、权限错误 | [连接与授权](references/01-connect-and-permissions.md) |
| 模型、MCP、Agent 配置 | [配置 Agent](references/02-configure-agent.md) |
| 提示词与业务上下文 | [提示词与上下文](references/03-prompt-and-context.md) |
| Skill、Python、多模块和文件产物 | [脚本与文件](references/04-skill-python-files.md) |
| Chat、SSE、附件、历史与续跑 | [客户端调用](references/05-chat-and-session.md) |
| BOM、合同等 JSON 与人审流程 | [结构化流程](references/06-structured-workflows.md) |
| 电子表格选区、分析、填充和图表 | [电子表格助手](references/07-spreadsheet-assistant.md) |
| 文档、OCR、图片及 PDF 核验 | [文档与视觉](references/08-document-and-vision.md) |
| 会话 ID、轨迹与文件导出、日志及断流排查 | [会话诊断](references/09-session-diagnostics.md) |
| Agent/Skill 迁移与交付 | [安装与验收](references/10-install-test-handoff.md) |
| 查找接口与版本依据 | [接口索引](references/11-api-and-version.md) |
| 修改 DeepAgent 源码或内置 Console | [后端开发](references/source/backend/index.md) |
| 修改调用 DeepAgent 的业务客户端 | [前端开发](references/source/frontend/index.md) |

## 关键边界

- 使用可信当前身份；`context_refs`、模型参数和 `DEEP_AGENT_CONTEXT` 不是认证凭据。认证信息只保存在 Coding 工具私有配置中。
- Skill 关联不自动授权 MCP、Python 或文档解析；PTC 仅能调用已授权且披露的工具，图片查看在外层完成。
- 平台结构化接受、客户端采用、人类批准和业务保存分别验证。格式失败在同一 Turn 内纠正，不另发请求重置预算；读取历史不重跑业务。
- 配置走正式 API，修改前读取共享影响，保存后回读。排障默认只读；删除、覆盖、业务写入和部署依当前任务授权，保留无关配置与历史数据。
- 交付说明实际版本、变更、结果入口及未验证项。临时证据标记 `TMP to delete`，不包含凭据或整段客户数据。
