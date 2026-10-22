# Agent 结构化输出（0.14.3 / 数据库 013）

Agent 的“输出”页签默认关闭。关闭时沿用既有文字、工具和 UI 暂停协议。开启后配置一个结果名称（英文标识）、一段说明和一份 JSON Schema，默认最终必须提交符合定义的原生 JSON；勾选二级选项“允许文本输出”后，也可用普通文字结束。中途仍可查询或等待 UI 确认；JSON 中的澄清问题只是数据，不自动触发暂停。

## 输出模式

| 配置 | 运行时输出类型 |
| --- | --- |
| 关闭结构化输出 | `str + DeferredToolRequests` |
| 开启，不允许文本（默认） | `ToolOutput(Schema) + DeferredToolRequests` |
| 开启，允许文本 | `str + ToolOutput(Schema) + DeferredToolRequests` |

“允许文本输出”位于右侧结构设置上方，带说明问号，只在开启结构化输出时显示，默认 false。关闭总开关保留该选项和定义草稿，不参与运行。字段“必须”只维护 JSON 的 required，不控制文本分支；允许文本也不会放宽 JSON 校验。BOM 等业务客户端是否允许文本结束应由其负责人决定，默认仍保持严格 JSON。

## 配置与权限

结果名称采用 1–64 位 ASCII 字母、数字、下划线，以字母开头，不能与已授权工具编码冲突。说明最多 8192 字符。外层字段可以全部非必须，也允许空对象；某字段出现后，其内部 required 等约束仍生效。无需按对话轮次动态换 Schema。

Console右侧默认展示字段卡片，“结构设置”标题旁提供“查看 JSON／添加结构／检查定义”；查看JSON沿用原生编辑器，可返回结构卡片。

Console 中，说明与完整 JSON Schema 向下撑满并对齐底边；无结构时可从空态文字“添加结构”进入详情。详情的“确定／取消”只应用或撤销弹框草稿，页头“保存”才提交 Agent。

字段模式仅投影根 `properties`，允许同时存在 `allOf/anyOf/oneOf/not/if/then/else` 等完整规则；切换模式不修改定义。本地 `$ref` 仅解析用于展示类型和字段数，详情仍编辑原始引用及兄弟关键字，不展开覆盖 `$defs`。数组/标量和仅从分支或引用继承字段、没有直接根字段的定义继续用原生编辑器；空对象仍可在字段模式添加结构。

字段卡片保留可直接修改的“必须”控件，只维护根 `required`；原条件规则仍独立生效，不因条件关联禁用勾选。浏览、切换及同名编辑保留原 required。卡片允许新增、改名、删除，不因复杂条件、依赖或名称规则额外禁用操作；原条件及 `$defs` 不自动跟随改写，由配置者维护规则一致性。保留 JSON 语法、重复编码和服务端完整 Schema 校验。类型与其他约束仍同时生效，并非覆盖原规则。详情“取消”、关闭和 Esc 不把失败草稿带入整页保存。

管理员使用现有创建/PATCH Agent 接口，一次保存开关和定义：

```json
{
  "structured_output_enabled": true,
  "allow_text_output": false,
  "output_config": {
    "name": "lease_result",
    "description": "按需组合正文、合同和澄清问题。",
    "json_schema": {
      "type": "object",
      "properties": {
        "text": {"type": "string"},
        "contract": {
          "type": "object",
          "properties": {"id": {"type": "string"}},
          "required": ["id"]
        },
        "clarification": {"type": "array", "items": {"type": "string"}}
      },
      "additionalProperties": false
    }
  }
}
```

`{}`、只有 text、只有合法 contract、contract 加 clarification 均可通过；`{"contract":{}}` 不合法。平台只检查定义中的静态规则，已有合同基线、版本/Hash、业务写入权限继续由客户端检查。

`GET /api/v1/agents/{id}/output-config` 为管理员只读接口，返回 `structured_output_enabled`、`allow_text_output` 和完整 `output_config`（没有定义为 null）。普通 Agent 详情不带管理 Schema；列表不逐项加载定义。

创建及 PATCH 的 allow_text_output 必须是 JSON boolean，显式 null 拒绝。PATCH 省略字段保持原值；关掉开关不删除草稿。显式 `output_config:null` 仅在合并后的最终状态关闭时删除定义。开启但没有定义会拒绝整个事务。无效 Schema 的编辑内容保留在页面，页头保存前必须修正；详情无第二个独立保存动作。

Agent 的更新账号由可信登录身份写入，与开关、定义和更新时间同一事务保存。列表批量解析 Username，账号服务不可用时显示账号 ID，历史未知显示“—”；不从请求 Body 或安装包接受伪造的更新账号。

## 校验和恢复

- 使用 Draft 2020-12，不补默认值、不转换类型；省略、null、空串、false、0 保持区别。对象、数组和标量都支持；数组/标量在模型工具参数中的内部包装不会出现在公共结果里。
- Schema UTF-8 最多 64 KiB、深度 16、节点 2048、属性总数 512、枚举总数 1024；引用展开后同样检查。支持本地无环 `$defs` 引用，根 `$id` 可作注解；不允许嵌套 `$id`、远端/file/循环/dynamic/recursive 引用，校验不访问网络。pattern/patternProperties 的单个正则最多 512 字符。支持 date、date-time、time、uuid 四类 format，未知 format 拒绝。最终结果最多 1 MiB。
- 完整模型响应进入工具分发前检查。输出与普通/MCP/UI 工具混在同一响应、或一次多个输出均被拒绝，宿主不会先执行其中的业务工具。Provider 已在远端执行的原生工具无法回滚，不能据此宣称其未执行。
- 每个 Turn 最多两次格式修正；混调、严格模式下纯文字、JSON/Schema 错误共用额度；允许的文本正常结束不消耗纠错额度，也不清除此前失败。UI 恢复不重置；总模型请求上限优先，最后一次请求仍保留输出工具。DeepSeek/Kimi 思考模式使用模型能力配置允许的工具选择方式，严格模式仍拒绝文字绕过；混合模式保留输出工具并允许正常文字结束。
- Schema 校验运行于现有 Python/jsonschema 子进程。每 Worker 最多两个运行、16 个排队，排队和执行各最多 2 秒。超时/取消 kill 并回收，容量及系统错误直接停止，不让模型反复改内容；缓存最多 128 项。
- 每个 Run（含 UI Deferred 续跑）从 Agent 配置读取定义和允许文本策略，在内存中构造输出类型；同一对话约定配置保持一致，修改配置后应新建对话。事件不再保存完整 Schema 或配置引用，不从历史快照恢复配置；续跑只恢复真实执行次数、格式失败和尝试序号。历史快照保留原样，但不参与当前运行。

## 客户端协议

普通 HTTP 和 SSE `final` 的结构化分支 `output` 为原生 JSON，成功时额外返回：

```json
{
  "output_info": {
    "name": "lease_result",
    "output_id": "stable-event-id",
    "tool_call_id": "provider-call-id",
    "attempt": 2,
    "turn_id": "turn-id"
  }
}
```

允许的普通文本沿用 text 增量与 assistant_message 历史，没有 output_info、成功 output 事件或伪输出工具调用。必须通过 output_info／History kind 识别 JSON，不能按值是否为字符串、是否像 JSON 猜测；Schema 本身可以返回字符串。为保证失败响应文字不在实时回复泄漏，混合模式正文待完整模型响应检查并保存后才发布既有text事件，首个可见正文可能晚于供应商首个Token。关闭结构化输出的增量不变。同响应文字加合法输出调用时，文字保留为过程说明，JSON 为唯一接受结果；不要看到第一个 text 就结束收流，也不要将 output 和 final 再显示两遍。此前失败只留在轨迹。

关闭路径不增加 output_info；Pending 沿用原 deferred 结构，不产生已接受结果。输出尝试 SSE、Live 包装与 History 详见[公共 SSE 协议](sse-protocol.md)。只有 accepted 结果进入普通 History，新增 `kind:"structured_output"`，其 `item_id` 等于 output_id。失败候选只在 Event API/轨迹读取。

客户端按 output_id 去重，直接将对象交给业务校验，不必生成假 Markdown 围栏。格式修正中不要另开修复轮次；平台格式额度耗尽也不要自动另开轮次重置额度。已接受结果的业务基线/引用错误可按客户端既有有界流程处理；网络、下载、保存失败保留在实际失败环节。

Console 只提供通用正文/JSON，“完整 JSON／按结构分组”位于回复及输出详情内，标题使用英文编码。超过 15 行默认折叠，复制和下载保留完整值。轨迹失败和成功都标“输出”，状态分别 Error/Completed；没有独立“校验”标签。渠道投递将接受 JSON 确定性序列化成英文编码标记的 JSON 代码块；超过渠道文本字节上限时按 UTF-8 字符边界分段并标序号。首段结束原回复，后续通过现有渠道发送接口按确认串行投递；失败立即停止，不重发已经发送或确认未知的段。普通正文沿用原有流式回复。公共 API 仍保留原生值。

## 安装和备份兼容

包格式仍为整数 `schema_version:1`，只有一种导出。关闭时连 allow_text_output 的 true/false、其他 false/null 新键及草稿都不写入 Agent ZIP，因此可导入旧版；开启时在 agent.json 携带开关和完整定义，现有旧版会明确拒绝。开启包仅在允许文本时导出 allow_text_output:true，省略按 false。旧包导新版的新建和覆盖均关闭且 allow_text_output:false，覆盖还会移除目标旧定义，不能继承原开关。

新版忽略安全未知扩展，仍检查必要文件、已知类型、重复 JSON 键、ZIP 路径、重复项、加密、符号链接和资源预算。未知内容仍计入包摘要；预检后换包或目标配置变化需重新预检。全量配置备份保留关闭草稿，且仍要求源与目标数据库 Revision 相同。

## 数据库 013

两张表保持逻辑1:1：agents仅存structured_output_enabled总开关；agent_output_configs以agent_id为主键，name、description、allow_text_output各是独立列，字段定义仍整体放json_schema JSON列，不新增逐字段明细表，也不把整份输出配置再包装成config JSON。不存在配置记录时文本策略为false；没有合法定义时不能单独保存true。删除定义同时清除该策略，关闭总开关则保留完整配置草稿。对外API的allow_text_output仍为顶层字段，不改变Console或安装包格式。


保留012，新增013→012：agent_output_configs.allow_text_output Boolean NOT NULL，应用和数据库默认均为 false，现有 Agent 继续严格 JSON。与开关、定义、更新时间及可信更新人同事务保存。配置备份保留关闭草稿，仅同 Revision 恢复；历史011/012导出使用其真实物理字段，不能读取新列或改标冒充013。

回退前检查 true 草稿及历史冻结混合策略；存在时拒绝013→012，防止静默改变恢复和展示语义。生产发布先备份、按维护流程完成迁移和服务升级，再显式开启；本地升级不代表 AlphaPG 已发布。

2026-10-23 快照清理不修改 012／013 或上述数据库降级保护。运行时另有回滚限制：新版创建的 Pending 不再携带配置快照，已发布旧 Runner 会按普通 legacy Pending 接续，不能保证原严格结构化策略。回滚前先结束或取消新版 Pending，不让旧 worker 接手；旧 Pending 由新版接续则重新装配当前配置，保留原执行预算。已完成历史仍展示当时保存的真实结果，不重新校验。

## 部署与依赖

012 新增 `agent_output_configs`，并在 agents 增加非空默认 false 的 `structured_output_enabled` 和可空 `updated_by`。不添加结果表或 Run 表，不改历史迁移；输出事实仍在 chat_session_events。

先完成配置备份，再在维护发布流程中升级数据库及服务，确认 Revision=013、物理列/表及 allow_text_output 默认false、旧 Agent 默认关闭和原会话可读后，才按需显式开启。生产 DDL 耗时和锁影响须由运维结合实际数据规模/权限评估，不能以本地验收耗时替代。新旧镜像混跑、旧配置备份跨 Revision 恢复和向旧版降级须遵守迁移边界；已有新定义/更新人/输出事实时降级会保护性拒绝，不自动删除。

**依赖变更（运维）：安装集合与版本约束无变化（相对 v0.13.9）。** 0.14.3相对v0.14.2也无依赖变化。本次复用已有 Python、jsonschema、Pydantic AI 2.46.0 / Harness 0.32.0，不在发布时动态安装。校验 worker 新增直接使用 jsonschema 的必需传递依赖 referencing（jsonschema 4.26.0 要求 referencing>=0.28.4；本机为 0.37.0），固定镜像需确认原安装完整，无需新增可选 extras。此结论不代表已经核验线上镜像 digest 或完整清单；固定镜像交接规则见[依赖镜像](../../deploy/dependency-images.md)。

本版本不自动修改客户端、Agent 提示词、Skill 或线上开关。BOM/租赁的完整生产 Schema、真实业务写入及适配启用仍由各客户端另行验收。
