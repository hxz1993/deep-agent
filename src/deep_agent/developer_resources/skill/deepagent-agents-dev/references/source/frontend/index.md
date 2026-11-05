# DeepAgent 业务客户端开发

用于已经取得源码的聊天、电子表格或其他业务客户端，包括其 Prompt、Schema、Agent manifest、Skill 和 UI Tool 资产。内置 `/console/` 属于[后端开发](../backend/index.md)；没有源码的配置与排查使用[业务接口指南](../../11-api-and-version.md)。

先读取目标仓库 AGENTS、分支和现有改动，并分别核对服务 `/info`、OpenAPI、客户端实际加载版本和 Agent 配置。资料基线为 **{{DEEP_AGENT_VERSION}} / 数据库 013**；本地资产变化不表示目标环境已更新。

## 定位与按需阅读

电子表格可能位于 `view-planning/packages/spreadsheet`（助手资产 `aiPanel/agentAssets/`）或独立 `element-deep-table`，按交付目标确认，不默认双向同步。DeepCube/Python 客户端入口为 `element-python3/packages/page/src/components/layout/content/DeepCubeChatbot/`；其他客户端由项目 AGENTS 定位。

| 修改或排查内容 | 参考 |
| --- | --- |
| 请求、身份、SSE、会话、附件、停止 | [调用契约](references/frontend-api-contract.md) |
| 原生输出接入、结果去重和业务采用 | [结构化输出](references/frontend-structured-output.md) |
| Prompt、完整 Schema、Skill/UI 包与线上同步 | [配置资产](references/frontend-agent-configuration.md) |
| context_refs、压缩与发送策略 | [上下文](references/frontend-context-lifecycle.md) |
| Deferred、人审、config_fill 与工具展示 | [交互协议](references/frontend-ui-protocols.md) |
| 本机服务、目标环境与页面代理 | [本地联调](references/frontend-local-proxy.md) |
| 环境与 Session ID 取证 | [只读会话诊断](references/frontend-session-diagnostics.md) |
| 渠道 owner、成员与身份 | [渠道身份](references/frontend-channel-settings.md) |
| 回归范围、问题归因与交付 | [前端回归](references/frontend-regression.md) |
| 授权删除测试资源 | [资源生命周期](../backend/references/agent-resource-lifecycle.md) |

## 接入要点

- 复用宿主同源请求层与有效 app/space/language；身份由网关或本地 Profile 提供，不用 Body user 或伪造 Header 代替授权。
- 原生结果按 `output_info` 与 `History.kind` 识别，不按值是否为字符串猜测。平台接受后还须做业务校验、人审和保存；客户端不保存平台 Schema 副本。
- 实时与 History 共用业务校验/展示逻辑。刷新、切换、断流恢复只读，不重发模型、补丁、填充或保存。真正停止调用取消接口并等待终态，不能用传输 Abort 冒充。
- Prompt 保持任务路由与产物契约，业务细则放 Skill，结构约束放完整 Schema。平台格式纠错留在同一 Turn；不要在客户端另开 repair 重置额度。

细节和负向场景见对应专题，按实际改动选择回归。可用包根 `scripts/parse_deep_agent_sse.py` 汇总私有 SSE，但通知帧数不等于真实工具执行或业务保存次数。交付分别说明本地改动、包安装、实页加载及业务回读，保留用户指定会话和证据。
