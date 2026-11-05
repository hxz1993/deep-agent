# Agent 安装包：安装、升级与跨环境迁移

资料核对：2026-11-06，DeepAgent {{DEEP_AGENT_VERSION}} / Head013；目标环境须另读 /info 和 OpenAPI。操作时以目标环境OpenAPI和预检响应为准，不能假定源码版本已部署。

0.14.4／0.14.5清理的是运行中的输出配置副本，不改Agent配置表、正式ZIP schema_version1、导出入口或草稿规则。ZIP不含会话／Pending／输出尝试，包安装兼容不等于旧Runner可接续新版Pending；反向回退限制见[结构化输出](structured-output.md#历史兼容与回退)。全量配置备份仍保留关闭草稿，会话备份保留原事件，不附带批量删除旧快照。

## 选择更新方式

| 目的 | 方式 |
| --- | --- |
| 首次安装助手，或整体升级/跨环境迁移提示词、Skill、UI 工具 | Agent 安装 ZIP，目标环境 preview → install |
| 只改提示词、一个 Skill 或工具定义 | 单项 PATCH/Skill package PUT，保留未涉及关联 |
| 初始化整个环境配置 | 同 Revision 配置备份导入；双方无会话，另附上传 Skill 全量包 |

Agent 包中的 Skill 与安装在 Coding Agent 中的开发 Skill 文件不是同一种安装目标。修改本机 Coding Agent 的技能目录不会同步到 DeepAgent；DeepAgent Skill 原包必须通过管理接口或 Agent 整包安装进入目标 FileStore 和运行目录。

## 包包含什么

```text
manifest.json       format=deepagent-agent-package, schema_version=1
agent.json          名称、提示词原文、capabilities、skills、可选模型参考
ui-tools.json       关联自定义 UI 工具包的完整定义（包括 schema、widget、metadata）
skills/<code>.zip   关联的上传 Skill 完整原包
```

内置 Skill、内置能力和 MCP 只带引用；模型只带名称与 Provider 类型参考，不带来源模型 ID。包不包含 Provider/MCP 凭据、会话、渠道/集成配置、平台文件 ID、业务元素或前端 widget 渲染代码。目标环境分别配置这些依赖。导出时关联上传 Skill 原包不可读取会直接失败。

预制包属于安装功能，唯一目录为 `src/deep_agent/agent_packages/builtin/`：当前为正式同格式预制资源 `deepcube-assistant.zip`、`deeptable-assistant.zip`。以模块路径加载、随源码发布，启动不安装；不再放入 deploy。预制与上传、导出 ZIP 格式相同。

电子表格当前资产位于`deepfos-repos/deepfos_planning/view-planning/packages/spreadsheet/src/pages/sheet/use/components/aiPanel/agentAssets/`。准确prompt/Skill版本、正式ZIP来源及hash从当前源资产和builtin/README.md读取，不把历史V85等版本写成当前固定基线。

`GET /api/v1/agent-packages` 的条目有可空 `prompt_version`，仅提取提示词首个Markdown标题中的明确版本，目录显示实际“提示词 V...”，没有版本就不展示；它不是服务版本或整个安装包版本。

更新预制包时先由前端资产负责人交付完整ZIP，核对提示词、Skill交付文件、完整UI schema与选择范围，再替换同名ZIP并更新来源说明、运行隔离包测试。源资产 `model_reference:null` 与发布包模型参考的差别必须记录。替换ZIP只影响后续安装，不代表线上Agent已同步，也不把业务UI renderer装进后端。

压缩最多50MiB、累计解压200MiB、2048文件；损坏、重复JSON键/文件、越界路径、符号链接、加密和已知字段类型错误严格拒绝。0.14允许安全未知扩展，但未知内容仍计入摘要，不能据此放宽ZIP安全与预算。不要用普通 Skill ZIP、随意手写 manifest 或前端资产 ZIP 冒充 Agent 包。

## 原生输出与包兼容

包格式仍整数schema_version1。关闭导出省略structured_output_enabled/output_config/allow_text_output及草稿；开启带总开关和完整定义，仅允许文本时增加allow_text_output:true，缺省false。旧服务拒绝开启包；旧包新建/覆盖新版重置两个开关false并清旧定义。策略变化使旧预检摘要失效；完整配置备份保留关闭草稿且仅同Revision恢复。完整定义回读走管理员`GET /agents/{id}/output-config`，不能只看普通Agent摘要。

更新包/平台/客户端是不同发布面，安装不自动让未适配客户端支持原生JSON。先隔离Agent验证，再按授权启用目标；细则见[结构化输出](structured-output.md)。

## 可执行的管理 API 流程

使用已确定来源/目标环境的管理员请求上下文。沿用现有鉴权客户端，不输出 token，不将来源认证转发到目标。以下路径相对该环境服务根；网关前缀按环境解析。

1. 从源环境 `GET /api/v1/agents/{source_agent_id}/export` 下载 ZIP。记录来源 Agent ID、版本和 ZIP SHA-256；不手工删改校验字段。也可在目标 `GET /api/v1/agent-packages` 取得预制包的真实 `code`，列表返回 `items/errors`。
2. 在目标选择 `create` 或 `overwrite`。新建名称初值取包名，重名时改名；不能把重名自动解释成覆盖。覆盖先 `GET /api/v1/agents/{target_agent_id}`，保存当前 `name`、`model_id`、关联范围；明确目标 ID。
3. `POST /api/v1/agent-packages/preview`，multipart 字段为 `file` 或 `builtin_code` 二选一，加 `mode`，覆盖另加 `target_agent_id`。此步骤只读，不上传 FileStore，也不调用模型/MCP。不要在 JSON Body 里发送文件。
4. 检查 `blocked`、`resources`、`references`、`removed_associations` 和共享 `affected_agents`。记录新建、覆盖、保持不变、取消关联和缺失项。阻断先解决；变更若超出用户授权范围，给出具体影响后再取得授权，已有授权范围内继续。
5. 用同一 ZIP 字节或预制来源 `POST /api/v1/agent-packages/install`，multipart 只有来源和 `options`；后者是 JSON 文本，结构如下。正常返回 HTTP **201**、`{agent, mode, results, missing}`。
6. 通过返回的 `agent.id` GET 回读，核对名称、提示词、模型、capabilities/skills，以及改动资源的内容/hash。区分“安装成功但缺依赖”与“已可对话”；只有要求行为验收或改动模型/工具行为时再做对应真实会话。

`options` 示例（值从目标预检和用户选择生成，非可直接照抄的 ID）：

```json
{
  "digest": "<目标 preview.digest，64 位十六进制>",
  "mode": "overwrite",
  "target_agent_id": "<明确选定的目标 ID>",
  "name": "<目标当前名称>",
  "model_id": null
}
```

上例 null 仅适用于目标原本没模型。覆盖必须显式发送目标当前 `model_id`，有模型则传其 ID；同样保留目标当前名称。API 省略 `model_id` 会采用包的匹配默认值，**不会自动保留覆盖目标模型**。目标已有模型不可用时当前安装校验会拒绝，先说明并在授权范围内修复配置，不偷偷换模型。

新建用 `mode=create`，不传 target；`name` 使用确认后的唯一名称，模型用 `preview.references.model.matched_id` 或手动选中的目标 `preview.models[].id`。默认同名可用模型优先，否则列表首项，否则 null；显式 null 为无模型。没有模型允许安装，对话需要请求模型或 Agent 默认模型，无环境默认回退。`temperature` 不再接受。

不发送旧的逐资源 `resources` 决策或 `mcp_mapping` 参数。`digest` 是包与目标配置状态摘要，不等于 ZIP SHA-256，也不能跨环境复用。

## 资源处理与影响

- Agent 新建产生新 ID；覆盖保留目标 ID、会话及渠道/集成引用，提示词和关联按包重建。包外旧关联取消，资源实体保留；整包覆盖不是局部 PATCH。
- Skill 编码不存在则新建，同内容保持不变，不同内容覆盖。共享 Skill 更新会影响其他引用 Agent。
- UI 包按编码、工具按全局名称新建或更新。工具已归属另一包则阻断，不能搬移。共享定义更新对其他 Agent 的影响列入摘要。
- UI 工具按整包关联：旧包额外工具仍属于该包时也随整包可用，不再按安装包的逐工具名单限制。旧 ZIP 中的 `ui_tool_selection` 被忽略，新导出不携带该字段；安装、预检对比和回读都核对包级关联与真实共享定义，不恢复旧明细选择。
- 内置能力按准确编码、MCP 按准确 `mcp:<服务名>` 自动关联；缺失允许安装但工具暂不可用。MCP 缺失时在目标 MCP 配置中补服务，再在 Agent 配置补关联；不能暗中映射到别的服务。安装并不探测 MCP 连接成功。

## 冲突与结果不确定

安装时在配置协调与引用锁下重新校验摘要。409 表示包、目标或资源状态已变化，重新预检并核对影响；不能仅替换 digest 后盲目重试。

文件上传、数据库写入与 Skill 运行目录准备协调执行，明确失败会回滚数据库并恢复/清理文件。超时、断连或 503 `result_uncertain=true` 不能声称回滚：先读回目标 Agent 和资源判断真实结果。新建可按确认名称定位候选并逐项核对；证据仍不充分时保持“结果待确认”，不要重复安装造成副本。

## Console 与验证范围

Console 流程：选预制/上传包 → 配置 Agent → 检查表并安装 → 独立反馈。上传后在上传框下直接展示解析摘要或错误。新建预填包名并选择默认模型；覆盖只选目标，隐藏名称和模型。检查与反馈按行显示缺失、共享影响及处理说明，长内容滚动，不分页；各步和反馈保持弹窗尺寸不跳变。

只执行迁移时做预检、安装结果和回读即可作为配置安装证据；按要求再补真实会话。纯 UI 调整一次桌面冒烟即可。修改安装规则、文件补偿、关联/工具范围或并发逻辑时，定向运行真实 ZIP + 隔离 SQLite/FileStore/运行目录测试。`docs/reports/agent-package-matrix/` 的 25,600 组是安装机制验收基线，不是每次业务迁移或 UI 修改的默认成本。

交付记录来源/目标环境与版本、ZIP hash、方式与目标 ID、逐项结果、缺失及后续动作；不包含凭据。大批量验收使用独立库和文件目录，`DEEP_AGENT_SKILL_RUNTIME_ROOT` 指定独立运行目录，不污染日常实例。

## 当前实现入口

仓库通常位于 `<deep-agent-repository>`。后续更新先核对：

- `docs/guides/agent-packages.md`：格式、流程与迁移。
- `src/deep_agent/api/v1/agent_packages.py`、`agent_packages/archive.py`：multipart 契约和 options。
- `src/deep_agent/agent_packages/service.py`、`runtime_transaction.py`：摘要、事务与文件补偿。
- `src/deep_agent/capabilities/ui/selection.py`：Agent 工具范围。
- `console/configuration/agent-packages.js`：当前交互。
- `docs/guides/configuration-import.md`：全量配置初始化，不能混作 Agent ZIP 安装。
