本交接用于本地既有开发会话继续研究和设计 DeepAgent 的中间产物管理能力。记录日期为 2026 年 11 月 6 日。内容保留本次讨论的业务动机、最终修正、已核实代码、同行先例、候选数据模型与未决问题，不代表已批准实施或修改仓库。

**已经确定的技术方向：原生 JSON 字段保存可控规模的配置和草稿，Agent 提交增量 patch，服务端校验、合并和保存。** MySQL 使用 JSON，PostgreSQL 使用 JSONB；首期不额外维护 TEXT 兼容路径，遇到实际旧系统限制再适配。

**尚未确定的产品抽象：轻量的会话产物，还是带目标与多个产物的 Mission。** 不应因为报告场景涉及多步执行，就把所有产物都变成工作流。独立产物服务不统一执行业务；前端、后端或 Agent 工具按各自能力消费产物，执行结果再反馈给产物和会话。

本文的“代码事实”来自本地只读检查；“行业事实”来自官方文档及公开源码，未登录各产品实测；“候选设计”仅供后续开发讨论。

# 一 决策清单与明确边界

## 已确定

1. 中间产物需要可直接读取和更新，不能每次仅从聊天历史中抽取并拼装。

2. 存储对象以配置、业务草稿、字段映射、查询计划及受控报告内容为主，不是把业务数据库或海量查询结果复制进来。

3. 优先直接入库，采用原生 JSON 类型与增量 patch。文件不是这些小型结构化对象的默认权威来源。

4. 模型给修改意图，程序负责真正应用修改。模型不必每轮重写完整大 JSON。

5. 专业页面负责业务交互，业务程序负责计算、导入、填报或渲染；产物服务不需要知道每类业务如何执行。

6. 可以先只保留最新内容，不强制实现完整版本历史或每次增量日志。

7. 原有本地仓库保持只读，本次只形成研究与交接材料，开发由用户已有本地开发会话继续。

## 尚未决定

- 顶层叫产物、会话产物、Mission，还是其他业务名称。

- 首版是否建立 Mission 表，或先给产物一个 session_id。

- 是否立即支持一份产物关联多个会话，以及跨会话续作的具体体验。

- schema 的注册、版本和验证职责如何在平台与业务插件之间分工。

- 确认、执行回执和问题反馈采用独立表，还是复用现有记录并建立关联。

- 首版是否提供通用产物管理界面，以及每种类型如何选择专业渲染器。

- 报告的多步运行定义是否作为后续独立能力，不应在底层存储设计中提前固化。

## 本次讨论明确排除的过度设计

不要求每个业务用户成为低代码开发者，维护、发布和管理一堆工作流或脚本；不要求把所有业务表达成一个万能 JSON DSL；不要求产物服务预置十种任务执行器；不要求所有交互绕回 Agent 调用前端工具；不要求第一期完整审计版本库或复杂调度平台。

可以提供独立接口，但独立接口不等于必须马上拆成独立微服务。挂在 DeepAgent 内部也是可行起点。

# 二 问题本质是持续维护同一份业务内容

合同导入、BOM 填报、对账工作台与预算报告都有一段持续交互：用户上传材料、检查结果、提出问题、澄清歧义、修改字段，最终确认由程序采用。专业页面中的步骤一、步骤二、表单、候选项和错误提示，本质上是一种更结构化、更界面化、边界更明确的业务对话。

自由聊天仍存在，但前端不能无边界地由模型随意生成。大多数业务页面、控件和交互事先设计好，模型输出的结构化内容供这些控件消费。对话和页面不是两份独立事实，应围绕同一份当前产物协同。

原来每轮返回全量 JSON 有三个实际问题：输出长、延迟高；未修改内容可能被遗漏或抄错；客户端很难判断本轮到底改了什么。因此已有项目改成首轮建立完整基线，后续提交增量。

本地 Agent 的类比很直观：建立一个文件，然后持续编辑同一个文件。云端会话虽然通常落在数据库，却缺少同样直接的“当前可修改对象”。若只依赖沙盒目录，又要额外解决身份、描述、映射、检索、引用、权限和计算资源回收后的可用性。沙盒仍有价值，但它是处理和执行手段，不必成为业务产物的权威管理层。

这里的关键对象不是“某次模型回复”，而是“这次任务正在维护的内容”。一条回复可以提出修改；被接受的修改更新当前内容；其他程序可以通过稳定接口直接取到它。

# 三 四类业务场景及共同点

## 租赁合同

输入包括合同、补充协议、通知函、主数据和用户补充。第一轮产生合同草稿，包含字段值、置信度、解释、原文定位、待澄清项与费用模型。用户在专业页面和对话中确认或修正，后续只改有关字段。最终前端或宿主程序填入合同表单、保存并计算付款计划。

这不是单纯 OCR 或字段复制。例如逐月固定租金与提成取高后按季度支付，和整季取高并不等价；保底是否已含物业也会改变结果。产物可以承载严谨的业务表达，但执行算法仍由租赁模块负责。

## BOM

输入是客户原有成本底稿，而不是强迫客户改填统一模板。需要区分物料身份和装配关系，确认父子层级、单位、用量、材料、规格和已有物料候选，再形成可执行的填报计划。

一个 Parts 可出现在多个 BOM 节点中，164 个节点不等于 164 种物料。节点用量通常相对于直接父件，不能都解释成总成总用量。重新导入还涉及新增、移父和共享物料。产物承载这些已解释的决定；执行者按业务规则导入并回读。

本地定位到的 BOM 材料不能直接认定为用户口述的“中煤”：当前资料出现“峰梅”及 BP000872、BP000900、BP000927 样例，客户名称尚未独立确认。也不能把该填报案例扩写为已实现的完整预算计算模块。

## 对账工作台

用户描述的是大弹窗或专业工作台：选择两边账单和单据，确认对账范围、匹配条件与每一步输入，查看问题，再持续调整。可以把匹配配置、字段映射、待确认差异和处理决定作为产物；执行结果或错误再回到页面及 Agent。

在指定本地目录的有界搜索中，尚未找到能确认就是该独立工作台的代码。发现的中田 SPM 对账材料、只读证据控制台和数据流“对账配置推荐”不是同一实现。因此此场景目前属于用户需求描述，不是已核实代码事实。

## 预算分析报告

这是更开放的场景。用户可持续改变查询范围、分析方法、展示和解释，不能提前穷举所有分析功能为一个巨大 DSL。可能由 Agent 维护查询计划、Python、HTML 或其他报告结构，程序按固定接口执行；有些步骤本身仍可调用 Agent。

报告是复杂上层用例，不能反推普通合同字段 JSON 也必须拥有工作流引擎。

## 共同抽象

共同点是：来源材料 → 当前产物 → 修改与专业校验 → 确认 → 消费执行 → 结果反馈。通用层可以管理身份、内容、类型、修订、关联和访问；具体字段、确认规则、执行顺序和业务含义属于消费方。

# 四 报告产物应拆成什么

以下是用户提出的组成，不是已实现的统一协议：

1. **POV 和查询范围**：期间、组织、版本、场景、币种或其他维度组合。它可以独立修改，并影响下游数据。

2. **查询计划 JSON**：指定查询哪个财务模型、Legal model 或其他服务，参数、过滤条件、依赖与查询顺序。它是可展示、可检查的计划，不应仅是一段自然语言意图。

3. **Python 或其他处理脚本**：调用计划完成查询、清洗、计算和整理。它可以产生已经处理好的报告数据源。

4. **处理后数据**：供渲染和解释使用的命名结果。小型摘要可作为产物，大批数据应另有受控引用，避免把产物库变成业务明细仓库。

5. **HTML 模板或报告正文**：包含布局、图表、指标、关注点和解释，绑定前述数据源。程序可渲染预览，用户也可要求调整。

6. **运行反馈**：查询、计算、校验和渲染的错误及结果引用，必须知道对应哪份输入和修订。

需要保留三个不同操作：用现有结果重新渲染；用新参数或数据重跑保存的逻辑；让 Agent 修改方法或重新解释。它们可以组合，但不应混成一个不可解释的按钮。

**复跑不等于完全排除 AI。** 用户明确修正过这一点：五步流程里第二步、第四步都可能是 Agent 分析，也可以在阶段结束后检查结果，决定继续或返工。复跑指保存并重新执行既定组织方式，不是每次重新自由规划；也不只是最后加一段 AI 总结。

另一方面，产物服务不必成为这个执行器。报告客户端、后端或现有运行环境消费保存的计划和脚本即可。普通业务用户不需要手工开发并发布流程。代码可以由 Agent 维护，但权限、隔离和执行能力必须由受控系统授予。

查询范围改变会使数据及相关解释失效；主题颜色改变可能只需重渲染。哪些依赖需要重算，应由报告消费者定义。旧 AI 结论不能无声地配到新数据上，可以按步骤重新调用模型，或将其标记为待重核。

# 五 Mission 与产物的两档候选

## 方案 A 轻量会话产物

提供独立产物接口，每个产物关联来源会话或所属会话。客户端可以直接列出和读取这个会话的当前合同草稿、BOM 计划或报告，不必回放全部消息。

适合先解决“当前内容在哪里”的问题。实现较少，但要明确会话删除、换会话续作、多份产物、共享和跨客户端访问的行为。若 session_id 成为强生命周期边界，后续跨会话扩展需要迁移。

## 方案 B 薄 Mission 加产物

Mission 保存名称、长期目标、来源会话、所属空间和业务状态。多个产物挂在其中，未来可以关联多次对话。Mission 是任务组织容器，不自带调度、计划执行、审批或发布系统。

它适合“同一个任务有多份可直接修改的内容，并持续维护”的体验。目标可以帮助 Agent 保持任务方向，但这不等同用户长期记忆：任务目标的作用域、权限和生命周期应明确，不应自动变成全局用户画像。

Mission 与 Pipeline 不是同义词。Mission 可没有流水线，Pipeline 也可只是某个报告产物。一次运行 Run 则是消费某份定义及输入后的事实记录，不应与长期目标或当前草稿合并。

## 建议的选择标准

先按真实客户端需求选择，而非因命名完整而增加表。若首期只有一个会话维护几份当前 JSON，A 足够；若已明确需要独立任务列表、长期目标、跨会话继续与多产物组织，B 更顺。当前会话没有最终拍板 Mission，后续设计必须把它保留为开放选项。

# 六 存数据库与 JSON 类型的最终方向

用户认为这些对象规模可控：不存海量业务数据，配置也不会比既有数据流或页面元素定义更大。因此直接入库比文件加数据库映射更简单。这个结论来自对象实际体量和访问模式，而不是 P 与 NP 的复杂度分类；算法时间复杂度不能直接证明 JSON 大小或数据库写入成本。

**本期选择：MySQL JSON 或 PostgreSQL JSONB，统一上层 patch 协议，不先做 TEXT 兼容。** 两种数据库有存储适配差异，但不需要两套业务流程。

需要修正一项版本记忆：**MySQL 从 5.7.8 起已支持原生 JSON 类型。** 若此前“5.7 不能用 JSON”，需核实具体小版本、兼容数据库、ORM 或平台字段限制，不能泛化为所有 5.7。参考 [MySQL 5.7 JSON 类型](https://dev.mysql.com/doc/refman/5.7/en/json.html)。

JSON 类型保证合法 JSON，不保证业务 schema 正确。合同必填字段、BOM 环路、类型、金额口径及跨字段约束仍由应用校验。PostgreSQL 中优先讨论 JSONB，不把保留输入文本的 JSON 与 JSONB 混同。

文本脚本或 HTML 也可以放在 JSON 包装对象里，例如 language 与 source 字段；这仍是一套存储接口。但一个巨大 source 字符串的微小文本修改，不会自动变成 JSON 字段级细粒度编辑。可以先替换该字符串，或以后增加受控文本编辑操作，别把 JSON Pointer 描述成源码语义编辑器。

PDF、原始账单、图片、超大结果等仍适合文件或对象存储，产物内容引用 file_id、哈希和必要元数据。本次选择“配置入 JSON 库”不等于所有附件都必须塞进数据库。

# 七 三种增量不能混淆

## 模型输出增量

模型只提交要修改的字段或片段，避免重抄未变内容。这主要减少输出量、延迟和误改概率。它不保证模型永远不读完整上下文；首次构建、复杂依赖或修复可能仍需读取较大范围。可按字段读取和提供摘要，但不能绝对承诺不消耗全量输入 token。

## 服务端合并增量

服务读取当前内容，检查基准修订，应用 patch，校验完整结果，然后保存。即使数据库最终写回整个对象，模型仍只输出增量，两者并不矛盾。

## 数据库物理局部更新

MySQL 的 JSON_SET、JSON_REPLACE、JSON_REMOVE 在特定条件下可触发部分更新优化，例如同一 JSON 列作为输入和输出，并满足大小和空间条件。不是所有 JSON 修改都只写局部物理字节。参考 [MySQL 8.4 Partial Updates](https://dev.mysql.com/doc/refman/8.4/en/json.html#json-partial-updates)。

PostgreSQL 的 jsonb_set 或下标赋值提供按路径更新语义，仍受行级并发控制，不是给每个 JSON key 一把独立锁，也不能保证仅修改磁盘上的几个字节。参考 [JSON 类型](https://www.postgresql.org/docs/17/datatype-json.html)、[JSON 函数](https://www.postgresql.org/docs/17/functions-json.html)。

TEXT 也能做 Agent 增量：应用层 parse → patch → validate → serialize → UPDATE。部分 JSON 函数还能接收合法 JSON 文本，但原生类型、索引和物理优化能力不同。本期不做 TEXT 兼容是简化选择，不是技术上不可能。

# 八 候选产物表与关联表

以下是逻辑字段建议，不是已存在数据库结构，也不是执行 DDL 的授权。字段名、ID 类型、租户体系与现有 ORM 应由本地开发会话对齐。

## artifacts 当前产物表

| 字段 | 用途 |
| --- | --- |
| id | 稳定产物身份，不以消息序号替代 |
| tenant_id 或 space_id | 权限及隔离归属，沿用平台已有模型 |
| owner_id | 创建或归属主体，不能从模型自报身份可信写入 |
| artifact_type | lease_contract、bom_plan、report_spec 等类型标识 |
| title | 人可读名称 |
| schema_id 与 schema_version | 内容约定及其版本，可按业务逐步引入 |
| content | MySQL JSON 或 PostgreSQL JSONB，保存当前规范化内容 |
| revision | 单调递增，用于条件更新，不要求保存每版历史 |
| content_hash | 可选，用于一致性或去重；需定义规范化方式 |
| source_session_id | 轻方案的来源或归属会话 |
| created_at 与 updated_at | 时间 |
| updated_by | 可信调用身份或执行主体 |
| metadata | 小型扩展元数据，不能变成无约束杂物箱 |

不要用一个含糊 status 同时表示“格式合法、已确认、已执行、已发布”。若保留 status，只定义清楚的通用生命周期；业务状态放在业务内容或关联记录中。

## artifact_links 关联表

在一产物多来源、多会话或多派生关系出现时引入：id、artifact_id、relation_type、target_type、target_id、可选 artifact_revision/target_revision、metadata、created_at。

可表达 source_file、source_session、business_record、derived_from、execution_receipt 等关系。查询前必须核验两端访问权限，关联本身不能授予目标访问权。首版若只有单会话归属，可暂缓通用关联表，不为未来所有关系预建复杂框架。

## 可选 missions 表

id、tenant_id/space_id、owner_id、title、goal、source_session_id、status、created_at、updated_at。产物增加 nullable mission_id 或通过关联表连接。

它只组织目标和内容，不包含预设执行器清单。若未选择 Mission，不需要先建一张空壳表。

## 可选反馈记录

若现有业务系统已有确认、导入和执行日志，直接关联它们；没有时可新增轻量反馈表或同一产物接口下的受控反馈类型。至少包括目标产物及修订、operation_id、状态、错误或结果引用、发生时间和可信来源。

反馈通常是发生过的事实，建议追加记录，而非把失败信息写进正在编辑的业务 JSON 正文。第一期可以很轻，不需要完整事件溯源系统。

# 九 候选 patch 接口和处理过程

以下接口是根据现有项目抽象的候选，不是已实现的统一 API。可先支持 add、replace、remove，避免一开始承诺 RFC 6902 所有操作。

```json
{
  "artifact_id": "artifact_123",
  "base_revision": 7,
  "patch_id": "request_unique_id",
  "operations": [
    {
      "op": "replace",
      "path": "/fields/rent_start/value",
      "value": "2026-10-30"
    },
    {
      "op": "remove",
      "path": "/issues/obsolete_issue"
    }
  ]
}
```

例子只说明协议形状。路径必须匹配实际 schema；数组不能把业务 ID 当作下标使用。对 BOM 节点这类稳定身份对象，可用按 node_key 的业务补丁，或将对象按 ID 索引，避免数组插入导致所有索引漂移。

服务端步骤：

1. 验证登录身份、租户、产物权限、类型和允许的修改范围。

2. 读取当前修订和必要内容，检查请求大小、操作数、深度及路径。

3. 检查 base_revision；过期时返回冲突，不静默覆盖。

4. 在内存副本应用 patch，不修改已发布引用或原对象。

5. 校验完整新内容：JSON Schema/类型约束，以及业务模块提供的检查。

6. 通过短事务或条件 UPDATE 原子保存 content 和 revision。

7. 返回新修订及所需片段或完整内容，由调用方决定如何刷新。

8. 将失败原因以结构化工具结果交给 Agent 或页面，不伪称成功。

JSON Pointer 中斜杠和波浪号有转义规则；remove、显式 null、缺失字段应区分；不能把“模型没输出字段”理解为删除。对于金额精度、日期、枚举、空值和数组语义，需要在具体 schema 中约定。

若服务本身不掌握业务语义，可提供 schema registry 或验证回调接口。通用层至少守住合法结构、权限和条件写入；专业校验依旧属于业务模块。不能把“保存成功”描述为“业务一定正确”。

# 十 并发和数据库写入

用户判断“通常一个会话更新一行，冲突不多”是合理的负载预期，但仍可能出现页面刚改完、较早 Agent 响应随后到达；多个标签页、重试、后台任务也可能写同一对象。无需因此做复杂分布式锁，最低限度的条件修订即可处理陈旧写入。

候选 SQL 语义：

```sql
UPDATE artifacts
SET content = :merged_content,
    revision = revision + 1,
    updated_at = :now
WHERE id = :id
  AND tenant_id = :tenant_id
  AND revision = :base_revision;
```

受影响行数为 0 时，区分权限/不存在与修订冲突，重新读取后由调用方决定重新计算修改。不要盲目把旧 patch 重放到新版本。

另一种做法是在短事务内 SELECT FOR UPDATE，读取最新内容、应用补丁并保存。**不要在持有数据库锁时等待模型生成、外部查询或用户确认。** 模型先生成修改请求，提交阶段再进行短事务。CAS 与行锁可按具体路径选择，不必两个机制机械叠加。

MySQL JSON_SET 与 PostgreSQL jsonb_set 可以把简单 path 更新表达在 SQL 中，但通用 JSON Patch 到各数据库函数的翻译并非零成本，尤其 remove、数组和多操作原子性。首版允许应用层完整合并，再用 JSON 类型保存。若实测体量和吞吐需要，再优化 SQL 局部路径更新。

patch_id 可用于幂等，但若服务端没有保存足够的处理记录，单有该字段不构成可靠去重。响应丢失时应能查询操作结果或当前修订；不要在执行结果不明时自动重复业务提交。

只保留最新版不妨碍 revision 防覆盖。若未来需要审计、回滚或可复现运行，可再引入 artifact_versions 或运行快照，不必把这项后续能力写成首期前置条件。

# 十一 确认 执行与反馈的闭环

Agent 生成字段映射后，前端直接填充是合理的，不必为了“全部由 Agent 执行”再建立专门前端工具。Agent 自己有合适工具时，也可执行查询或脚本，但只是消费方之一。

确认应绑定具体内容与范围，而不是只存一句“用户同意”。候选结构：

```json
{
  "artifact_id": "artifact_123",
  "revision": 8,
  "scope": ["fields/rent_start", "fee_models/model_a"],
  "decision": "confirmed",
  "source_event_id": "event_456"
}
```

确认人和时间由可信服务写入。内容改变后哪些确认失效，交给业务消费者按依赖判断；通用存储服务不应猜租金或 BOM 的业务关系。

执行反馈例子：

```json
{
  "artifact_id": "artifact_123",
  "revision": 8,
  "operation_id": "operation_789",
  "status": "failed",
  "errors": [
    {
      "code": "UNKNOWN_CUSTOMER",
      "path": "/fields/customer_id",
      "message": "目标系统未找到该客商"
    }
  ],
  "result_refs": []
}
```

状态至少能区分已提交、成功、失败、部分成功和结果未知。超时不一定意味着没有执行；业务执行器负责幂等、回读与确定事实。反馈可有大日志文件引用，不必在正文堆放日志。

必须区分四层：结构校验通过、业务采用、用户确认、实际执行成功。DeepAgent 的 accepted structured output 仅能证明某种输出接受或校验，不可替代其他层。

# 十二 本地代码证据

## DeepAgent 平台

检查时目录为 /Users/wuzhiruge/PycharmProjects/deepfos-knowleage_base/deepfos-AI/deep-agent，分支 test，HEAD 03578bdf，标签与产品版本 v0.15.13。这是本地快照，不代表线上环境已部署或测试通过。

- src/deep_agent/schemas/chat.py:12–82：普通请求包含 agent_id、session_id、message、attachments、context_refs。

- schemas/context_reference.py:42–76：context_ref 包含 key、type、schema_version、label、payload、metadata；payload 给模型，metadata 用于显示/历史。

- docs/guides/ui-tools.md:253–266 及 api/v1/chat.py:345–453：真正等待 UI 工具时，通过 deferred_tool_results 和 tool_call_id 续跑。

- agent/structured_output.py:223–256、db/conversation.py:404–418、conversation/projector.py:61–71：结构化输出记录及 History 投影。

- db/models.py:270–305、350–402：现有 chat_sessions、chat_session_events。source_type/source_id 当前是渠道来源，不应未经设计直接当通用产物关联。

普通业务澄清的 final 结束本轮，下一轮沿用 session_id，不等于执行栈一直暂停。合同和 BOM 主流程不能与 Deferred UI 工具机制混说。

## 租赁合同

源码根：/Users/wuzhiruge/PycharmProjects/deepfos-work/design_0929_lease_contract_parsing/ai-fill。

- ai-fill/src/agent/draft.ts:36–47：LeaseDraft 包含 schema_version、revision、coverage、issues、result、document_evidence、operation_advice。

- ai-fill/src/dify/types.ts:68–110：字段包含 key、label、value、confidence、reason、source_blocks、confirmation；confidence 是 0–100，来源使用 document_id/block_id。

- ai-fill/src/agent/finalProtocol.ts:77–110、198–232：lease_contract_patch 带 base_revision、base_hash、revision、patch_id 和 operations；检查去重、版本、规范化 hash、白名单路径，合并后重验，操作上限为 500。

- useLeaseAgent.ts:301：当前 LeaseDraft 在 React state；finalConversation.ts:95–139 从 completed final 和补丁回放恢复。

- documentEvidence.ts:10–14、194–210：证据文件带 file_id、byte_size、sha256；字段定位到页码坐标证据。

- reviewArtifact.ts:6–63：较大复核结果可引用受控文件，下载校验后进入 reducer，不接收任意 URL。

- useLeaseAgent.ts:1557–1597：正式业务保存另调宿主 saveAndGeneratePaymentPlan。

这证明已有“基线加补丁”的业务方案，但当前客户端草稿和会话回放，不等于独立产物 CRUD 已存在。

## BOM

源码根：/Users/wuzhiruge/PycharmProjects/deepfos-work/ruge-demo-dev/design_1009_bom_ai_fill。

- src/services/bomTurnProtocol.ts:15–34、231–248、284：bom_result 含 turn_id、base_revision、base_hash、revision、source_signature、project、version、snapshot、patches、question_set、question_receipts、review、next。首轮完整 snapshot 且 patches 为空；续轮 snapshot 为 null，使用稀疏补丁。

- patch 结构为 patch_id、node_key、changes、reason，校验基准、版本递增、重复 ID 和未知行。

- bomSourceReferences.ts:7–31：可让模型给 node_key 与 source_ref，程序根据保存的原表和列映射展开完整结果，不要求模型重抄所有数字。

- reviewV2.ts:10–16、57–60：置信度在 row_assessments 行级，不是每字段均有统一 value/source/confidence 包装。

- workbenchTurnFlow.ts:133–138、bomWorkbenchReplay.ts、bomConversation.ts：从服务器历史重建并校验后采用草稿。

- browserImportStore.ts：IndexedDB 主要存执行及回执，不宜称为草稿权威库。

## 预算报告课题 43

docs/design/43-budget-report-pipeline/ 的名称为“预算报告流水线与持续分析产物”。检查时为未提交研究材料，包含 README.md、pigment-and-prototype-research.md 及工作台交互说明；标明讨论回顾和需求研究，尚未批准产品实施。原材料偏复用 analysis_report.py 与 report-template.html，本次口述进一步提出开放报告中由 Agent 维护多种产物的方向。后续需把新讨论与旧草案区分，不能静默假设旧草案已覆盖。

# 十三 本地文件编辑机制与 JSON patch 的关系

AI Labs 已迁移到 /Users/wuzhiruge/PycharmProjects/AI-hub/agent-labs/。本次只读检查两个例子：

## 精确文本替换

agent-harness/learn-claude-code/agents/s02_tool_use.py:89–98 的真实函数为：

```python
run_edit(path: str, old_text: str, new_text: str) -> str
```

模型提供路径、旧文字和新文字；宿主 Python 读文件、找到旧文字、替换后写回。找不到返回错误，不写文件。该教学实现只替换第一次匹配，没有唯一匹配保护；不能把它说成所有 Agent 的安全做法。结果作为 tool_result 回给模型。

## Codex apply_patch

agent-harness/codex/codex-rs/apply-patch/src/parser.rs:145 的解析入口：

```rust
parse_patch(patch: &str) -> Result<ApplyPatchArgs, ParseError>
```

补丁带文件路径、上下文、删除行和新增行，通常按内容定位，不靠固定第几行第几个字符。UpdateFileChunk 包含 change_context、old_lines、new_lines。执行层通过文件系统接口真正写入。匹配先精确，再允许一定空白/标点规范化；仍找不到就返回 Failed to find expected lines。多文件操作失败不等于已完成部分自动回滚。

因此，“模型增量、程序落盘”完全可以迁移到数据库产物。文本 patch 依赖内容上下文，JSON patch 依赖对象路径，两者定位语义不同。重命名代码符号还可能需要语言服务，普通字符串替换不理解函数定义和调用的语义。

# 十四 真实 Agent 增量存储实现

## Trustcall 加 LangMem 加 LangGraph PostgresStore

这是接近本次需求的一条完整链路。Trustcall 的 PatchDoc 工具接收 json_doc_id、planned_edits、patches；每项 patch 是 op、path、value。当前枚举为 add/remove/replace，不能宣称完整支持 RFC 6902 的所有操作或自带 test 条件锁。

服务端用 jsonpatch.apply_patch 合成新文档，再交给 Pydantic 验证；验证失败进入修复。LangMem 比较前后，只写变更记录，但 store.aput 接收的是合并后的完整 kind/content。PostgresStore 的 value 为 JSONB，upsert 使用 SET value = EXCLUDED.value，而不是 jsonb_set。

这验证了模型输出增量与数据库整对象写入可以共存。检查到的路径没有携带 expected version，upsert 原子不等于整个读取、模型推理、写回链条能防止丢失更新。

来源：[Trustcall](https://github.com/hinthornw/trustcall/blob/main/trustcall/_base.py)、[验证实现](https://github.com/hinthornw/trustcall/blob/main/trustcall/_validation_node.py)、[LangMem](https://github.com/langchain-ai/langmem/blob/main/src/langmem/knowledge/extraction.py)、[PostgresStore](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/store/postgres/base.py)、[Memory Template](https://github.com/langchain-ai/memory-template)。

## Mastra Working Memory

update-working-memory 工具在 JSON 模式让模型只提交新增或变化字段；对象递归合并、数组整体替换。它不是 RFC 6902 路径操作。null 的删除行为受工具验证器处理影响，不能简单称为完整 RFC 7396。

resource scope 且适配器支持的 PostgreSQL 路径，在事务中确保行存在、SELECT FOR UPDATE、读取最新值、执行 JavaScript merge 回调，再 UPDATE。对应 workingMemory 列实际是 TEXT，metadata 才是 JSONB。这是“数据库 TEXT 也能支持模型增量”的直接证据。

其他作用域/适配器可能回退为 fetch → merge → save，不能声称全产品都具备同一并发保证。原 schema 校验不应误称为所有路径都做了合并后完整二次校验。

来源：[工具与合并](https://github.com/mastra-ai/mastra/blob/main/packages/memory/src/tools/working-memory.ts)、[Memory 实现](https://github.com/mastra-ai/mastra/blob/main/packages/memory/src/index.ts)、[PostgreSQL 适配](https://github.com/mastra-ai/mastra/blob/main/stores/pg/src/storage/domains/memory/index.ts)、[存储类型](https://github.com/mastra-ai/mastra/blob/main/packages/core/src/storage/constants.ts)。

上述源码基于核查时 main 分支，后续开发应固定实际依赖版本，而不是默认所有历史发行版相同。

# 十五 产物和共享状态服务先例

## Google ADK ArtifactService

ADK 明确把产物与会话 state 分开。产物通常是有名称的文件或 blob；普通名称作用于 app/user/session，user: 前缀支持同用户跨会话。保存同名内容产生新版本，可读最新或指定版本；GCS 后端支持持久化。

它适合作为“运行时与产物服务分离”的参考，但 JSON 作为文件内容不等于字段级 patch、业务 schema 或对象查询。当前 GCS 源码对并发版本创建使用 create-only 条件和重试，不应说它直接覆盖；但没有调用者 expected-version 的结构化合并接口。

来源：[Artifacts](https://adk.dev/artifacts/)、[BaseArtifactService](https://github.com/google/adk-python/blob/main/src/google/adk/artifacts/base_artifact_service.py)、[GCS 实现](https://github.com/google/adk-python/blob/main/src/google/adk/artifacts/gcs_artifact_service.py)。

## LangGraph Store 与 Agent Server

Store 使用 namespace 与 key 保存 JSON 类字典，支持跨 thread 访问和外部 GET/PUT/DELETE/search 接口。比线程检查点更接近独立可变结构化内容。

公开 PUT 契约没有看到业务 JSON Schema 或 expected-version 参数；namespace 也不是天然权限边界，应结合授权钩子。Graph state 的 schema 和 checkpoint reducer 不能自动当成 Store 的能力。

来源：[Stores](https://docs.langchain.com/oss/python/langgraph/stores)、[Store API](https://docs.langchain.com/langsmith/agent-server-api/store/store-or-update-an-item)、[授权](https://docs.langchain.com/langsmith/auth)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

## Letta Shared Memory Repositories

云端组织级共享 Git 仓库，可给多个 Agent 挂载，SDK 可读写文件，每次变更产生 commit，支持历史 ref 和 contentSha256 前置条件。它接近共享持久工作文件，但不是实时多人合并：Agent 侧 checkout 后仍有 push/pull。

当前文档限制包括文本内容 10 MiB、最多 2,000 文件、每请求一个文件操作。没有因此自动具备合同或 BOM 的业务 schema。

来源：[共享记忆](https://docs.letta.com/concepts/shared-memory)、[Repository API](https://docs.letta.com/agent-sdk/repositories)。

## Deep Agents Backends

把文件接口与底层持久化拆分：thread state、跨 thread Store、Context Hub 等。Context Hub 偏长期指令与技能，不应直接称为完整业务产物库。可借鉴接口和存储分离，不能因此回避业务身份、权限和内容协议。

来源：[Backends](https://docs.langchain.com/oss/python/deepagents/backends)、[Context Hub 与 Store](https://docs.langchain.com/langsmith/context-engineering-concepts)。

这些先例说明方向成立，但没有统一行业模型。可借鉴部分能力，不宜声称一个组件直接覆盖多端编辑、schema、权限、确认与执行反馈全链条。

# 十六 可对话修改的分析和流程产品

## Deepnote

Agent 可创建、修改、删除 notebook blocks，并执行代码检查结果。SQL、Python、文本、图表和输入构成用户保存的分析对象，当前 notebook 可以直接运行，不必先发布 app。

它最接近预算分析的持续迭代，但不自动成为合同/BOM 专业业务对象服务。其持久 /work 与临时 /tmp 也说明存储可独立于计算生命周期；持久不等于所有文件都有历史版本。

来源：[Agent](https://deepnote.com/docs/deepnote-agent)、[Notebooks](https://deepnote.com/docs/notebooks)、[执行模式](https://deepnote.com/docs/execution-modes)、[文件系统](https://deepnote.com/docs/importing-data-to-deepnote)、[版本历史](https://deepnote.com/docs/history)。

## Sim

Chat 可以创建、编辑、运行、调试已有工作流，改变节点、连线和变量。Function 支持 JavaScript、Python、Shell；API 的 manual 来源可运行已保存草稿，不要求先部署。

但 manual 路径需要写权限，不支持 async: true；正式触发器和消费者仍区分部署快照。它证明“对话改定义并直接执行”存在，不代表每个业务用户都应被迫使用工作流编辑器。

来源：[Chat Workflows](https://docs.sim.ai/chat/workflows)、[Function](https://docs.sim.ai/workflows/blocks/function)、[执行 API](https://docs.sim.ai/api-reference/workflows/executeWorkflowV2)、[CLI](https://docs.sim.ai/cli/workflows)。

## n8n AI Workflow Builder

自然语言可选择、配置和持续修改节点，再手动执行当前画布。生产事件/定时运行仍需要发布。公开源码中的 workflow-builder 还体现代码与表达式修改，但主分支不能代表所有套餐或已发布版本。

来源：[AI Builder](https://docs.n8n.io/build/ways-of-building-workflows/ai-workflow-builder)、[执行类型](https://docs.n8n.io/build/understand-workflows/understand-executions/types-of-executions)、[公开技能源码](https://github.com/n8n-io/n8n/blob/master/packages/@n8n/instance-ai/skills/workflow-builder/SKILL.md)。

## Windmill

AI Sessions 可持续修改用户自己的 scripts、flows 和 full-code apps。普通 run 测试草稿，明确 deployed/production 才使用线上版本。它不是“加一个 AI 节点”这么简单，但草稿测试和正式部署必须分开描述。

来源：[AI Sessions](https://www.windmill.dev/docs/core_concepts/ai_sessions)、[AI Flow Chat](https://www.windmill.dev/docs/core_concepts/ai_generation)、[Draft and deploy](https://www.windmill.dev/docs/core_concepts/draft_and_deploy)。

## DataFlow Harness 与 Data Formulator

DataFlow-Harness 研究自然语言持续修改持久 pipeline：对话和画布共享同一份 DAG，使用类型化操作改节点、边和参数，并验证 schema。它主要组合注册算子，不证明任意代码都能自由修改，持久性、并发和恢复仍有待评估。来源：[论文](https://arxiv.org/html/2607.16617v2)、[实现](https://github.com/OpenDCAI/DataFlow-WebUI)。

Microsoft Data Formulator 支持多轮分析、代码转换、图表和可回看的 Data Thread，适合研究报告体验；本次未充分核实“任意完整流程无 Agent 重跑”的统一契约。来源：[项目](https://github.com/microsoft/data-formulator/releases)、[0.7 介绍](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/)。

**对本项目的意义：** 行业已有对话修改流程的产品，不能宣称空白；但用户最新需求是专业业务页面消费可修改产物，不是让每个人维护可发布低代码应用。因此这些产品仅作为较复杂上层用例参考，底层产物服务不应照搬其工作流和发布体系。

# 十七 报告执行与渲染组件

- **Hex**：已发布应用的 scheduled run 与重新运行 Agent 提示词的 Tasks 是两类动作。可借鉴“更新保存的分析”和“重新由 AI 分析”的区别。Hex 4.0 的代码化查询、逻辑、图表与布局属于私测，官方明确尚不支持 scheduled runs 和 Tasks，不能把两代能力合并。来源：[Scheduled runs](https://learn.hex.tech/docs/share-insights/scheduled-runs)、[Tasks](https://learn.hex.tech/docs/explore-data/tasks)、[4.0 限制](https://learn.hex.tech/docs/hex-4-0/what-is-hex-4-0)。

- **marimo**：Python 文件保存 notebook，依赖图管理重算与过期输出，可作为可执行分析基础。依赖环境的 sandbox 不等于文件系统和网络安全隔离。来源：[Dataflow](https://docs.marimo.io/guides/editor_features/dataflow/)、[脚本运行](https://docs.marimo.io/guides/scripts/)、[包管理](https://docs.marimo.io/guides/package_management/)。

- **Quarto**：参数化执行 Python/Markdown 并生成 HTML 等格式。freeze 可以复用已有计算结果，但不自动识别所有外部数据变化；渲染已有 ipynb 默认不一定执行其单元。来源：[参数](https://quarto.org/docs/computations/parameters.html)、[执行管理](https://quarto.org/docs/projects/code-execution.html)。

- **Evidence**：SQL/Markdown 报告、编辑与实时预览，Agent 可给出修改 diff，再接受、拒绝或继续修改。开源框架和托管 Studio 能力需分开，不能当通用 Python 环境。来源：[Editing](https://docs.evidence.dev/editing)、[CLI](https://docs.evidence.dev/cli/commands)。

- **Observable Framework**：构建时数据加载器将 Python/SQL 等结果生成数据文件，再由前端读取。缓存存在时不一定重新查询，构建成功不等于数据已刷新。发给浏览器的数据仍受查看者可访问范围约束。来源：[Data loaders](https://observablehq.github.io/framework/data-loaders)。

- **Papermill**：参数化输入 notebook 执行到独立输出 notebook，是可借鉴的小型执行器，不是完整协作产品。来源：[Execution](https://papermill.readthedocs.io/en/latest/usage-execute.html)。

这些是可组合的组件，不单独满足产物服务的业务身份、权限、确认及反馈需求。可重复执行固定代码也不等于完全可复现结果：数据版本、环境、随机性、时间、外部 API 和副作用都可能改变输出。

# 十八 给本地开发会话的继续工作清单

## 先确认现状

- 固定本地分支和版本，重新核对本文行号及接口，不能依据旧快照直接修改。

- 确认现有结构化输出、历史存储、租户和文件服务可复用哪些字段。

- 查清真实对账工作台所在目录后再对齐其协议，不能用类似材料代替。

- 确认现网 MySQL/PG 具体版本、JSON 能力、ORM 映射、迁移与备份约束。

- 将课题 43 的旧研究与本次口述方向并列，不擅自把研究结论写成已实现。

## 首版最小验证目标

1. 创建一个合同/BOM 风格的 JSON 产物并直接 GET，无需历史重放。

2. 首轮基线和后续 patch 都走同一持久内容。

3. 页面修改与 Agent 修改遵循相同修订条件。

4. 过期修订、非法路径、类型错误和未知节点均拒绝，不污染原内容。

5. 执行消费方拿到指定内容并产生回执，Agent 能读到准确反馈。

6. 只有最新内容也能工作；不以完整历史作为首版验收条件。

7. 大附件继续引用文件服务，内容权限与文件权限一致。

8. 模型不直接获得任意 SQL UPDATE 或不受限业务写权限。

## 应明确的接口测试

缺失键与 null、数组下标与稳定 ID、重复 patch_id、并发页面改动、旧 Agent 响应、响应超时、部分业务成功、重复提交、跨租户访问、schema 升级、超大内容、非法脚本引用、HTML 预览隔离、旧错误对应新修订等。

HTML 或脚本产物即使存为合法 JSON，也不因此安全可执行。渲染需防注入；脚本执行需权限、资源、网络与凭据隔离。业务写入由消费方守住授权与幂等。

## 本次不作为开发要求

不立即建设通用工作流引擎；不承诺每个用户发布应用；不立即建设全文版本库；不强制微服务拆分；不为未出现的老数据库提前实现双存储兼容；不将自动通过结构校验等同自动批准业务动作。

# 附录 微调话题与术语修正

关于“模型变强后为什么微调讨论变少”，本次结论不是微调已经没有价值，也没有行业统计支持“今年没人讲”。很多抽取、格式遵循和工具调用可先用强模型、提示词、示例、Skill 与工具解决，减少训练数据和评测成本。

RAG 提供当前资料；Skill 描述方法和工具使用；微调改变模型行为，三者用途不同。稳定、重复、量大的任务，在小模型成本、低延迟、本地部署或高吞吐需求下仍可能适合微调。合同抽取积累了足够高质量确认样本后可评估；频繁改变逻辑和展示的开放报告，优先上下文、工具和可编辑产物。是否训练应由基线评测和成本收益决定，而非跟随热度。

Pigment Mission 在本次讨论中是用户提供的产品类比：一个长期目标或要求关联输出，并可能复跑或定时。未获得足够证据确定其底层文件、数据库或执行协议，因此本文不把“Pigment 一定采用某种 JSON/脚本/HTML 存储”作为事实。

最终需要保留的判断是：**当前要补的是业务各方共同维护、直接读取和消费的中间产物，不是把所有任务都改造成工作流。原生 JSON 加受控 patch 已确定，任务容器和表结构仍需按实际复用边界收敛。**