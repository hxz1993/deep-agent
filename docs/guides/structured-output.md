# Agent 结构化输出（0.14.0 / 数据库 012）

Agent 的“输出”页签默认关闭。关闭时沿用既有文字、工具和 UI 暂停协议。开启后配置一个结果编码、一段使用说明和一份 JSON Schema，最终必须提交符合定义的原生 JSON。中途仍可查询或等待 UI 确认；JSON 中的澄清问题只是数据，不自动触发暂停。

## 配置与权限

结果编码采用 1–64 位 ASCII 字母、数字、下划线，以字母开头，不能与已授权工具编码冲突。说明最多 8192 字符。外层字段可以全部非必须，也允许空对象；某字段出现后，其内部 required 等约束仍生效。无需按对话轮次动态换 Schema。

管理员使用现有创建/PATCH Agent 接口，一次保存开关和定义：

```json
{
  "structured_output_enabled": true,
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

`GET /api/v1/agents/{id}/output-config` 为管理员只读接口，返回 `structured_output_enabled` 和完整 `output_config`（没有定义为 null）。普通 Agent 详情不带管理 Schema；列表不逐项加载定义。

PATCH 省略字段保持原值；关掉开关不删除草稿。显式 `output_config:null` 仅在合并后的最终状态关闭时删除定义。开启但没有定义会拒绝整个事务。无效 Schema 的编辑内容保留在页面，页头保存前必须修正；详情无第二个独立保存动作。

Agent 的更新账号由可信登录身份写入，与开关、定义和更新时间同一事务保存。列表批量解析 Username，账号服务不可用时显示账号 ID，历史未知显示“—”；不从请求 Body 或安装包接受伪造的更新账号。

## 校验和恢复

- 使用 Draft 2020-12，不补默认值、不转换类型；省略、null、空串、false、0 保持区别。对象、数组和标量都支持；数组/标量在模型工具参数中的内部包装不会出现在公共结果里。
- Schema UTF-8 最多 64 KiB、深度 16、节点 2048、属性总数 512、枚举总数 1024；引用展开后同样检查。支持本地无环 `$defs` 引用，根 `$id` 可作注解；不允许嵌套 `$id`、远端/file/循环/dynamic/recursive 引用，校验不访问网络。pattern/patternProperties 的单个正则最多 512 字符。支持 date、date-time、time、uuid 四类 format，未知 format 拒绝。最终结果最多 1 MiB。
- 完整模型响应进入工具分发前检查。输出与普通/MCP/UI 工具混在同一响应、或一次多个输出均被拒绝，宿主不会先执行其中的业务工具。Provider 已在远端执行的原生工具无法回滚，不能据此宣称其未执行。
- 每个 Turn 最多两次格式修正；混调、纯文字、JSON/Schema 错误共用额度。UI 恢复不重置；总模型请求上限优先，最后一次请求仍保留输出工具。DeepSeek/Kimi 思考模式使用模型能力配置允许的工具选择方式，服务端仍拒绝文字绕过。
- Schema 校验运行于现有 Python/jsonschema 子进程。每 Worker 最多两个运行、16 个排队，排队和执行各最多 2 秒。超时/取消 kill 并回收，容量及系统错误直接停止，不让模型反复改内容；缓存最多 128 项。
- 首轮真实用户事件冻结定义，后续请求引用其事件和摘要。UI 恢复从原始 Event Store 取回，修改/关闭 Agent、压缩或重启不会换成新定义；快照损坏明确失败，不能静默采用当前配置。

## 客户端协议

普通 HTTP 和 SSE `final` 的 `output` 均为原生 JSON，成功时额外返回：

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

关闭路径不增加 output_info；Pending 沿用原 deferred 结构，不产生已接受结果。输出尝试 SSE、Live 包装与 History 详见[公共 SSE 协议](sse-protocol.md)。只有 accepted 结果进入普通 History，新增 `kind:"structured_output"`，其 `item_id` 等于 output_id。失败候选只在 Event API/轨迹读取。

客户端按 output_id 去重，直接将对象交给业务校验，不必生成假 Markdown 围栏。格式修正中不要另开修复轮次；平台格式额度耗尽也不要自动另开轮次重置额度。已接受结果的业务基线/引用错误可按客户端既有有界流程处理；网络、下载、保存失败保留在实际失败环节。

Console 只提供通用正文/JSON，“完整 JSON／按结构分组”位于回复及输出详情内，标题使用英文编码。超过 15 行默认折叠，复制和下载保留完整值。轨迹失败和成功都标“输出”，状态分别 Error/Completed；没有独立“校验”标签。渠道投递将接受 JSON 确定性序列化成英文编码标记的 JSON 代码块；超过渠道文本字节上限时按 UTF-8 字符边界分段并标序号。首段结束原回复，后续通过现有渠道发送接口按确认串行投递；失败立即停止，不重发已经发送或确认未知的段。普通正文沿用原有流式回复。公共 API 仍保留原生值。

## 安装和备份兼容

包格式仍为整数 `schema_version:1`，只有一种导出。关闭时连 false/null 新键及草稿都不写入 Agent ZIP，因此可导入旧版；开启时在 agent.json 携带开关和完整定义，现有旧版会明确拒绝。旧包导新版的新建和覆盖均关闭，覆盖还会移除目标旧定义，不能继承原开关。

新版忽略安全未知扩展，仍检查必要文件、已知类型、重复 JSON 键、ZIP 路径、重复项、加密、符号链接和资源预算。未知内容仍计入包摘要；预检后换包或目标配置变化需重新预检。全量配置备份保留关闭草稿，且仍要求源与目标数据库 Revision 相同。

## 部署与依赖

012 新增 `agent_output_configs`，并在 agents 增加非空默认 false 的 `structured_output_enabled` 和可空 `updated_by`。不添加结果表或 Run 表，不改历史迁移；输出事实仍在 chat_session_events。

先完成配置备份，再在维护发布流程中升级数据库及服务，确认 Revision=012、物理列/表、旧 Agent 默认关闭和原会话可读后，才按需显式开启。生产 DDL 耗时和锁影响须由运维结合实际数据规模/权限评估，不能以本地验收耗时替代。新旧镜像混跑、旧配置备份跨 Revision 恢复和向旧版降级须遵守迁移边界；已有新定义/更新人/输出事实时降级会保护性拒绝，不自动删除。

**依赖变更（运维）：安装集合与版本约束无变化（相对 v0.13.9）。** 本次复用已有 Python、jsonschema、Pydantic AI 2.46.0 / Harness 0.32.0，不在发布时动态安装。校验 worker 新增直接使用 jsonschema 的必需传递依赖 referencing（jsonschema 4.26.0 要求 referencing>=0.28.4；本机为 0.37.0），固定镜像需确认原安装完整，无需新增可选 extras。此结论不代表已经核验线上镜像 digest 或完整清单；固定镜像交接规则见[依赖镜像](../../deploy/dependency-images.md)。

本版本不自动修改客户端、Agent 提示词、Skill 或线上开关。BOM/租赁的完整生产 Schema、真实业务写入及适配启用仍由各客户端另行验收。
