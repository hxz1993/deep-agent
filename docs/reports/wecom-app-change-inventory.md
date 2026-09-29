# 企微应用改造范围核对

核对日期：2026-09-29。比较基线：`f7f821f02404bb177d873b9b0eab828492278313`。包含已跟踪文件差异及未跟踪新文件；不把历史已提交的来源改造、模型分组和用量 UI 改造重复算入本轮。

## 功能改动

| 范围 | 实际改动 |
| --- | --- |
| 渠道配置 | 增加 wecom_app/webhook，引用已有企微应用；独立 Token、EncodingAESKey；掩码与保留/替换；应用重复关联提示及删除引用保护 |
| 公共回调 | GET 验证并返回解密 echostr；POST 验签解密后入队并返回 success；其他渠道422，不返回配置详情 |
| 内部桥接 | 网关向 API 获取应用信息、下载媒体、发送文本；仍要求内部渠道鉴权，并核对 Run、账户及配置版本 |
| 入站及回复 | 文本/图片/语音/文件接入既有附件链路；AMR/Ogg Speex转WAV；最终文本回复与分段；不新增成员姓名转换 |
| 队列与 Runs | 复用既有内存队列和 Runs；新 Webhook 快速确认、重复消息处理、命令异步执行、配置变化校验、状态及错误日志 |
| 会话与统计 | 增加 channel:wecom_app 来源类型到公共枚举、查询及展示；沿用既有 Events 提问/用量与 Runs 发送者口径 |
| Console | 新渠道入口及手工配置弹框、保存后ID/URL、管理应用离开确认、字段间距、回调说明、滚动与扩展区、选定原图标；现有页面识别新类型 |
| 数据库与备份 | 011新增两个TEXT可空凭据列、bot_id/secret改可空；配置导入、备份结构及测试同步；不新增表/索引/Runs列 |
| 部署与日志 | 当前页面 origin 拼接固定公网路径，不新增环境变量；wechatpy/defusedxml依赖；INFO阶段日志和关联ID；Webhook访问日志去Query |
| 文档 | 新渠道指南及验证报告、AGENTS及导航；另按用户请求迁入Agent/MCP客户权限说明 |

## 必须单列的共用层影响

- 共用 `ChannelService` 与 `ChannelStore` 新增同一账户、同一消息 ID 但 payload_hash 不同的冲突拒绝。此校验不只作用于新企微应用，也影响旧 WS 渠道。它是防止重试内容冲突的防护，不能描述为旧渠道代码完全没改。
- 渠道日志补入消息 request_id，诊断记录优先保留显式关联 ID；影响共用日志链路。
- 共用企微 HTTP Client 增加媒体下载、文本发送和发送前复核回调；原成员资料查询继续使用原流程。
- 应用引用检查纳入新渠道；被新渠道引用的应用不能直接删除。
- 会话来源枚举抽到公共定义，三个查询入口及相关统计复用；不重新定义原来的统计口径。
- `chat.js`、`main.js` 等部分前端文件仅更新静态导入版本，保证新模块加载；不能从文件数推断为修改了聊天业务行为。

用量页面的差异仅增加企微应用在 Agent 关联渠道图标中的类型与名称分支；未给模型/用户维度或贡献会话增加渠道图标，也未修改两层切换动画。其他已确认 UI 保持当前基线。

## 非企微应用渠道的实际 HTTP 结果

本机8000现有 `local钉钉`，无平台身份Header、无Query、无请求体：

```http
GET /api/v1/webhooks/channels/7fe0f436fcc141dd985b3ad9ed96c82c
HTTP 422
{"code":"webhook_not_supported","detail":"该渠道不支持 Webhook","request_id":"90c972879f0b43f9adcf235260ad8bd1"}

POST /api/v1/webhooks/channels/7fe0f436fcc141dd985b3ad9ed96c82c
HTTP 422
{"code":"webhook_not_supported","detail":"该渠道不支持 Webhook","request_id":"655884dcc55844a9888221909265d20a"}
```

这是正式回调入口的类型拒绝响应，不是早期的渠道信息探测接口；不会运行 Agent 或发送消息。合法企微应用无平台Header的200证据来自上一轮隔离本地HTTP测试，POST网关接收使用测试替身，不能宣称已完成公网企微端到端验收。

## 环境与提交

改造前基线已推送；本轮发布版本为 0.12.4，提交与 Tag 以 Git 记录为准。本地数据库已备份升级011；未部署或修改Test/Alpha。代码核对不是对所有生产权限和平台投递能力的无条件保证。

## 当前文件清单

下列为此次核对时的工作树文件（不含被Git忽略的临时验证材料）：

- `AGENTS.md`
- `console/assets/channel-logos/README.md`
- `console/chat.js`
- `console/configuration-template.html`
- `console/configuration.js`
- `console/configuration/channels.js`
- `console/configuration/configuration.css`
- `console/diagnostic-detail.js`
- `console/diagnostics-center.js`
- `console/diagnostics-channels.js`
- `console/diagnostics-sessions.js`
- `console/diagnostics-usage.js`
- `console/diagnostics.js`
- `console/index.html`
- `console/main.js`
- `console/session-display.js`
- `console/session-inspector.js`
- `console/workspace.js`
- `docs/guides/README.md`
- `docs/guides/wecom-applications.md`
- `docs/guides/wecom-channel.md`
- `requirements.txt`
- `src/deep_agent/api/v1/channel_accounts.py`
- `src/deep_agent/api/v1/diagnostic_sessions.py`
- `src/deep_agent/api/v1/diagnostic_usage.py`
- `src/deep_agent/api/v1/global_settings.py`
- `src/deep_agent/api/v1/sessions.py`
- `src/deep_agent/api/v1/wecom_applications.py`
- `src/deep_agent/channels/app_context.py`
- `src/deep_agent/channels/gateway/app.py`
- `src/deep_agent/channels/gateway/deepagent_client.py`
- `src/deep_agent/channels/gateway/runtime.py`
- `src/deep_agent/channels/metadata.py`
- `src/deep_agent/channels/registry.py`
- `src/deep_agent/channels/service.py`
- `src/deep_agent/core/logging.py`
- `src/deep_agent/db/models.py`
- `src/deep_agent/db/reference_schema.py`
- `src/deep_agent/db/session_source.py`
- `src/deep_agent/db/stores/channel.py`
- `src/deep_agent/db/stores/wecom.py`
- `src/deep_agent/diagnostics/channels.py`
- `src/deep_agent/diagnostics/messages.py`
- `src/deep_agent/diagnostics/runtime.py`
- `src/deep_agent/diagnostics/schema.py`
- `src/deep_agent/schemas/channel.py`
- `src/deep_agent/services/channel_gateway_client.py`
- `src/deep_agent/services/configuration_import.py`
- `src/deep_agent/services/wecom_client.py`
- `tests/frontend/console/wecom-applications.test.mjs`
- `tests/integration/test_channel_accounts_api.py`
- `tests/integration/test_configuration_import_api.py`
- `tests/integration/test_configuration_import_databases.py`
- `tests/unit/test_channel_migration.py`
- `tests/unit/test_config_backup.py`
- `tests/unit/test_configuration_import.py`
- `tests/unit/test_diagnostic_channels.py`
- `tests/unit/test_diagnostics_runtime.py`
- `tests/unit/test_global_settings.py`
- `tests/unit/test_project_layout.py`
- `tests/unit/test_session_source_api.py`
- `tests/unit/test_session_source_storage.py`
- `tests/unit/test_sqlite_bootstrap.py`
- `console/assets/channel-logos/wecom-app.svg`
- `docs/guides/agent-mcp-permissions.md`
- `docs/guides/wecom-app-channel-validation.md`
- `docs/guides/wecom-app-channel.md`
- `migrations/versions/011_channel_webhook_credentials.py`
- `src/deep_agent/api/v1/webhooks.py`
- `src/deep_agent/api/v1/wecom_app_bridge.py`
- `src/deep_agent/channels/adapters/wecom_app/__init__.py`
- `src/deep_agent/channels/gateway/webhook.py`
- `src/deep_agent/schemas/session_source.py`
- `src/deep_agent/services/wecom_webhook.py`
- `tests/frontend/console/wecom-app-channel.test.mjs`
- `tests/integration/test_wecom_webhook_api.py`
- `tests/integration/test_wecom_webhook_persistence.py`
- `tests/unit/test_webhook_access_logging.py`
- `tests/unit/test_webhook_migration.py`
- `tests/unit/test_wecom_app_gateway.py`
- `tests/unit/test_wecom_application_client.py`
