# 说明

本目录记录 DeepAgent 当前可使用、可对接或已验证的行为。

设计背景见 [`../design/README.md`](../design/README.md)，未完成事项见
[`../design/TODO.md`](../design/TODO.md)。

## 协议与使用

| 文档 | 用途 |
| --- | --- |
| [`agent-mcp-permissions.md`](agent-mcp-permissions.md) | 面向客户的 Agent / MCP 权限、会话隔离及不同接入身份说明 |
| [`platform-integration.md`](platform-integration.md) | 平台接入基础：服务名 URL、Eureka/本地发现、System 与 Account 账号链路 |
| [`sse-protocol.md`](sse-protocol.md) | 对话 SSE 事件、历史与 Deferred 续跑协议 |
| [`session-event-client-migration.md`](session-event-client-migration.md) | DeepCube、电子表格和通用 AI 客户端迁移到 Session Event 的精确清单 |
| [`client-adaptation/README.md`](client-adaptation/README.md) | DeepCube 与 DeepTable 两个外部客户端的逐仓库、逐文件适配说明 |
| [`ui-tools.md`](ui-tools.md) | 仓库贡献者新增和维护 UI 工具的说明 |
| [`ui-tools-external.md`](ui-tools-external.md) | 集成方使用和配置 UI 工具的说明 |
| [`mcp-resources.md`](mcp-resources.md) | MCP Resource 探测、代理工具和安全边界 |
| [`agent-packages.md`](agent-packages.md) | Agent 安装包格式、导出、上传/自带安装与空模型边界 |
| [`skills.md`](skills.md) | Skill 安装、Agent 授权、渐进披露、迁移与发布复测 |
| [`configuration-import.md`](configuration-import.md) | 无会话新环境的同Revision配置预检与初始化接口 |
| [`code-mode.md`](code-mode.md) | Hybrid Code Mode 启用、工具选择、文件变量、边界与排障 |
| [`console-admin-access.md`](console-admin-access.md) | Console 企业管理员判定、管理API门禁和业务接口边界 |
| [`console-ui-guidelines.md`](console-ui-guidelines.md) | Console 页面、弹窗、表单、容器、控件和视觉验收规范 |
| [`message-event-persistence.md`](message-event-persistence.md) | 真实事件及时落库、刷新跟随、必要异常与部署边界 |
| [`wecom-channel.md`](wecom-channel.md) | 渠道网关与企微Adapter部署、五个快捷命令、会话来源和排障 |
| [`feishu-channel.md`](feishu-channel.md) | 飞书扫码、权限、流式回复与媒体发送 |
| [`dingtalk-channel.md`](dingtalk-channel.md) | 钉钉扫码、Stream 接入、默认卡片与消息昵称 |
| [`wecom-app-channel.md`](wecom-app-channel.md) | 企微应用 Webhook 配置、回调地址、011 迁移和联调日志 |
| [`wecom-applications.md`](wecom-applications.md) | 企微应用共享配置、按需成员资料、一周缓存与 006 迁移 |
| [`deploy/jenkins/README.md`](../../deploy/jenkins/README.md) | Jenkins 网关启动版、仅 API 版与只读诊断入口；原始脚本单独保存于 docs/archive/jenkins |
| [`project-structure.md`](project-structure.md) | 当前项目目录、运行链路和职责边界 |

## 实现经验

| 文档 | 用途 |
| --- | --- |
| [`application-references.md`](application-references.md) | 数据库003逻辑引用、发布顺序、迁移回退及验收 |
| [`mysql-idle-connection-timeout.md`](mysql-idle-connection-timeout.md) | MySQL 空闲断链、连接池参数、环境取值和复现验收指南 |
| [`mysql-character-set.md`](mysql-character-set.md) | MySQL utf8mb4 部署要求、Emoji 保存失败、只读检查和修复验收 |
| [`experience/ai-title-generator.md`](experience/ai-title-generator.md) | AI 会话标题生成经验 |
| [`experience/llm-model-modalities.md`](experience/llm-model-modalities.md) | 模型输入模态元数据经验 |
| [`experience/mcp-shared-registry-diagnostics.md`](experience/mcp-shared-registry-diagnostics.md) | MCP Registry 事故排障说明 |
