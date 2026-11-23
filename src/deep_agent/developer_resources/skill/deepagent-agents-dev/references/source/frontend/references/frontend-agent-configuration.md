# 前端 Agent 配置资产与同步契约

## 适用边界

本说明用于维护前端仓库中的 Agent manifest、系统提示词、UI Tool JSON 和同步说明。它不授权修改 deep-agent 代码、数据库或线上配置。

只有用户明确要求同步某个环境时，才使用该环境当前 OpenAPI 和管理 API；没有授权时只改本地资产并说明未线上生效。

## 当前管理入口

- 管理页面：`/console/#/configuration`
- OpenAPI：`/openapi.json`
- 旧 `/admin/` 已移除，不再作为操作入口。

环境域名和服务前缀由目标环境决定，不在 skill 或源码中写死登录态。

## Agent schema

`AgentCreate` 当前字段：

```text
name, model_id, prompt, capabilities, skills, structured_output_enabled, allow_text_output, output_config
```

`AgentUpdate` 是相同字段的部分更新。`model_id` 可为 null；PATCH 省略保留、显式 null 清空。`temperature` 已移除且发送会被拒绝。安全规则：

- 只改提示词时只 PATCH `{"prompt": "..."}`。
- 不发送 `model`、`base_url`、`api_key` 等非 AgentUpdate 字段。
- 修改 `capabilities` 或 `skills` 前先 GET 当前 Agent，保留线上已有项后再增删目标 code；两者都是显式列表且 PATCH 不接受 `null`。
- `capabilities`关联Tool Package，`skills`关联Skill Package。UI按整个ui包启用，历史ui_tool_selection不参与授权/运行，旧包读入忽略，新导出不携带；不恢复逐工具选择。
- 本地 manifest 是期望状态说明，不应直接当成覆盖线上 Agent 的 PATCH 请求体。

需要估算提示词上下文时调用 `POST /api/v1/agents/prompt-estimate`，Body 为 `model_id/prompt`。该结果是对应模型估算值；真实对话用量仍只以 `final.usage` 为准。

## UI Tool schema

`UiToolCreate` 当前字段：

```text
name, package_code, description, parameters_json_schema, widget, result_schema, metadata
```

`UiToolUpdate` 字段相同但不包含 `name`。当前来源只有：

- `builtin`：内置、只读、默认可用。
- `manual`：通过管理 API 创建，需显式关联 Agent。

以下旧字段不要再保存或发送：

```text
agent_ids, override, mcp_server_name, discovered_at
```

UI Tool 不再从 MCP 自动发现。当前已删除 UI Tool 和工具包的资源级启停状态；手工工具已归属有效 `ui:<package>`，且 Agent `capabilities` 包含精确工具包 code 时，该包全部合法工具进入当前Agent能力集合。`enabled` 属于废弃输入，接口会明确拒绝而不是静默忽略。

工具包通过 `/api/v1/ui-tool-packages` 创建和维护；`GET /api/v1/tools` 可查看 Agent 可配置的工具包能力。

## Agent Skill schema

- `GET /api/v1/skills` / `GET /api/v1/skills/{code}`：读取 Skill、版本、资源索引、content hash 和运行时状态。
- `POST /api/v1/skills`：上传新 Skill ZIP。
- `PUT /api/v1/skills/{code}/package`：替换已有 Skill 包；code 必须保持不变，相同内容可返回 `noop/repaired`。
- `PATCH /api/v1/skills/{code}`：只更新 label/description，不替换包内容。
- `POST /api/v1/skills/{code}/reload`：重新物化运行时文件，不代替包更新。

前端仓库交付的 Skill 以目录为源，至少包含 `SKILL.md`；推荐用 `manifest.yaml` 固定 `code/label/version/schema_version=1.0`，references 按需加载。打 ZIP 时允许 Skill 文件位于根或唯一同名包目录，禁止把 token、测试输出和 `.DS_Store` 打进去。

### 电子表格助手应用专用配置

组件先用 `GET /api/v1/integrations/deeptable-assistant` 获取关联 Agent，再沿既有聊天协议传递当前应用上下文。DeepAgent 根据会话 App/Space 与调用者权限追加应用提示词和专用 Skill。切换调试 App 后用新会话验收，已有会话沿用原应用。

专用包在应用详情上传与下载。复用原 Skill ZIP 和包内编码；交付通用 Agent ZIP 时单独移交环境专用配置。测试同名包时在目标 App 和未配置 App 发同一问题，核对轨迹中实际加载的应用 Skill 别名。客户端保持平台权限边界，内部存储键只用于服务端排查。

## Schema、metadata 与 result_schema

- `parameters_json_schema` 保存时会校验 JSON Schema 合法性和大小，运行时还会用于参数校验与模型纠错。
- `deepcube_clarify` 还要求非空 fields、稳定且唯一的字段 key、合法字段类型，以及 select 的非空有效 options。
- `metadata` 随 SSE 提供给前端做 widget/form_type/display_name 识别，不作为模型 ToolDefinition 的说明。
- 需要约束模型生成的规则写在系统提示词、工具 `description` 或 `parameters_json_schema` 中。
- 不依赖 `result_schema` 代替前端提交结构检查或 deferred 续跑协议验证。

## DeepCube 助手约定

- 两个 UI Tool 的 `package_code` 都是 `ui:deepcube`，Agent capabilities 包含 `ui:deepcube`。
- 变量查询能力由 `mcp:deepfos` 提供；需要变量定义、当前用户可见值或原始引用时，Agent capabilities 应保留 `mcp:deepfos`。
- 运行工具范围由整包capabilities决定；prompt可以路由工具但不能充当包内授权过滤。DeepCube提示词/renderer是否支持某工具须查当前资产，不用历史名单推断。
- `deepcube_config_fill` 可以保留为手工定义和 schema 参考，但最终配置由正文中的 `config_fill` Markdown JSON 代码块承载。
- `config_fill` 解析失败时前端明确提示并保留原文，不自动补引号或猜业务字段。
- 结构化上下文启用后，提示词只说明 `deepcube.editor_context@1` 的 `payload` 业务字段和 `metadata` 边界，不再写 `<reference>`、`[@context]`、`<user>` 或“哪段是 User/Context”的文本信封教程。
- `deepfos_variable_get` 成功后应停止重复查询。返回的 `reference` 用于说明变量元素中的 `&&/$$` 原始引用；Python 页面绑定、数据范围和脚本使用页面本地编码。只有结果明确提供 `effective_value` 时才表述为当前有效值。
- DeepCube 当前把通用脚本语法、建模、解释和排错规则交给 `deepcube-script-authoring` Skill；系统提示词只保留页面上下文、工具路由、交互和产物契约。Agent `skills` 必须包含该 code。

## 整包交付：首次安装、升级与跨环境迁移

按 [整包操作参考](../../backend/references/agent-packages-and-migration.md) 执行已授权目标环境的导出/预检/安装。该参考仅提供配置 API 契约，不授权修改后端代码。交付整个助手时优先整包；不要将普通 Skill ZIP 或前端自定义 manifest 当作 Agent 包上传。

覆盖使用明确目标 ID，并发送目标当前名称和 `model_id`（包括 null），不能省略模型而意外套用包的默认匹配。预检中的共享更新和取消关联是实际变更范围；跨包工具重名阻断，409 后重新预检。安装后核对整包capabilities和共享定义；目标包额外合法工具也会随整包可用，预检需核对实际影响。

包只含工具定义及 widget 标识，前端 renderer 代码仍随前端发布。MCP 缺失需在目标环境配置并补充 Agent 关联；未配模型可完成安装，但对话前必须选可用模型。只验证配置安装时不必调用模型；修改提示词/工具行为或要求可用性验收时再做对应真实会话。

## View Planning 电子表格与预制包

电子表格助手资产从 `deepfos-repos/deepfos_planning/view-planning/packages/spreadsheet/src/pages/sheet/use/components/aiPanel/agentAssets/` 维护，个人报表 renderer 从同仓 `packages/personal-table` 定位。旧 `element-deep-table` 独立目录已移除；旧 manifest 的 `exported_from` 是来源记录，不能据此寻找已删除仓库。

预制包唯一目录为后端`src/deep_agent/agent_packages/builtin/`，准确提示词/Skill版本、来源提交和hash从当前源资产与builtin/README.md读取，不把旧V85等快照当固定版本。前端交付完整正式ZIP及来源说明；不顺手替换后端资源。替换ZIP只影响后续安装，不自动同步已安装Agent，widget代码仍随客户端发布。

## 原生输出配置与协同同步

默认关闭。二级allow_text_output默认false，API顶层strict boolean，PATCH省略保持、null拒绝。管理员GET`/agents/{id}/output-config`回读两个开关与`output_config:{name,description,json_schema}`，Chat不逐请求带Schema。开启必须有完整生产定义，覆盖本产品首轮/续轮，而不是text演示桩。name规则和预算以[原生输出](frontend-structured-output.md)及平台指南为准。

- 只改无关prompt时仍使用最小PATCH；当prompt/原生Schema/开关构成同一生成契约时，在同一次Agent PATCH中同步；若本次明确改变文本策略，把顶层allow_text_output一并提交，未涉及则省略保持，不嵌进output_config。Skill包独立上传并逐文件/hash回读；若替换共享Skill会影响旧契约，先POST独立新code，再把目标Skill引用加入同次Agent PATCH，只精确替换原引用并保留其它项。已有目标code只在完整内容一致时复用，不盲目覆盖；不存在跨资源原子事务。
- 先私有before快照及并发基准，更新后完整GET输出定义、prompt和Skill原包。保留模型/MCP/其它skills、name及未涉及UI绑定；updated_by/updated_at由可信服务端变化，不伪造Body字段。
- 关闭开关保留草稿；显式output_config=null仅在最终关闭时清除。0.14.4起每个Run含Deferred续跑使用当前定义，不恢复历史版本；同一会话配置保持一致，改配置后新建会话。客户端不存平台Schema副本，配置同步不改写旧结果、批准或回执。
- Agent ZIP仍schema_version1。关闭导出省略structured_output_enabled/output_config/allow_text_output及全部草稿；开启含总开关和完整定义，仅允许文本时导出allow_text_output:true。旧包新建/覆盖新版均重置两个开关false并清旧定义。策略也进入预检摘要，policy-only变化会使旧摘要失效；配置备份按同Revision保留关闭草稿。安全未知扩展不放宽已知类型或ZIP校验。
- 配置回退按已验证的prompt/Schema/开关及业务Skill成组恢复与回读，不只关开关留下要求原生工具的prompt。回退不删除已有历史或撤销已保存业务数据。

### BOM业务资产的分工

完整Schema维护结构、required、enum和可静态表达的ready条件，系统提示词只给最新产物路径与流程路由；不要继续向模型教旧围栏或旧扁平输出。业务Skill维护映射、查询证据与人审规则，避免在prompt和Skill重复整份字段字典。

编号查询先从实际映射字段提取非空、非占位值集合；相邻客户编号或其它列不能借作该编号字段的全局精确查询，即使称为辅助查询。纳入导入的行缺必需用量时不得ready；某一行的用户说明不推及整批。具体列号、原稿行号、项目号属于用例，不固化进通用提示词。

## 单项同步流程

1. 获取目标环境 `/openapi.json`，确认 schema 和路径。
2. GET 当前 Agent、目标 Skill、UI Tool 工具包、UI Tools 和 `/api/v1/tools`。
3. 若 Skill 包有变化，先 POST 安装或 PUT 替换并回读 content hash/resources；再按需要更新 Agent `skills`。
4. 先创建或更新 UI 工具包，再按 `UiToolCreate` / `UiToolUpdate` 同步包内工具；不要原样发送带本地包装字段的 JSON 文件。
5. 按上方契约PATCH所需prompt、输出定义/开关、capabilities或skills；修改列表保留其它项，输出协同变更不拆成半套配置。
6. GET回读Agent及完整output-config、Skill包和涉及UI Tool，核对prompt、定义/开关、capabilities/skills、包版本/hash及widget/metadata。
7. 改动行为时用全新会话验证受影响的 Skill、clarify、deferred 续跑或 config_fill；纯配置回读与前端交互冒烟按本次范围执行。

接口返回 200 只表示配置写入成功，不代表模型生成和前端渲染已通过。

## 安全

- 不把 token、cookie、用户 UUID、数据库连接或私有环境 profile 写入配置资产。
- 不打印完整登录态；测试证据只保留必要状态码、结构和脱敏后的会话 ID。
- 线上同步前记录本地版本和目标环境，写入后记录回读结果，避免把 Test 与其它环境混淆。
