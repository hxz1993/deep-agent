# Agent 安装包

安装包用于首次安装和升级 Agent。预制 ZIP 与上传 ZIP 使用相同格式、解析、预检及事务服务；预检只读，不上传文件、不调用模型或 MCP。

## 操作流程

1. 只选择预制安装包或上传 ZIP。上传后立即解析，在上传框下展示包名、Skill 和 UI 工具摘要或解析错误；解析成功后进入配置。
2. 默认新建，名称使用包内名称；覆盖时只选择目标 Agent，不提供重命名。新建的默认模型同名优先，其次首个可用模型，没有则留空，可改选；覆盖不显示默认模型配置，原模型（含空值）保持不变。点下一步时检查重名并重新预检。
3. 以“类型 / 内容 / 处理 / 说明”表格检查新建、覆盖、保持不变、关联、缺失及共享影响；UI 工具逐项显示，过长滚动、不分页。缺失说明只放对应行，明确配置入口、补充关联及未修复的影响；共享更新统一确认后安装。
4. 安装成功切换独立反馈页，逐项显示结果；底部提供文字“查看 Agent 配置”和主按钮“确定”。各步骤和反馈均保持 860 × 650 弹窗尺寸（受桌面视口上限约束）。

新建生成新的 Agent ID；名称重复须更名。覆盖保留目标 ID、历史会话、渠道和集成引用，替换提示词及关联配置；包中没有的旧关联取消，资源实体保留。内置能力与 MCP 按准确代码自动关联，不提供映射。模型/MCP 凭据不随包传递。

## 资源更新与 UI 工具范围

Skill 编码不存在时创建，同编码同内容保持不变，不同内容覆盖。UI 工具包按编码、工具按全局名称创建或更新，跨包同名或代码内置资源冲突明确阻断。影响其他 Agent 的共享资源更新在预检中列出。

旧 UI 包有 A/B/C，安装包只有 A/B：保留 C 及原归属，其他 Agent 仍可使用 C；本次 Agent 仅使用 A/B。`Agent.ui_tool_selection` 保存包编码到工具名单的映射：

- 未包含某包 key：整包关联，兼容旧 Agent。
- 包 key 对应具体名单：仅这些工具可进入模型声明与运行，`[]` 表示不启用该包任何工具。
- 普通配置可调整范围；PATCH 省略字段保留，取消包关联时同时清理对应范围。
- 复制、配置备份导入、Agent 导出安装、工具提示和 HITL 续跑均保留并校验范围。

安装旧包时从 ZIP 内实际 UI 定义建立明确名单，不把目标环境额外工具带入。导出包含整个关联 UI 工具包的定义，同时保存 Agent 的实际选择范围；再安装不扩大工具范围。

## ZIP 格式

```text
manifest.json
agent.json
ui-tools.json
skills/<skill-code>.zip
```

`manifest.json`：`format=deepagent-agent-package`、`schema_version=1`、`exported_from`。软件版本不同不阻断；不支持的格式、版本及影响运行的未知字段明确拒绝。

`agent.json` 包含名称、提示词、能力与 Skill 编码、可选模型参考及 `ui_tool_selection`。模型参考只含名称和 Provider 类型；安装匹配不要求 Provider 类型相同。`ui-tools.json` 包含整个关联自定义工具包及工具定义、参数/结果 Schema、widget 标识和元数据，不包含组件渲染代码。上传 Skill 保留完整原始 ZIP；内置 Skill、工具和 MCP 仅携带引用。缺失或不可读取的关联 Skill 原包导致导出失败。

不包含温度、来源 Agent ID、平台文件 ID、会话、日志、Provider 凭据或 MCP 认证配置。

压缩上限 50 MiB，累计解压上限 200 MiB，文件数上限 2048；嵌套 Skill 同时执行单包校验。拒绝损坏、重复、越界、链接、加密或校验失败的文件，以及不识别的字段和内容。

## 预制目录

唯一位置为 `src/deep_agent/agent_packages/builtin/<code>.zip`。文件名去掉 `.zip` 即包编码。目录与安装功能同属一个模块；以模块相对位置读取，随完整源码发布，启动不安装。README、子目录不列为安装包。

当前电子表格和 DeepCube 为暂行包，后续正式版整包替换。详见[目录与来源说明](../../src/deep_agent/agent_packages/builtin/README.md)。两份 ZIP 从旧部署目录移动时 SHA-256 保持一致。替换 ZIP 不更新数据库；预检后内容变化会拒绝继续安装。

## 管理员 API

| API | 请求及返回 |
|---|---|
| `GET /api/v1/agents/{id}/export` | 返回实际 ZIP 下载 |
| `GET /api/v1/agent-packages` | 返回 `items`、不可读取或无效包的 `errors` |
| `POST /api/v1/agent-packages/preview` | multipart 的 `file` / `builtin_code` 二选一，加 `mode=create/overwrite` 和覆盖时的 `target_agent_id` |
| `POST /api/v1/agent-packages/install` | 相同来源，加 JSON 文本 `options`；返回 `{agent, mode, results, missing}` |

预检返回 `digest`、`mode`、`target_agent`、`resources`、`references`、`models`、`ui_tool_selection`、`removed_associations`、`blocked`。资源状态为 `new/update/reuse/missing/blocked`，UI 行另含逐工具 `tools`、`retained_tools`、`selected_tools`，共享更新含 `affected_agents`。

安装 options：`digest`、`mode`、`target_agent_id`、`name`、`model_id`。新建不得带目标；覆盖必须带目标。省略模型采用预检默认，显式 `null` 清空。旧 `resources`、`mcp_mapping` 参数不再接受。

预检摘要覆盖包内容、Agent、资源及引用状态。正式安装重新解析、在配置独占屏障下核对摘要；变化返回 409，重新预检。上传文件先通过 FileStore 保存，Agent/Skill/UI 元数据在一个事务内写入；运行目录保留可恢复暂存，失败恢复旧内容并清理本次新增且未被数据库引用的文件。确认资源就绪后才报告成功。

提交应答异常会重新核对数据库；已提交且配置完全一致返回成功，确认回滚才报告回滚，无法确认返回 503 `result_uncertain=true`。前端中断或超时重新查询 Agent 列表，不重复提交或声称回滚。

## 005 迁移与本地验收

未发布 `005` 包含三项：`model_id` 可空、移除温度、新增 `ui_tool_selection` 并保留旧 Agent 的整包语义。降级遇空模型或非空工具范围字典明确拒绝，避免丢失配置或扩大可用工具范围。

对于已标为 `005` 的本地库，普通 `upgrade head` 不会再执行；须停本地 API/网关，核对 revision 后使用 Alembic Operations 显式执行本迁移幂等 upgrade，仅补齐列形状，不改 revision、不重建业务数据。正式 `004→005` 仍走正常迁移。

本次本地按用户要求不新增备份，清理指定验收数据、保留原有 6 个 Agent。全组合使用独立 SQLite/FileStore/运行目录。`DEEP_AGENT_SKILL_RUNTIME_ROOT` 可指定实例 Skill 目录，隔离验收不得共用日常实例缓存。

全组合结果及复现入口见 [矩阵验收](../reports/agent-package-matrix/README.md)。此功能随产品版本 0.10.0 发布。
