# DeepAgent 后端与内置 Console 开发

本章用于已取得 DeepAgent 源码且需要修改的任务；没有源码时使用[业务接口指南](../../11-api-and-version.md)。内置 `console/` 归后端仓库，外部业务客户端见[前端开发](../frontend/index.md)。资料基线为 **{{DEEP_AGENT_VERSION}} / 数据库 016**，操作前核对目标 `/info`、OpenAPI、仓库 AGENTS、分支和已有改动。

## 按任务读取

| 任务 | 参考 |
| --- | --- |
| 模块职责、启动与数据流 | [后端架构](references/backend-architecture.md) |
| 本地服务、API、SSE 检查 | [运行与接口](references/local-run-and-api.md) |
| ToolPackage、MCP、Skill、Python、文件 | [工具与运行时](references/tool-packages-and-runtime.md) |
| 严格输出、格式纠错、业务接受 | [结构化输出](references/structured-output.md) |
| UI Tool、Deferred、SSE 与 History | [交互与历史](references/ui-tools-sse-history.md) |
| 上下文引用、预算与压缩 | [上下文](references/context-compaction-and-references.md) |
| Console 页面、样式与浏览器 | [Console](references/console-and-admin-frontend.md) |
| Session ID、Events、日志证据 | [诊断](references/session-diagnostics.md) |
| 企微与飞书渠道、平台 owner 与成员 | [渠道](references/wecom-channels-and-applications.md) |
| Agent 整包导出、安装、覆盖 | [安装迁移](references/agent-packages-and-migration.md) |
| 删除与共享资源影响 | [资源生命周期](references/agent-resource-lifecycle.md) |
| 数据库迁移、配置备份 | [数据库](references/database-migrations-and-backups.md) |
| 验证范围与交接 | [测试](references/testing-and-handoff.md) |

## 开发要点

- 先在实际失败边界复现，再修改负责该行为的层。配置任务走正式 API，不直接写数据库；只读排障不触发启动、迁移或重放。
- 沿用已有本地 Profile、数据库、会话与附件；依赖、数据库结构和部署变更单独评估，不能启动时自动安装依赖。
- `chat_sessions + chat_session_events` 是会话事实源。SSE、History 与轨迹使用投影，压缩不改写原事件；Model 是运行事实，model_catalog 仅供目录浏览/预填。
- Agent 的 `capabilities`、`skills` 为显式列表；时间工具默认运行时注入。Python/Skill 共用授权文件与 File ID，`view_image` 在 PTC 外查看；`include_context=true` 才注入无凭据上下文，见[脚本指南](../../04-skill-python-files.md)。
- 回归按实际影响选择，纯文案/布局不扩成完整后端矩阵。保留无关改动与用户数据，自建进程/浏览器清理；提交、推送和部署遵从当前任务授权。
