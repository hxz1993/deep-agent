# 原生输出接入与业务采用

资料核对：2026-11-06，历史参考基线 Head 013；目标环境须另读 /info 和 OpenAPI。适用于有业务产物的Agent客户端；普通文字聊天和真正Deferred按各自契约。平台指南为`<deep-agent>/docs/guides/structured-output.md`和`sse-protocol.md`；目标环境仍需独立核验。

## 配置与启用

结构化输出默认关闭；Agent顶层保存structured_output_enabled、allow_text_output及output_config:{name,description,json_schema}。允许文本默认false，PATCH省略保持、null或非布尔拒绝；关闭总开关保留Schema与策略草稿，删除定义同时清策略，没有定义不能单独保存true。管理员GET /agents/{id}/output-config回读两开关与完整定义；普通聊天初始化不依赖该接口，也不逐次Chat传Schema。

使用完整静态业务Schema覆盖本产品首轮/续轮/澄清/复核等组合，保留业务版本和互斥规则。Schema上限64KiB、展开后仍有深度/节点/字段预算，结果上限1MiB；用目标平台真实admission和实际Provider验证，不用最小桩或只看本地编译。更多配置和包规则见[配置资产](frontend-agent-configuration.md)。

隔离Agent先验后，按授权先发布可读取新结果的客户端；若默认请求契约也在变化，先保留旧默认，待Agent切换后再启用新默认。共享Skill会影响旧契约时，预置独立新code并逐文件/hash回读，再在同一次Agent PATCH中同步prompt、完整Schema、开关及目标Skill引用；保留模型/MCP和其它绑定。旧页面需刷新并证明加载新资源。Skill上传、Agent PATCH和客户端发布不是跨资源原子事务；不为纯UI补丁同步Agent，也不因改本机Codex Skill更新线上业务Skill。

### 结果分组与Schema预算

仅当产品需要一级业务分组时设计显式外壳，不把BOM外壳推广成平台通用协议。外壳和内部字段用title/description承载名称、用途及简短字典；流程、查询和人审规则仍留业务Skill。新增层级或带注解的$ref可能增加展开深度，须按目标平台实际展开后重新核算预算；可等价改写约束，但不能删除required/enum/ready条件换取准入。

传输形状改变时，按所属持久用户请求中的显式版本选解码器，不根据返回键猜版本。完整原始wire用于直播/History冲突比较、去重及新批准证据；解包值才交业务reducer。旧hash/证明不改写；显式重整旧失败轮仍用原请求声明。业务专属解包放客户端领域边界，不侵入通用SSE解析器。

## 接收入口

原生JSON分支的HTTP成功和SSE`final(status=completed)`返回原生`output`及：

```json
{"output_info":{"name":"result_name","output_id":"stable-result-id","tool_call_id":"real-call-id","attempt":1,"turn_id":"platform-turn-id"}}
```

- 按字段是否存在读取output，不用真假值；null、false、0、空串、对象和数组都可能合法。
- 直接把原始值交业务校验；不要JSON.stringify成假Markdown围栏来复用旧正则，也不删除未知字段、补默认值或猜类型让它通过。
- 中间text/thinking仅展示过程，不提前采用其中的产物。本版不提供结构字段级partial；未闭合过程围栏不能作为临时合同或补丁。
- 可仅消费final；若同时消费独立output，按output_id去重，禁止重复应用。同一ID的来源、身份、接受状态或值冲突时明确拒绝，不以“最后一个通知”为准。final-only也必须登记接受事实，防迟到拒绝覆盖。
- 校验预期name、Session、平台Run/Turn、持久顺序和用户请求归属；业务turn_id、patch_id、submission_id另行校验，不用平台ID替代。

独立output事件：accepted=false/status=error是格式失败尝试，不是顶层运行error；只更新进度/诊断，保留有效草稿，不结束Run、不自动repair。accepted=true/status=completed才是持久接受结果。真正final/error/cancelled按协议收口。

## 允许文本输出（0.14.3）

三种策略：关闭结构化为str+Deferred；开启且allow_text_output=false为ToolOutput(Schema)+Deferred；开启且true为str+ToolOutput(Schema)+Deferred。Schema的required仅约束JSON分支，允许普通文本不表示JSON可缺字段；Schema本身也能返回字符串、null或false，识别分支用output_info/History.kind，不能按typeof或正文像不像JSON。

允许的普通文本没有output_info、成功output事件或结构化接受事实，沿text/final及assistant_message处理。完整消费流，首个text后仍可能有工具、Deferred或JSON。同响应文字伴随合法输出时，文字为过程说明，JSON是唯一接受结果，按output_id去重。恢复History时也要按所属持久用户/Run和structured_output身份分流；伴随assistant_message即使标记phase=final，也不能再当作旧围栏结果送业务解析或挡住该原生结果。原生身份/内容冲突仍明确拒绝。混合模式文字待完整模型响应检查并持久化后才发布，首个可见正文可能更晚；失败响应文字不进入普通回复。关闭路径仍实时增量。

每个Run（含UI确认续跑）从当前Agent读取Schema与文本策略，Run内保持一致；不保存、引用或恢复完整Schema快照。续跑保留原请求和格式预算，同一会话约定配置不变，修改后应新建会话。客户端不获取管理Schema来渲染结果，也不自行加配置快照或恢复逻辑。

严格业务工作台不因平台升级自动允许文本。本次核验的正式BOM继续false，生成、修订、人审与repair仍需完整bom_result；具体开关以后仍服从当前产品决定。若产品要允许纯解释请求用文本结束，先对齐客户端完成状态、提示词与Skill路由，再隔离验证；文本不能更新草稿/hash/revision、确认、批准或保存计划，不机械地因新开关重写业务资产。

## History、Live与断线

History新增`kind=structured_output`，直接读name/output，`item_id==output_id`。Live包装、SSE output与final中的同一结果沿用这个ID，不能当成不同结果再次应用。

原生产物与旧围栏产物按各自持久契约解码后送同一业务校验；混合模式的普通文字走独立展示分支，不送业务reducer。缺output_info时按已声明的混合文本、明确关闭路径、旧历史或Pending分别处理；若当前产品要求新请求必须native，缺身份应报配置/协议错误，不能静默降级。历史事实、批准hash和已有回执不改写。

断流/重开只读原会话History并校准，不自动重跑模型、重发补丁或业务操作。不能因为HTTP响应丢失就判未提交；保存未知先核实原execution/journal。跨目标/附件/来源变化时，旧业务基准不可用于当前目标。恢复后的批准是否需要重审由产品既有规则决定，不把平台completed当写权限。

0.14.3旧历史在0.14.5读取、继续提问及旧Pending升新版续跑已验证，前提沿用当前业务契约和一致配置；原记录不重写。反方向，新版无快照Pending交回0.14.3旧Runner可能失去严格JSON策略，降级前须先完成或按工具协议取消这些等待确认／选择的交互。说明风险时写清方向和条件，不把这条反向限制说成“旧历史不兼容”，也不要求用户删除会话。详情见[平台兼容边界](../../backend/references/structured-output.md#历史兼容与回退)。

兼容指普通客户端的业务结果、身份和消费方式；原始Events停止新增配置快照／引用，失败镜像与错误文案也有修正，不保证逐帧字节或数量不变。普通客户端不因此订阅原始Events或新增过滤规则。

## 业务检查与纠错

依产品契约分开验证：

1. Schema格式接受及平台身份/持久归属。
2. 来源签名/节点覆盖、当前revision/base_hash、允许路径、真实引用、父子依赖及patch幂等。
3. 最新用户输入、逐项人审与整批批准。
4. 不可变计划、原生写入结果、原执行恢复及独立保存回读。

Schema只能校验静态结构，不能证明UUID来自真实查询、COUNT/分页已齐、文件存在或业务值合理。未分发工具、输出调用、输出之后的查询、错作用域/错Run或不完整工具事实不能冒充业务证据；证据截止与完整性按本产品原规则保留。

平台同Turn最多两次格式修正，总Provider请求预算优先；客户端不为格式失败另开轮次重置额度。accepted后的业务基准/引用错误，才按既有有界repair关联被拒output_id、原提交和当前基准。网络、下载、文件失效、权限和保存失败停在对应环节，不一概重调模型。不能通过删问题、放宽Schema、猜默认值或改测试压低待确认数。

普通澄清JSON是业务数据，不自动变Deferred；真正Deferred沿原tool_call_id、平台Turn和剩余预算续跑，输出定义由服务端本次Run加载。恢复对话显示不自动执行批准或保存。

## 两种已对齐的业务形态示例

| 产品 | 保留的业务根与边界 |
| --- | --- |
| BOM | 默认allow_text_output=false；结果名bom_turn_v3；output={bom_result:BomTurnV3}，bom_result必需且为唯一一级业务键，不可省略或为null；一级标题BOM处理结果，内层业务schema3不变。说明放内层summary，不增加无结果完成分支。按所属持久用户bom.state.output_transport=native_json_bom_result_v1显式解包；旧native_json_v1及围栏限原契约历史解释，不改写旧hash/证明。完整wire和output_id用于一致性、去重与新批准证据。首轮完整snapshot，续轮snapshot=null与稀疏patches；来源、行审和执行门禁保留。项目资产版本与正式Skill code从项目交接及目标回读确定 |
| 租赁 | 结果名lease_result；一个对象按需组合text、lease_contract、lease_contract_patch、lease_clarification、lease_review、lease_review_file。完整合同/patch互斥，内联review/review_file互斥；省略产物不清空基线，问题版本/答复回执/暂缓状态保留 |

这些是产品各自契约，不是通用默认Schema。其它客户端沿自身业务协议设计。租赁大复核仍走原文件引用并校验权限、大小、Hash与快照；不截断JSON或改公开下载绕过限制。平台只保证接受，实际填充、保存和复核需客户端独立验收。

## 验证与错误记录

按影响覆盖首轮/续轮、失败后成功、重复与同ID冲突（含final-only起点）、null/false/0、断线恢复、旧新History混合、业务拒绝、真正Deferred，以及实际写入/独立回读。用户指定重复次数时每例满足，保留失败与修复后重跑，不把旧回执当第二次执行。

每个模型格式失误即使平台同Turn修好也登记：会话/Run、失败output_id、attempt、原错误与叶路径、最终接受ID、客户端是否采用。错误汇总只有anyOf时，可用私有原候选和同份生产Schema诊断叶路径；不把原候选交普通业务History。区分JSON/Schema错误、业务校验、网络/文件与保存失败。

若事件只给对象路径和required、未给缺失字段名，按证据原样记录，不猜字段；enum错误未取得原候选时不猜非法值。错误清单被截断时只报告已返回数量，不当作实际总数。

客户端POST/Run、Provider请求、格式attempt、工具调用与成功业务动作分别统计；重复读取History按稳定身份去重。Usage/缓存只读实际final或管理员持久诊断真值，缺失标未知；累计token不当作当前窗口占用，Run时长累加不当并发wall-clock。提示词能减少失误，不能宣称从此保证零结构错误。

严格JSON策略allow_text_output=false也不保证History没有伴随assistant_message。按同一Run的唯一有效structured_output判断业务结果，不能靠文本策略或phase=final推断产物；所有原生身份和冲突检查必须先通过。错误应区分业务拒绝、原生契约冲突和真实持久化未知，不能让恢复捕获器把前两类统一改成网络问题。动态ready条件依赖原稿引用或历史合并时，Schema描述与Prompt/Skill明确数据来源，客户端保留实际合并值门禁；已有数据库值不自动补缺失的用户原稿值。
