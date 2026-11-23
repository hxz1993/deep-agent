# 原生结构化输出：后端契约与维护边界

资料核对：2026-11-06，历史参考基线 Head 013；目标环境须另读 /info 和 OpenAPI。以当前源码和目标环境实况为准。本页用于后端实现、Console、安装与跨客户端交接，不授权修改环境或调用模型。

## 定义与Run内装配

- 默认关闭。管理员POST/PATCH Agent一次保存顶层structured_output_enabled、allow_text_output及output_config:{name,description,json_schema}，不逐次Chat传Schema。允许文本默认false、strict boolean，省略保持、null拒绝；没有定义不能单独保存true。
- `name` 为 1–64 位 ASCII 字母、数字或下划线，以字母开头。业务名可与已授权工具相同。模型侧使用 `output__<name>`，超长时生成稳定短名。配置、安装包、`output_info.name` 和历史继续使用原业务名。使用完整业务 Schema 表达首轮与续轮契约。
- PATCH省略保持；关闭保留草稿。显式`output_config:null`只允许最终关闭状态；开启而无定义须整个事务拒绝。更新人取可信身份，不接受Body/包伪造。
- GET /api/v1/agents/{id}/output-config回读两开关及完整定义；普通详情只给bool，不加载管理Schema。普通聊天初始化不依赖管理接口。
- 每个Run开始读取一次当前Agent的Schema和allow_text_output，在内存构造Output Type与校验器；UI续跑也重新装配当前配置。同一会话约定配置保持一致，改定义／策略后新建会话，不支持恢复历史配置版本。
- 不再向用户事件写deepagent_output_contract或后续请求的deepagent_output_contract_ref，不存来源事件引用，也不扫描旧快照／摘要来恢复配置。schema_digest仍按原规则输出，只是指纹，不作为配置查找键。
- 存储保持两表逻辑1:1：agents放structured_output_enabled；agent_output_configs按agent_id保存独立name、description、allow_text_output列及完整json_schema JSON列，没有逐字段明细表。对外API的allow_text_output仍是顶层字段。

## 校验、纠错与分发

Draft2020-12；不补默认值、不隐式类型转换。缺键、null、空串、false、0分开处理；根对象、数组和标量均可，模型工具参数的内部包装不得泄露到公共输出。

当前Schema上限：UTF-8 64KiB、深度16、节点2048、属性512、枚举1024；引用展开后同样检查。仅支持本地无环$defs，禁止外部、循环、dynamic/recursive引用及嵌套$id，不访问网络。最终JSON上限1MiB。更完整约束以仓库`docs/guides/structured-output.md`为准，不按客户样例另改平台默认值。

产品可显式设计一级业务分组，并以title/description提供Console卡片和字段说明；BOM的bom_result外壳属于其客户端契约，不成为平台隐式包装。新增外壳或$ref兄弟注解可能增加展开深度，须用实际normalize/expand及完整业务正负例验证，不能删除约束换预算。平台的name、公共output_info及业务schema_version分别维护。

- 完整响应在宿主分发前校验；多个输出、输出与普通/MCP/UI调用混调、严格模式纯文字绕过或JSON/Schema错误共享同Turn格式预算。未分发工具不进入普通History，不形成查询、审批或保存证据。
- 每Turn最多两次格式修正，UI恢复不重置，总请求预算优先。容量/系统错误不是模型内容错误，不触发无意义修正。平台失败候选不交客户端业务采用，也不通过新业务POST重新给格式额度。
- Provider远端已执行的原生行为无法回滚；宿主未分发不等于远端从未发生行为。
- validator使用有界子进程；修改时核对超时/取消回收、队列与缓存资源，不能把复杂Schema校验阻塞在API事件循环或无限排队。

## 接受事实与公共协议

原生JSON分支HTTP成功及SSE`final(status=completed)`的`output`直接为原生JSON，并带：

```json
{"output_info":{"name":"result_name","output_id":"stable-result-id","tool_call_id":"real-call-id","attempt":1,"turn_id":"platform-turn-id"}}
```

独立SSE`output`：

| 状态 | 含义 |
| --- | --- |
| accepted=false / status=error | 格式失败尝试，带errors，不是Run结束，不公开候选业务正文 |
| accepted=true / status=completed | 通过当前Run完整Schema校验且接受事实已持久化，可交业务校验 |

来源SDK调用/ack、预算和必要状态保存后，才发送已接受结果。发送失败不能撤销接受事实；输出事实仍在chat_session_events，012的agent_output_configs只存配置，不另建结果/Run事实源。

接受事务不回查用户事件中的Schema；保留Session／所有者／Turn／Run／源响应／Part、真实调用名称／ID／参数、未分发／拒绝门禁、已保存SDK确认、原生值一致、原子提交和幂等。SDK标量／数组的response参数包装由当前Run的内存参数解包，不写配置副本；失败候选在轨迹展示原始源参数，不从当前或旧Schema猜测解包。

`History.kind=structured_output`直接含name/output；`History.item_id == SSE.output_id == final.output_info.output_id == 持久输出事件ID`，Live包装沿用同一身份。失败尝试只在Events/轨迹，不进入普通业务History。输出与普通工具生命周期分开，输出不等于Deferred。

客户端可仅采用final；若同时采用output，则按output_id去重。即使仅从final建立状态，也要登记接受事实，拒绝迟到的同ID拒绝或异值；不能覆盖原始值或重复应用补丁。平台Turn/Run与业务turn_id、patch_id、submission_id分别保留。

没有output_info的关闭路径及已有文字历史沿原类型处理；已要求原生的客户端新请求不能拼假围栏或静默降级。只读恢复不自动重发业务或模型请求。普通澄清JSON不自动变Pending，真正Deferred仍用原调用及剩余预算。

## 严格与混合结束策略

关闭结构化为str+Deferred；开启且allow_text_output=false为ToolOutput(Schema)+Deferred；开启且true为str+ToolOutput(Schema)+Deferred。字段required只约束JSON，不能把“允许文本”实现为放宽Schema或跳过JSON校验。

文本正常结束没有成功output事件/output_info，History为assistant_message；按真实调用来源和History.kind识别，不按Python str判断，结构化字符串/null/false仍有原生输出身份。混合模式文字在完整响应检查并保存后才发布既有text事件，失败候选文字不进入普通回复；关闭路径增量不变。同响应文字+合法输出时JSON是唯一接受结果。客户端完整消费流，不能见text提前收口。

Schema及文本策略只在当前Run内保持一致；Deferred／换Runner／换进程续跑采用当前配置，但仍从真实执行记录恢复请求数、格式失败次数和attempt，不能重置额度。平台支持mixed不等于BOM等业务已适配；客户端决定文字能否结束本次业务请求，平台不据文字生成批准或保存事实。

## 历史兼容与回退

| 方向 | 当前边界 |
| --- | --- |
| 0.14.3历史 → 0.14.5读取／继续提问 | 保留正文、工具、原生值、状态及输出身份；旧快照／引用保留原样但不驱动运行，已接受结果不重新校验。 |
| 0.14.3 Pending → 新版续跑 | 使用当前配置和原剩余预算；配置一致条件下已验证。Pending指等待UI确认／选择，不是普通历史记录。 |
| 新版Pending → 0.14.3旧Runner | 不可靠：旧Runner按legacy分支处理，可能丢失严格JSON输出策略。回退前先完成或按工具协议取消这些交互，避免新旧worker交叉接续；不是删除会话。 |

普通HTTP／SSE／Live／History的业务消费契约保持兼容；客户端不需要Schema或新增过滤。原始Events停止新增配置副本，失败检查点的镜像和错误文案更完整，不声称所有帧逐字节／数量不变。不能据此扩大删除instructions、System Prompt、计划或业务上下文，不批量清理旧事件。

## Schema接受之后

平台accepted不等于客户端adopted，也不等于用户批准或业务保存成功。来源签名、revision/hash、允许路径、引用、补丁幂等和业务依赖仍由对应客户端验证；通用平台不内置BOM或租赁启发式。

格式修正由平台处理；accepted后的业务基准错误可由客户端按原有有界规则修复，并关联原提交/被拒output_id。网络、下载、文件权限及保存失败停在真实环节，不一概归为模型格式错误。每次格式失败包括同Turn后来成功的尝试都要登记，不能用最终accepted声称模型零失误。

诊断必须区分错误路径与可确认的坏值：仅返回required及对象路径时不能猜缺失字段；未取得失败原候选时不能猜enum非法值。错误列表截断只证明返回了这些错误。维护错误映射时优先提供可定位的缺失字段/未知键与叶路径，同时保留隐私和有界错误预算，不公开完整失败候选。

## Console、包与发布

- Console输出详情的确定/取消是本地草稿；页头保存才提交Agent。通用JSON展示、复制/下载保留完整值，失败/成功轨迹统一“输出”类型，用状态区分；客户端业务渲染不放入Console。
- 结果轨迹详情只用Summary / Output / Raw英文页签，成功展示已保存原生值、失败展示真实源参数；不展示配置或Schema页签，不为渲染结果查询当前配置。分组与折叠复用共享代码控件，不创建另一套JSON截断规则。
- 包格式仍整数schema_version1。关闭导出省略所有输出字段及草稿；开启含总开关与完整定义，仅允许文本时带allow_text_output:true，缺省false。旧包新建/覆盖新版重置两个开关false并清旧定义。策略进入预检摘要和导出并发检查；旧版拒绝开启包，不能继承目标旧值。
- 仅安全未知扩展可忽略，已知字段类型、重复JSON键、ZIP路径/重复项/加密/符号链接及资源预算仍严格校验；未知内容也进入包摘要。全量配置备份保留关闭草稿且要求同Revision。
- 012升级到013时只给agent_output_configs新增allow_text_output NOT NULL DEFAULT false；表按agent_id逻辑1:1，name/description/策略独立列，json_schema存完整定义；总开关仍在agents。012历史输出表/开关/updated_by保持。013降级到012会删除该列，存在true草稿或历史冻结mixed策略时先拒绝；DDL按仓库独立授权／维护发布规则处理，核对真实Revision和物理结构，不手改标记。已有输出定义／更新人／事实时其它降级保护仍有效，不删数据换通过。
- 源码发布不等于环境升级，平台升级也不自动更新客户端、prompt、Skill或开关。先隔离Agent验证完整Schema与实际Provider，再按授权部署客户端和配置、显式启用并完成真实业务闭环。
- 固定基础镜像不在发布时动态安装依赖。本版本复用既有安装集合；后续新增import、传递依赖、wheel或范围变化需单列运维交接并核对镜像实物。
- 0.14.4/0.14.5相对0.14.3没有依赖／迁移变化，Head仍013。模型传输通过model_http_settings请求级协商Accept-Encoding:identity规避并发zstd解码，不补丁全局decoder、不重放部分Run；上游忽略协商仍可能失败。解码错误归传输层，不能误报Schema拒绝或用改提示词代替修复。

## 入口与测试选择

先读当前仓库：`docs/guides/structured-output.md`、`sse-protocol.md`、`agent-packages.md`；跨客户端映射见`docs/design/09-structured-output/client-design-alignment-20261022.md`，其中“未实施”仅描述当时设计阶段，不代表当前客户端交付状态。

实现入口：`agent/structured_output.py`、`structured_output/schema.py`/`validation.py`/`worker.py`、`schemas/agent.py`/`chat.py`、Event codec/Conversation projector、`agent_packages/`、`migrations/versions/012_structured_output.py`，路径相对`src/deep_agent`（迁移相对仓库根）。

按影响选`tests/unit/test_structured_output_schema.py`及`tests/integration/test_structured_output_*.py`、`test_allow_text_output_*.py`，不为文案更新跑全部组合。涉及契约时核对首轮／Deferred当前配置装配、预算恢复、旧快照缺失／损坏但历史仍可读、失败后成功、重复通知、断线恢复、混合历史、混调未分发、接受事务及业务拒绝。按需使用真实Provider与隔离Agent；分别报告已验证与未验证，平台通过不替代业务填写／保存／回读。
