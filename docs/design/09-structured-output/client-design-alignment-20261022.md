# BOM、租赁与 DeepAgent 结构化输出联合设计确认

> 2026-10-22；状态：**三方完成两轮设计对齐，均确认可以按修订方案接入；未启动实施，未验证端到端功能。**
>
> 用户授权范围：向指定的两个开发会话核对方案，收口后只更新 DeepAgent 课题。客户端开发、线上 Agent、提示词、Skill、提交推送和发布均不在本轮范围；后续由用户分别通知负责人。

后续状态：用户已另行批准本仓 0.14.0 实施及发布，平台验收见[报告](../../reports/Report%20DeepAgent%200.14.0%20验收/验收报告.md)。上述授权范围描述本次设计协调阶段，客户端适配仍由用户安排，不发开工通知。

## 1. 参与方、基线与确认凭据

| **参与方** | **评审会话 / 本次核对基线** | **两轮结论** |
| --- | --- | --- |
| DeepAgent | 本课题；`test@552b7b2`，产品0.13.9 / DB011，拟012；Core2.46.0 / Harness0.32.0 | 一套平台输出/校验/持久化/协议能力，可容纳两种不同业务契约，不内置客户业务判断 |
| 峰梅｜智能填报BOM | [指定会话](codex://threads/01a11ed6-a75e-7910-b799-0e798cad0bfe)；UX0.1.33 / `9b9d768`、Prompt V16.3 / Skill1.9.4（该会话只读核对） | 首轮确认可接入并提出两项公共契约明确化；第二轮确认“无剩余设计阻碍” |
| 达美乐｜租赁智能填报 | [指定会话](codex://threads/01a0ec2c-20a3-71c1-ba7c-f26950c9e455)；`lucia_dev_1016@34a4998`（该会话只读核对） | 首轮确认可接入，补文件复核与流式体验边界；第二轮确认“没有剩余设计阻碍” |

确认对应的会话轮次，便于用户在原会话复核：

- BOM首轮：`01a15f65-f503-7c51-8cf8-581d398a1be4`；最终消息 `msg_0874797900f7e759016ad8f026deac819194b20e3ff499feea`。
- 租赁首轮：`01a15f65-f5ae-7ea2-9a54-218fd2e4bc5f`；最终消息 `msg_06194652dcac009d016ad8f05e158087d08a34f4b54154c984`。
- BOM第二轮：`01a15f6c-9686-7c70-ac64-7b0750f2ff78`；最终消息 `msg_0874797900f7e759016ad8f0d598288191aad15365f16e0559`。
- 租赁第二轮：`01a15f6c-971a-7ce1-9032-009d677f312c`；最终消息 `msg_06194652dcac009d016ad8f0d6360887d0ba8661809456b178`。

第二轮双方确认文字：

> BOM：“可按此修订方案接入，无剩余设计阻碍。”

> 租赁：“仍可按此修订方案接入，没有剩余设计阻碍。”

以上是**设计可用性确认**，不是已经升级客户端、编译完整业务 Schema 或跑过线上回归。两个会话本轮均声明未修改文件/线上、未开始实施。

## 2. 共同复用能力与不可混淆的边界

### 2.1 共享平台能力，不统一客户业务根对象

共享的是 Agent开关、一份固定JSON Schema、输出校验/重试、最终接受、公共SSE/History/Live、轨迹与安装迁移。BOM不必套用租赁示例中的text/contract字段；租赁也不必改成BOM的revision和snapshot形态。

两边都可以把一轮需要组合的业务内容放进**一个最终JSON对象**。JSON Schema可配置内部required、静态互斥与条件约束，但不按首轮/续轮动态换工具；真实业务状态继续由客户端检查。

### 2.2 平台身份与业务身份分开

| **层级** | **字段** | **用途** |
| --- | --- | --- |
| 平台envelope | session_id、turn_id、run_id、output_id、tool_call_id、name | 会话归属、Run/Deferred关联、输出尝试和传输去重 |
| BOM业务JSON | schema_version、turn_id、base_revision/base_hash、revision、source_signature、project/version | BOM请求基准、来源、目标与业务状态演进 |
| BOM请求上下文 | submission_id、rules_version及其他已存在控制项 | 用户提交幂等和规则选择，保留原位置，不因为平台字段增多而塞进业务最终包 |
| 租赁业务JSON | 各产物schema_version、revision/base_revision/base_hash、patch_id、question_set_id、snapshot_signature | 合同、问题集和复核各自状态及业务幂等 |

不得把平台turn_id写回BOM的业务turn_id，不得用output_id替代patch_id/用户submission_id，也不删除业务schema_version。平台不加虚构输出版本，不等于禁止业务对象保留真实版本字段。

### 2.3 三个不同完成点

1. 平台accepted：本次输出按冻结Schema合法，且已经持久化。
2. 客户端adopted：该业务结果通过来源/基准/引用等检查，被并入当前草稿。
3. 用户批准及业务执行成功：按项目既有授权、不可变计划、写入和回执规则完成并独立读回。

accepted不替代2或3；`operation_advice.fill.allowed`也不代表填写、保存、生成已经成功。

## 3. BOM 最小接入映射

### 3.1 根包与阶段

| **项目** | **确认后的映射** |
| --- | --- |
| 输出编码 | `bom_turn_v3` |
| final.output | 直接是原`BomTurnV3`对象，不增加`{bom_turn_v3:...}`层，也不转成假围栏 |
| 保留业务根字段 | schema_version、review_mode（适用时）、turn_id、base_revision、base_hash、revision、source_signature、project、version、summary、snapshot、patches、question_set、question_receipts、review、next |
| 首轮识别预览 | snapshot.source_model与完整来源引用行；patches=[] |
| 后续确认/复核/整理 | snapshot=null＋稀疏patches，保留patch_id/node_key/changes/reason |
| 正式行方案的问题表达 | question_set=null、question_receipts=[]；需确认行由review.row_assessments/issues等与备注表达 |
| 后续动作 | review、next继续业务协议，不因平台final成功而跳过人审或自动保存 |

首轮/后续结构可用同一Schema的组合/条件约束表达。来源签名是否等于当前文件、revision是否递增、来源是否覆盖全行、目标是否真实存在、父链/字段依赖和补丁是否重复仍由BOM客户端检查。

### 3.2 客户端需适配的读取位置

| **入口** | **设计要求，尚未修改** |
| --- | --- |
| `src/services/agentProtocol.ts` SSE接收 | 新增原生对象分支；旧字符串分支服务关闭Agent及旧历史，不把新对象再序列化成围栏走正则 |
| `src/services/bomTurnProtocol.ts` | 将围栏解码和对象业务校验分层，共用原来的业务检查/reducer |
| `src/services/workbenchTurnFlow.ts` | 终态等待同时识别structured_output；不能仅寻找assistant_message/final |
| History回放/时间线/失败恢复 | 按真实项类型读取接受对象；未知提交不自动重发，刷新只读恢复 |
| 查询证据和原生审批证明 | 按已提交输出及真实已执行MCP事实定位截止/来源，不只取最新文本助手消息 |
| 批准/执行/回执 | 既有用户批准、不可变计划、成功项不重发和独立回读保持 |

本轮已核对源码入口，不生成完整BOM Schema，不改变Prompt V16.3、Skill1.9.4及其线上配置。

## 4. 租赁最小接入映射

### 4.1 一个组合结果，保留既有产物

输出编码为`lease_result`。可选顶层键如下，内部仍沿用原业务格式：

| **字段** | **保留的核心规则** |
| --- | --- |
| text | 普通说明，不作为业务产物或授权 |
| lease_contract | LeaseDraft；schema_version/revision/title/summary/coverage/issues/result；新首版operation_advice.fill，字段/费用模型/证据/确认信息 |
| lease_contract_patch | schema_version/base_revision/base_hash/revision/patch_id/operations及完整合并校验 |
| lease_clarification | schema_version/question_set_id/revision/stage/fields/receipts与适用的合同/复核基准 |
| lease_review | schema_version/revision/snapshot_signature/summary/findings/changes；现代模型修正另走合同patch |
| lease_review_file | 原有file_id/sha256/snapshot_signature/revision文件引用，不是模型输出里内嵌文件正文 |

顶层可全部不必须，允许仅正文、仅回执或空对象。**省略产物不清空已有合同或复核状态**。不能套用“至少一个必须”的统一平台限制。

同一份固定Schema添加两项静态互斥，下面只是外层约束片段，不能替代完整业务Schema：

```json
{
  "type": "object",
  "allOf": [
    {"not": {"required": ["lease_contract", "lease_contract_patch"]}},
    {"not": {"required": ["lease_review", "lease_review_file"]}}
  ]
}
```

完整合同＋澄清、patch＋澄清、review＋patch、仅正文、仅业务回执等仍能由一份定义表达。当前是否已有合同基线、问题集版本是否正确、before/after是否匹配、允许路径与阶梯父子依赖继续由客户端判断。

### 4.2 客户端需适配的读取位置

| **入口** | **设计要求，尚未修改** |
| --- | --- |
| `client.ts`、`useLeaseAgent.ts` onEvent/consumeCompletedFinal | 接收原生output＋output_info；失败output尝试不生成运行错误步骤，不触发外部repair |
| `finalProtocol.ts` reduceLeaseFinal | 抽出对象处理入口，围栏仅是旧路径解码；保留原业务校验和应用顺序 |
| `finalConversation.ts` replayLeaseConversation、historyToLeaseMessages/restoreHistory | 处理structured_output，旧围栏和新JSON可在同会话按事件顺序重放 |
| `reviewArtifact.ts` | 对象文件引用入口，保留下载大小/Hash/真实快照/版本/权限检查；历史恢复也需重新验证引用 |
| repairOutput | 用被业务拒绝的output_id及当前基准关联，不能继续只比较finalText或重做所有业务查询 |

问题集独立revision、答案submission_id与receipts不删除；已暂缓/回答事项不能在恢复后变成未回答。客户端填写、保存、生成、完整读回、复核与失败停点均沿原规则组织。

### 4.3 大复核的文件引用边界

- 平台1MiB限制约束内联JSON结果；租赁现有复核文件下载5MiB限制仍由原客户端验证。
- 大复核可通过lease_review_file传原有引用，不强迫全量内联，不截断JSON，也不自动提高平台限额。
- file_id不是访问授权；依旧使用原文件来源/会话归属/下载鉴权和内容Hash校验，不新增公开URL或通用平台文件服务。
- 文件失效、Hash错误、下载超时和快照过期应停在相应客户端环节，不能一律重调模型或自动应用部分结果。

### 4.4 流式体验的明确范围

首版公共协议不提供输出参数的partial事件。租赁当前从过程围栏生成字段级预览的方式**不计为本版保留能力**，客户端未来采用：

```text
过程进度/文字展示 → 已持久化accepted → 完整结构展示 → 业务核验/人审/操作
```

不能从过程文字围栏提前采用合同/问题集。若以后需要字段级候选预览，应另行定义尝试身份、只读性、替换/失效与最终接受规则，不在本次默认为已支持。

## 5. 公共协议的两项明确修订

### 5.1 跨通道唯一身份

对每个History `kind:"structured_output"`项，规范强制：

```text
history.item_id == 对应持久化output/attempt.event_id
                == SSE output.output_id
                == final.output_info.output_id
                == Live包装中同一结果的output_id
```

不再要求客户端从示例猜测映射。History不必重复增加output_id键，但必须稳定遵守等式。UI按平台ID去重，业务仍按自身patch/提交ID幂等。

### 5.2 混调拒绝的工具项不进入普通History

整批拒绝后，未分发的普通/MCP/UI ToolCall：

- 保留原始响应与not_dispatched证据，Event API/轨迹可查，标明未执行。
- **不投影成普通History工具项**；不显示为已执行、等待UI或可操作卡。
- 不计入工具执行数，不作为查询结果/原生审批证明，不产生假成功ToolReturn。
- 原始Provider Usage仍如实统计；供应商内部实际已执行的native行为不能被本地标记抹去。

BOM审批与租赁业务证据读取必须只采用实际成功执行、归属正确且足够完整的工具事实。

## 6. 两层纠错与 UI 等待

- JSON/Schema错误在平台同Turn预算内修正；失败output帧只是一次尝试，客户端不终止Run、不另开自动repair。
- 平台格式重试耗尽不能自动转换成客户端新轮并重置同一格式预算。用户后续显式操作沿既有产品流程处理，不能后台无限重试。
- accepted后若业务hash、revision、引用或依赖不正确，客户端可以走原有**有界业务repair**，关联被拒output_id、用户原提交和当前业务基准，保留已有有效状态。
- 网络、下载、文件失效、写入/保存拒绝不是统一的“模型输出坏了”，停在真实环节，不一概重调模型。
- BOM needs_adjustment及租赁final中的澄清JSON是业务数据，用户下一普通轮回答，不自动创建平台Pending。
- 平台保留真正Deferred能力不要求两项目现在启用。租赁旧挂起UI会话维持现有只读策略，不能借本次接入自动恢复。
- 未来显式使用Deferred时按原tool_call_id、平台Turn、冻结Schema和剩余预算续跑；不能继续假定用户输入与最后输出一定同一Run，不能把业务JSON的turn_id当平台Turn。

## 7. Schema容量与启用门禁

两项目尚未生成、编译并在真实Provider请求中验证完整生产Schema。不能凭最小样例就宣称已满足64KiB、展开后深度/节点/字段数、模型工具参数限制及性能预算。

实施时：

1. 业务负责人提供完整Schema，复用本地$defs，保留真实业务版本与约束，不用空对象桩冒充完成。
2. 平台验证原始与展开后大小、层级、节点和字段预算、循环/外部ref限制。
3. 捕获实际Provider请求，确认optional未被改成所有字段必须，互斥/条件与根类型符合预期。
4. 用首轮全量、续轮稀疏、复核/文件两路径和实际体量样本回归；若超初始限额，以测量数据评估调整，不静默放宽，也不通过删除业务约束挤入。
5. 客户端已适配并通过联合回归后才显式开启Agent；不适配的旧客户端继续关闭。

## 8. 后续工作边界与文档落点

本轮只修改DeepAgent09课题：主设计、公共协议、回归清单与本记录。两边代码、提示词、Skill、线上Agent、数据库和版本都不修改；也不发开工或发布通知。

联合用例在[测试计划JNT组](structured-output-test-plan.md#19-三方设计对齐后的联合回归)集中维护；所有用例仍为待执行。普通接口/底层回归仍保留原矩阵，不用业务联合用例替代安装、迁移和持久化故障测试。

后续若用户进入计划模式并启动开发，本平台按完整设计落地；两个客户端由用户分别交接，不默认在本会话串联开发或自动切换线上开关。最终停止点是三方设计确认和文档核对完成，**不是业务功能已经实现或发布**。
