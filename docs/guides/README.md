# 说明

本目录记录 DeepAgent 当前可使用、可对接或已验证的行为。

设计背景见 [`../design/README.md`](../design/README.md)，未完成事项见
[`../design/TODO.md`](../design/TODO.md)。

## 协议与使用

| 文档 | 用途 |
| --- | --- |
| [`sse-protocol.md`](sse-protocol.md) | 对话 SSE 事件、历史与 Deferred 续跑协议 |
| [`session-event-client-migration.md`](session-event-client-migration.md) | DeepCube、电子表格和通用 AI 客户端迁移到 Session Event 的精确清单 |
| [`ui-tools.md`](ui-tools.md) | 仓库贡献者新增和维护 UI 工具的说明 |
| [`ui-tools-external.md`](ui-tools-external.md) | 集成方使用和配置 UI 工具的说明 |
| [`mcp-resources.md`](mcp-resources.md) | MCP Resource 探测、代理工具和安全边界 |
| [`skills.md`](skills.md) | Skill 安装、Agent 授权、渐进披露、迁移与发布复测 |
| [`console-admin-access.md`](console-admin-access.md) | Console 企业管理员判定、管理API门禁和业务接口边界 |
| [`project-structure.md`](project-structure.md) | 当前项目目录、运行链路和职责边界 |

## 实现经验

| 文档 | 用途 |
| --- | --- |
| [`mysql-idle-connection-timeout.md`](mysql-idle-connection-timeout.md) | MySQL 空闲断链、连接池参数、环境取值和复现验收指南 |
| [`experience/ai-title-generator.md`](experience/ai-title-generator.md) | AI 会话标题生成经验 |
| [`experience/llm-model-modalities.md`](experience/llm-model-modalities.md) | 模型输入模态元数据经验 |
| [`experience/mcp-shared-registry-diagnostics.md`](experience/mcp-shared-registry-diagnostics.md) | MCP Registry 事故排障说明 |
