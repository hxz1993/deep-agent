# Agent 安装包

安装包用于首次安装和升级 Agent。预制 ZIP 与上传 ZIP 使用相同格式、解析、预检及事务服务；预检只读，不上传文件、不调用模型或 MCP。

## 操作流程

1. 只选择预制安装包或上传 ZIP。上传后立即解析，在上传框下展示包名、Skill 和 UI 工具摘要或解析错误；解析成功后进入配置。
2. 默认新建，名称使用包内名称；覆盖时只选择目标 Agent，不提供重命名。新建的默认模型同名优先，其次首个可用模型，没有则留空，可改选；覆盖不显示默认模型配置，原模型（含空值）保持不变。点下一步时检查重名并重新预检。
3. 以“类型 / 内容 / 处理 / 说明”表格检查新建、覆盖、保持不变、关联、缺失及共享影响；UI 工具逐项显示，过长滚动、不分页。缺失说明只放对应行，明确配置入口、补充关联及未修复的影响；共享更新统一确认后安装。
4. 安装成功切换独立反馈页，逐项显示结果；底部提供文字“查看 Agent 配置”和主按钮“确定”。各步骤和反馈均保持 860 × 650 弹窗尺寸（受桌面视口上限约束）。

新建生成新的 Agent ID；名称重复须更名。覆盖保留目标 ID、历史会话、渠道和集成引用，替换提示词及关联配置；包中没有的旧关联取消，资源实体保留。内置能力与 MCP 按准确代码自动关联，不提供映射。模型/MCP 凭据不随包传递。

## 资源更新与 UI 工具包

Skill 编码不存在时创建，同编码同内容保持不变，不同内容覆盖。UI 工具包按编码、工具按全局名称创建或更新，跨包同名或代码内置资源冲突明确阻断。影响其他 Agent 的共享资源更新在预检中列出。

Agent 只按 `capabilities` 关联或取消关联整个 UI 工具包，包内全部有效工具均可用，包括后续新增工具。旧数据库和旧 ZIP 的 `ui_tool_selection` 名单在读取时忽略；Agent 再保存会清空旧列，新导出不再包含名单。无需数据库迁移。

若目标环境已有同编码工具包，ZIP 中未列出的旧工具仍保留在该共享包中，整包启用的 Agent 也能使用它们；预检逐项提示这些额外工具。导出包含整个关联 UI 工具包的定义。

## ZIP 格式

```text
manifest.json
agent.json
ui-tools.json
skills/<skill-code>.zip
```

`manifest.json`：`format=deepagent-agent-package`、`schema_version=1`、`exported_from`。软件版本不同不阻断；不支持的格式、版本及影响运行的未知字段明确拒绝。

`agent.json` 包含名称、提示词、能力与 Skill 编码及可选模型参考；旧 ZIP 中的 `ui_tool_selection` 会被忽略。模型参考只含名称和 Provider 类型；安装匹配不要求 Provider 类型相同。`ui-tools.json` 包含整个关联自定义工具包及工具定义、参数/结果 Schema、widget 标识和元数据，不包含组件渲染代码。上传 Skill 保留完整原始 ZIP；内置 Skill、工具和 MCP 仅携带引用。缺失或不可读取的关联 Skill 原包导致导出失败。

不包含温度、来源 Agent ID、平台文件 ID、会话、日志、Provider 凭据或 MCP 认证配置。

压缩上限 50 MiB，累计解压上限 200 MiB，文件数上限 2048；嵌套 Skill 同时执行单包校验。拒绝损坏、重复、越界、链接、加密或校验失败的文件，以及已知字段的错误类型；安全的未知扩展忽略但计入摘要。

## 预制目录

唯一位置为 `src/deep_agent/agent_packages/builtin/<code>.zip`。文件名去掉 `.zip` 即包编码。目录与安装功能同属一个模块；以模块相对位置读取，随完整源码发布，启动不安装。README、子目录不列为安装包。

电子表格包于 2026-09-22 更新为 Alpha 正式导出的 V91 提示词及配套 Skill/UI 资产，DeepCube 保留 2026-09-14 Test 官方导出包。预制列表的 `prompt_version` 仅取提示词首行标题末尾明确的 V 版本，无版本返回 null，页面不显示标记；不使用服务版本替代。来源、内容版本和 SHA-256 见[目录与来源说明](../../src/deep_agent/agent_packages/builtin/README.md)。替换 ZIP 不更新数据库；预检后内容变化会拒绝继续安装。

## 管理员 API

| API | 请求及返回 |
|---|---|
| `GET /api/v1/agents/{id}/export` | 返回实际 ZIP 下载 |
| `GET /api/v1/agent-packages` | 返回 `items`、不可读取或无效包的 `errors` |
| `POST /api/v1/agent-packages/preview` | multipart 的 `file` / `builtin_code` 二选一，加 `mode=create/overwrite` 和覆盖时的 `target_agent_id` |
| `POST /api/v1/agent-packages/install` | 相同来源，加 JSON 文本 `options`；返回 `{agent, mode, results, missing}` |

预检返回 `digest`、`mode`、`target_agent`、`resources`、`references`、`models`、`removed_associations`、`blocked`。资源状态为 `new/update/reuse/missing/blocked`，UI 行另含逐工具 `tools`、`retained_tools`，共享更新含 `affected_agents`。

安装 options：`digest`、`mode`、`target_agent_id`、`name`、`model_id`。新建不得带目标；覆盖必须带目标。省略模型采用预检默认，显式 `null` 清空。旧 `resources`、`mcp_mapping` 参数不再接受。

预检摘要覆盖包内容、Agent、资源及引用状态。正式安装重新解析、在配置独占屏障下核对摘要；变化返回 409，重新预检。上传文件先通过 FileStore 保存，Agent/Skill/UI 元数据在一个事务内写入；运行目录保留可恢复暂存，失败恢复旧内容并清理本次新增且未被数据库引用的文件。确认资源就绪后才报告成功。

提交应答异常会重新核对数据库；已提交且配置完全一致返回成功，确认回滚才报告回滚，无法确认返回 503 `result_uncertain=true`。前端中断或超时重新查询 Agent 列表，不重复提交或声称回滚。

## 005 迁移与本地验收

未发布 `005` 包含三项：`model_id` 可空、移除温度、新增 `ui_tool_selection` 并保留旧 Agent 的整包语义。降级遇空模型或非空工具范围字典明确拒绝，避免丢失配置或扩大可用工具范围。

对于已标为 `005` 的本地库，普通 `upgrade head` 不会再执行；须停本地 API/网关，核对 revision 后使用 Alembic Operations 显式执行本迁移幂等 upgrade，仅补齐列形状，不改 revision、不重建业务数据。正式 `004→005` 仍走正常迁移。

本次本地按用户要求不新增备份，清理指定验收数据、保留原有 6 个 Agent。全组合使用独立 SQLite/FileStore/运行目录。`DEEP_AGENT_SKILL_RUNTIME_ROOT` 可指定实例 Skill 目录，隔离验收不得共用日常实例缓存。

此功能随产品版本 0.10.0 发布。全组合回归入口为 `tests/run_agent_package_matrix.py`，默认报告输出到 `output/TMP_to_delete-agent-package-matrix`；历史验收报告可从 Git 恢复。


## 0.14.3 文本策略与旧版兼容

schema_version 继续整数1，只有一种导出：关闭结构化输出时省略 structured_output_enabled、output_config、allow_text_output，连保留的 true 草稿也不导出；开启携带完整定义，仅允许文本时增加 allow_text_output:true，缺省 false。旧包新建／覆盖安装均关闭结构化输出和文本策略，不继承目标旧值。开启包只能由支持结构化输出的新版本安装，0.14前旧版拒绝且不得产生写入。

allow_text_output 纳入预检摘要、目标状态和事务，只有此值改变也使旧预检失效；正式导出下载期间修改策略会返回冲突。复制和同 Revision 全量配置备份保留关闭草稿。兼容专项只区分552b7b2的0.14前旧版和本次最终新版，不增加0.14内部兼容分支；必须由正式新版导出接口生成ZIP送真实旧版完整预检、安装、覆盖及回读，不能手工拼包替代。
