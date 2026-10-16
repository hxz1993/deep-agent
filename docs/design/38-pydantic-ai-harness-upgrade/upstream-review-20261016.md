# Pydantic AI / Harness 增量研究与采用分档

> 核验日期：**2026-10-16（Asia/Shanghai）**。状态：**调研完成，未升级产品依赖、未实施新能力、未提交**。
> 比较范围：Core **2.46.0 → 2.57.0**；Harness **0.32.0 → 独立仓库末版 0.36.0 → 合仓版 0.57.0**。
> 本文的“建议近期”是研究建议，不代表用户批准开发；后续研发状态统一维护于 [TODO](../TODO.md)。

## 1. 判断与基线

**值得升级，但应先做兼容适配，再分批启用新能力。** 这轮最有价值的是已有工具链的稳定性修复、PTC 跨 Run 状态接口、决策模型统一接口，以及本地沙盒候选。没有理由因此替换 DeepAgent 的会话、权限或 Console。

推荐顺序：

1. 做 B 组兼容适配，在隔离环境验证 A 组修复，作为一次基础库升级交付。
2. 优先验证 C1 PTC 状态和 C2 决策路由；两者可分别实验，不必捆绑发布。
3. C3 缓存优化按实际 Provider 做小样；D 组在明确业务收益后再设计。
4. E 组保持现状，特别是之前明确关闭的提前执行、监督模型与持久执行，不因上游再更新而自动重开。

| **对象** | **本轮实查** | **含义** |
| --- | --- | --- |
| DeepAgent 工作区 | `test / c53646a / 0.13.5`；存在既有未提交修改 | 以当前源码为准，全部保留；本轮只改研究文档 |
| 产品声明及 `.venv` 实装 | Core `2.46.0`、Harness `0.32.0`、Monty `0.0.23`、Python `3.12.10` | 学习仓库更新没有改变本地8000的依赖 |
| Pydantic AI 学习副本 | `v2.57.0 / 54a31abfb7bb` | 含新版 Harness `0.57.0`；不是已经安装的产品版本 |
| Harness 独立学习副本 | `v0.36.0 / 220ddcadf2d2` | 独立上游归档；Core 2.52起合仓、配套Harness 0.52；不是遗漏中间21个版本。PyPI 包名仍是 `pydantic-ai-harness` |
| 方法 | 官方逐版 Release、固定 Tag 源码及上游测试、当前产品调用路径、Context7文档交叉核对 | 浅克隆不据本地单条 git log 推算完整提交数；代码图谱仍指向旧目录时回到 `src/` 核实 |

历史对照为9月22日 A～E 分档与9月25日 D 系列复核，实际参考入口见文末“历史研究”。本轮不是复述全部旧功能，也没有跑新版真实模型或安装兼容测试。

## 2. A 组：完成升级适配后，已有路径可直接受益

这些项通常不需要为功能另加开关；**仍以 B 组迁移和回归通过为前提**。自动受益不等于所有请求都会更快。

| **编号** | **上游变化与证据** | **对应现有路径 / 获得什么** | **限制** |
| --- | --- | --- | --- |
| A1 | Core 2.51：复用 Agent graph、减少 RunContext 复制、单子节点不额外创建任务组；PR [8658](https://github.com/pydantic/pydantic-ai/pull/8658)、[8775](https://github.com/pydantic/pydantic-ai/pull/8775)、[8774](https://github.com/pydantic/pydantic-ai/pull/8774) | Agent 每轮运行及 Capability 调用的本地开销下降 | 我们每次也有重新装配；graph复用收益受实例复用影响。不能把框架微优化说成合同六分钟问题已解决 |
| A2 | Core 2.52：OpenAI Chat 流内错误转为模型异常；PR [9313](https://github.com/pydantic/pydantic-ai/pull/9313) | OpenAI、OpenRouter、DeepSeek等 Chat 通道更早获得明确错误 | 不修复网络、余额或网关；客户端的最终错误展示仍由我们负责 |
| A3 | Core 2.53～2.54：Chat/Responses 流式与非流式响应一致、保留文本与思考 Part 边界；PR [9418](https://github.com/pydantic/pydantic-ai/pull/9418)、[9417](https://github.com/pydantic/pydantic-ai/pull/9417)、[8729](https://github.com/pydantic/pydantic-ai/pull/8729) | 当前 Chat、订阅 Responses 和完整 Part 保存链路继承模型适配修复 | 必须验证 SSE 和保存后的 History 一致，不自动修复旧事件 |
| A4 | Core 2.53：重复工具调用ID防错执行；PR [8782](https://github.com/pydantic/pydantic-ai/pull/8782)；2.55历史修复 [9819](https://github.com/pydantic/pydantic-ai/pull/9819) | SDK 工具分发更稳，避免同批异常ID造成错误调用 | 不替代产品层重复 UI 确认校验；不能删除 `agent/deferred.py` 的保护 |
| A5 | Core 2.54、2.57：同轮重复加载能力去重、带前缀/改名工具的 prepare 重试独立；PR [9397](https://github.com/pydantic/pydantic-ai/pull/9397)、[10219](https://github.com/pydantic/pydantic-ai/pull/10219) | Skill `load_capability`、MCP前缀和工具准备路径 | 不强制模型先读Skill，也不保证遵守业务规则 |
| A6 | Core 2.57：OpenAI Chat补齐当前轮工具调用所需的空思考字段、透传Gemini工具思考签名；PR [10251](https://github.com/pydantic/pydantic-ai/pull/10251)、[10235](https://github.com/pydantic/pydantic-ai/pull/10235) | 使用相应Chat Profile/兼容网关时，工具后续跑更可靠 | StepFun已有显式Profile需对照；Gemini原生协议与OpenAI兼容通道分开测 |
| A7 | Harness随Core 2.56：估算不重复计算已变化指令和工具返回文件；PR [9929](https://github.com/pydantic/pydantic-ai/pull/9929) | 我们的 `context/estimator.py` 调用了官方 `estimate_context_tokens` | 修正估算，不是实际内容压缩；24K/48K读取包装仍由产品控制 |
| A8 | Harness 0.35：非JSON CodeMode结果处理改善；[0.35 Release](https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.35.0)；合仓版继续维护CodeMode并发与取消 | 已关联PTC时，继承底层Monty封送和执行修复 | 自定义 `HybridCodeModeToolset` 必须先完成B组核验；不扩大已授权工具集合 |

另有条件收益：2.54/2.56 对部分Provider的异常HTTP 200正文给出模型错误；2.57释放流式并发槽、等待事件流取消清理、关闭由模型名称创建的HTTP客户端。**需分别检查是否经过对应模型、并发限制或客户端所有权路径**，不能合并声称“所有流式泄漏都解决”。我们通常显式创建Model，未全局装配SDK模型并发限制；自建客户端仍需自己关闭。[2.54](https://github.com/pydantic/pydantic-ai/releases/tag/v2.54.0) · [2.56](https://github.com/pydantic/pydantic-ai/releases/tag/v2.56.0) · [2.57](https://github.com/pydantic/pydantic-ai/releases/tag/v2.57.0)

## 3. B 组：升级时必须核验或调整

| **编号** | **当前与新版差异** | **最小改法** | **验收重点** |
| --- | --- | --- | --- |
| B1 依赖联合固定 | 新Harness `0.57.0`精确依赖Core `2.57.0`；CodeMode要Monty `>=1,<2`；Core的OpenAI extra要求`openai>=3.26.0`，Anthropic `>=1.12.1`，Google `>=2.30.0`，Python `>=3.11` | 单独解算并固定经过验证的依赖组合，保留最小extras；不在现有8000上直接试装 | Python3.12满足下限；内部deepfos/DeepCube、aiohttp/httpx2、打包镜像一起检查。不是升级一个包即可 |
| B2 Skill读取环境 | `Skills(directories, include=...)`现在从Workspace读取；当前产品没有传Workspace | 为受信Skill库提供明确、尽量只读的Workspace backend；继续由现有工具处理资源和脚本，不为加载文档开放宿主Shell | 选择范围、无Skill、缺文件、多个库、并发更新及首Run；避免向模型泄露运行机器路径 |
| B3 Skill安装校验 | 当前 `skills/package.py`以构造 `Skills(...)`作为解析校验；新版构造只保存设置，真正读取在Run开始或异步`load()` | 在安装校验中显式执行新版读取/校验入口；对应调整异步边界 | 畸形frontmatter在安装阶段就报错，不能上传成功后首次聊天才失败 |
| B4 Skill调用语义 | 新版识别`disable-model-invocation`、`user-invocable`，并支持多库合并/跳过 | 读回实际catalog，明确被排除项的状态；保持现有“匹配先加载、子文档按需读”提示词 | 不把`user-invocable`当强制加载，也不误称支持了其余`allowed-tools/hooks/model`等frontmatter行为 |
| B5 PTC自定义包装 | 我们覆写CodeMode wrapper及发现公告；新版有新的参数、InstructionPart及执行对象；旧Monty0.x换1.x | 对照公共扩展点逐项适配`ptc/capability.py`、`ptc/toolset.py`；保持现有安全默认，选定新能力才显式转发它的参数 | 普通/native+PTC双通道、动态披露、变量重用、类型规范化、文件不透明中转、超时取消、诊断。`max_duration_secs`新版映射为每片段执行预算，不能当整轮墙钟超时 |
| B6 Hook生命周期 | Core 2.54的`wrap_*`覆盖完整阶段，before/after/error恢复在handler内，handler只能调用一次；[7053](https://github.com/pydantic/pydantic-ai/pull/7053) | 核对`RuntimeDiagnostics`、流式持久化Capability和自定义重试的执行顺序；before若换Model，检查诊断包装是否仍覆盖实际请求 | 模型/工具耗时含义、成功恢复异常、取消flush、一次落库、最终Part与SSE一致；请求视图修改与`ctx.messages`持久修改分开；不要仅更新耗时断言让测试通过 |
| B7 消息与JSON结构 | 新版有WorkspaceRef、TextContent来源metadata及更多诊断字段 | 既有Event Codec先做真实序列化往返；启用新能力后再扩展History/SSE投影 | 新旧记录可读、未知字段不丢事实；后台完成不能被算成用户新提问。无需因此预设新增DDL |
| B8 搜索供应商行为 | Exa/You新增`native`能力，Exa支付失败不再无意义重试 | 核对我们“固定供应商、不静默降级”的装配；需要时显式关闭原生替代 | 用户选了Exa/You就实际走所选路径；402、空结果、限流、代理取消分别回归 |

升级还要核对Google新模型的自动续长输出与默认超时变化，以及Anthropic未指定输出上限时的新默认；保留产品显式输出上限和正式取消。SDK默认连接池1000/100 keep-alive、标量timeout不再扩大connect/pool超时也需容量验证，不能让Provider默认值静默改变产品预算与连接行为。

**B组是升级工程，不是新建业务功能。** 研究没有证据要求数据库变更。若后续状态存储确需DDL，须按根AGENTS另行申请，不能借依赖升级顺带实施。

## 4. C 组：值得优先验证的可选能力

### C1 · PTC跨Run状态：最贴近合同草稿持续修改需求

**当前判断：有潜力，建议优先隔离验证；尚未实现。**

新版`CodeMode(repl_store=...)`接受应用提供的异步`load(ctx)`、`save(ctx, bytes | None)`。第一次`run_code`前恢复Monty状态，Run结束时保存；同Run变量本来就保留，跨Run要主动接这层Store。框架负责dump/load与生命周期，我们负责可信存储、键和并发。

```text
第一轮：run_code → draft = {...} → 程序校验 → 保存REPL快照
第二轮：恢复同一会话REPL → draft["某字段"] = 新值 → 校验 → 保存
交付：程序将draft序列化为JSON并发布文件 → 模型只引用文件ID和变更摘要
```

模型看到的是执行结果和精简摘要，不需要每轮重写整份JSON。**最后一步发布文件不是`repl_store`自带的功能**，仍需接我们现有文件发布流程；不能因此直接把内部导出/UI工具加入PTC，突破现有授权边界。

关键限制：

- 官方示例的内存dict只在同进程有效。跨Pod/重启要另选持久存储；Key至少受当前可信用户、Agent、Session范围约束，不能照抄示例缺ID时的空字符串。
- 快照是 opaque bytes，不是业务JSON；没有业务Schema、修订冲突或逐字段审计。最终文件仍要独立Schema校验和Hash。
- 原始dump不自带认证；只能恢复本服务产生且可信保存的状态。不得把里面的凭据、不透明文件载荷token或旧工具授权跨Run复活。
- 加载失败会新建空REPL，保存失败仅告警；资源限制或Monty版本不匹配也可能丢弃快照。业务草稿必须检测“未恢复/旧修订”，保留阻断，不能静默生成空合同。
- 取消Run仍可能保存空闲REPL，片段超时/reset可能保留上一次快照；程序错误不回滚已经发出的MCP写入。同会话并发需锁或修订检查。
- 快照大小、TTL、清理、下载权限由应用负责。它不替代完整CPython沙盒，也不是 durable execution。

最小验证：两轮只改一字段、跨进程恢复、两用户隔离、授权收缩、快照损坏/过大、取消、保存超时、旧修订冲突；逐字符核对程序生成JSON与草稿一致。此处测试未执行。

证据：[PR10256](https://github.com/pydantic/pydantic-ai/pull/10256)、[固定版 CodeMode文档](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/harness/code-mode.md#keep-the-repl-across-runs)、[`CodeModeReplStore`](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/src/pydantic_ai_harness/pydantic_ai_harness/code_mode/_repl_store.py)。

### C2 · 决策模型：用现成适配做Sheet/有限候选路由

**当前判断：有潜力，等端点可用后做业务对照；尚未接入。**

Core 2.50提供统一`DecisionModel`，2.55加入`OpenAIDecisionsModel`，2.53还有SystemOne。结构化输出字段会转换成判断、候选选择或评分问题，而不是要求模型先写一份自由JSON。

```text
文件读取器：提取Sheet名称、表头、代表行、引用关系
决策调用：input=这些证据；questions=哪些候选可能是BOM
程序：保留候选与不确定状态
业务Agent：读取被选完整Sheet及必要关联Sheet，继续处理
```

在发生模型请求前决定输入证据与问题；新增一次决策请求及其Token成本，目的是减少主Agent无关上下文。不能保证速度和准确率双赢，必须和当前同模型、同文件方案比较。字符串解释、自由金额提取等不适合只靠决策接口，低置信度或无候选要交回主Agent/用户。

与10月14日结论的差异：**SDK现在补齐了OpenAI Decisions适配**，后续不应再优先手写同一HTTP协议；内部网关曾404、订阅端点权限未证实的事实没有被升级改变。OpenRouter普通Chat通了也不等于Decisions通了。既有研究与历史探测见[决策API课题](../19-model-provider-architecture/decision-api-research.md)。

证据：[DecisionModel](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/models/decision.md)、[OpenAI Decisions实现](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/pydantic_ai_slim/pydantic_ai/models/openai_decisions.py)、[2.55发布](https://github.com/pydantic/pydantic-ai/releases/tag/v2.55.0)。

### C3 · Prompt缓存与缓存失效诊断：优化，不改业务答案

**当前判断：有潜力，可在基础升级后做小范围验证。**

新版统一`cache`/`Caching`配置、缓存诊断，以及Planning缓存锚点修复值得吸收。发生在模型请求构建及响应诊断阶段，主要调整缓存断点或供应商字段，不减少真实正文、不改变工具结果。缓存命中只能减少部分输入成本或延迟；不同模型/渠道支持不同，不能承诺每次命中。

产品已有缓存Usage统计，无需再建统计表。先验证固定模型的连续3轮相同前缀、Skill加载前后、工具目录变化后和压缩后行为；比较供应商实际cache_read与输入Token，不用估算命中率代替。

特别区分：我们当前OpenRouter是`OpenAIChatModel(OpenRouterProvider)`，**不是`OpenRouterModel`**。官方对后者的CachePoint修复不能算成当前链路自动受益。订阅端点对显式缓存字段也有不同规则，不全局打开缓存参数。

证据：[2.55缓存能力](https://github.com/pydantic/pydantic-ai/releases/tag/v2.55.0)、[2.57 Planning/OpenRouter修复](https://github.com/pydantic/pydantic-ai/pull/10257)；产品`models/provider_registry.py:build_runtime_model`。

## 5. D 组：有价值，但暂不直接接入

| **方向** | **执行变化 / 模型看到什么** | **对我们的价值与成本** | **当前建议** |
| --- | --- | --- | --- |
| D1 BackgroundTools | 先返回已启动task ID，模型可继续；完成后通过`UserPromptPart`追加结果，新版含origin和tool_call_id元信息 | 适合结果相互独立且主Agent有别的事可做的长只读查询；不会让必须等查询才能回答的链路自然变快，还可能增加模型轮数 | 暂缓。完成消息不经过通常的工具结果/错误hooks，且`ToolReturn.metadata`被丢弃；需先接输出限额、文件授权、History归属和SSE，不能把工具执行耗时提前算作完成 |
| D2 自托管Workspace/Sandbox | FileSystem/Shell通过`ctx.workspace`工作；WorkspaceRef可随消息恢复工作区 | 新版不再只有Modal：Smol微虚机、Bubblewrap、SSH及云后端都值得比较，减少自写执行控制 | 进入既有`SANDBOX-001`候选，未选型。Smol需平台wheel/KVM和磁盘刷写回收验证；Bubblewrap需Linux/userns，默认仍可读取宿主可读文件、看到及影响同用户进程，不直接作为多租户强隔离 |
| D3 MCP Apps | 工具结果关联App资源；宿主读取资源、转发App工具调用，前端渲染受控交互 | 可减少部分标准化交互工具自建页面，但我们不是AG-UI/Vercel客户端，现有UI Deferred协议不会自动变成MCP Apps | 有潜力，当前暂缓；要设计iframe来源/CSP、权限、工具代理及历史恢复。`call_tool_for_app`不经过普通`process_tool_call`，应用代理须补授权；不直接加载第三方HTML替代现有租赁UI |
| D4 更完整的失败尝试诊断 | `FallbackModel`记录失败尝试模型、耗时、错误和Usage | 有助于解释“为何最终成功却等待很久”，但当前产品未统一启用FallbackModel，UI也未映射新诊断 | 可借鉴诊断信息分层；不是新增默认回退模型，不承诺自动恢复供应商失败 |

BackgroundTools源码明确：普通`run_stream()`不交付后台完成结果，`run_stream_events()`等完整运行入口才支持；我们当前使用后者只是满足一个条件，**不能据此判断已可无缝接入**。运行停止/Deferred暂停时未完成任务会取消，且同步工具不能被Python强行立即停下。[后台工具](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/harness/background-tools.md)

本地沙盒候选依据：[Smol](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/harness/smol-sandbox.md)、[Bubblewrap](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/harness/bubblewrap-sandbox.md)、[Workspace](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/workspace.md)。它们仍需接平台身份、附件授权、FileStore产物及失联回收；工作区ID本身不是权限。

## 6. E 组：当前不做，或只保留参考

| **能力** | **判断与原因** |
| --- | --- |
| `Conversation`、`PostgresStepStore`、`PostgresMediaStore` | 新版提供跨Run对话容器和存储接口，但我们已有SQL Event Store、确定性History及FileStore。不能为追新并排写第二份会话事实源；只参考公共消息边界 |
| `ApprovalReviewer` | 它让第二模型替人批准低风险、已要求审批的工具调用；不是强制Skill阅读器，也不处理`CallDeferred`外部UI确认。当前没有需要减少海量审批的场景，不增加额外模型成本与自动放行链 |
| `eager/speculate`提前执行 | 前者边生成代码边执行，后者提前跑可推测调用；可能产生无法撤销的副作用。此前D02明确关闭，保持关闭，不以这次更新重开 |
| `TrajectoryJudge`及在线监督模型 | 此前D03明确不做，保持关闭，不当成租赁推理错误的通用兜底 |
| Durable执行、StepPersistence、Render/PGTask/DBOS等 | 此前D05明确不做；持久REPL不等于崩溃后安全重放MCP写入，不将两者混为一项 |
| Meta等新Provider、更多托管MCP | 有真实供应商或服务需求时再接。MiniMax/StepFun在2.57仍无专用Provider，保留现有薄适配，不为版本升级撤掉它们 |
| Coder/Researcher整套、CLAI2/ACP/gh-aw | 适合本地编码/研究客户端；不替换线上多用户的DeepAgent服务与Console，只按需要借鉴组合方式 |
| Realtime/媒体生成等 | 既有“本项目不做”边界不变；特定Provider更新不算当前文本Agent自动新增能力 |
| 官方SummarizingCompaction近期修复 | 近期保留尾部不足、反复摘要重复等思路可参考；我们没有直接使用该摘要链，不把它算A组，也不叠加第二个压缩器。现有来源Hash、八段摘要、附件Evidence和SSE保持 |

依据：[Conversation及存储发布](https://github.com/pydantic/pydantic-ai/releases/tag/v2.55.0)、[ApprovalReviewer](https://github.com/pydantic/pydantic-ai/blob/v2.57.0/docs/harness/approval-reviewer.md)、[合仓版Providers目录](https://github.com/pydantic/pydantic-ai/tree/v2.57.0/pydantic_ai_slim/pydantic_ai/providers)。

## 7. 建议的验证批次

**第一批：基础升级＋兼容修复。** 单独环境解算依赖；先跑公共Part/旧History往返、Skill安装/首Run、原生/流式工具调用、工具搜索、PTC、压缩和取消；再跑相关全量。最后才重启本地8000做真实Provider冒烟。当前未执行此批次。

**第二批：两个各自可否决的小实验。**

- PTC：同一份合成草稿连续修改，比较“模型重返全文”与“程序修改＋程序生成文件”；记录输出Token、字段无损、状态恢复及失败阻断。不得借实验修改租赁主Agent或业务保存。
- 决策路由：BOM文件固定独立答案，比较漏选/误选Sheet、关联Sheet完整性、总延迟、请求数和总Token。包含多个BOM、纯说明页、无BOM、空表和歧义；不能只测容易样本。

**第三批：按真实需要评估缓存或沙盒。** 缓存以实际命中和计费字段验证；沙盒以目标部署权限、网络、隔离、取消和TTL验证。后台工具与MCP Apps另行设计，不能捆进依赖升级。

所有批次保留：80次模型请求预算边界、UI最后一步与重复回传、断流后的持久化、24K/48K完整包装分页、附件与Skill授权、跨Run文件引用、取消与后台清理。新增能力不能绕过已有安全和事实源边界。

## 8. 源码索引与研究边界

| **需复核的产品入口** | **上游固定版本入口** |
| --- | --- |
| `requirements.txt`；`src/deep_agent/agent/factory.py` | `pydantic_ai_slim/pyproject.toml`；`src/pydantic_ai_harness/pyproject.toml` |
| `skills/runtime.py`、`skills/package.py`、`skills/instructions.py` | `src/pydantic_ai_harness/pydantic_ai_harness/skills/{_capability,_loader}.py`；`tests/harness/` |
| `ptc/capability.py`、`ptc/toolset.py`、`ptc/file_payload.py` | `.../code_mode/{_capability,_toolset,_repl_store}.py` |
| `agent/diagnostics.py`、`agent/message_persistence.py`、`agent/runner.py` | `pydantic_ai_slim/pydantic_ai/_agent_graph.py`、`capabilities/`、`run.py` |
| `agent/context/{compaction,estimator}.py` | Harness `compaction/`；只把实际使用的helper变化计入自动收益 |
| `models/provider_registry.py`、`models/compatible_providers.py` | Core `models/decision.py`、`models/openai_decisions.py`、`providers/` |
| `db/event_codec.py`、`db/serialization.py`及History投影 | Core `messages.py`、`conversation.py`、`workspaces/` |

上表产品路径均相对于`src/deep_agent/`（已带前缀项除外）；上游为主仓库Tag `v2.57.0`。需要复现时进入AI Hub学习副本，不从那里import生产代码。

上游已有测试可作为后续移植验收参考，**本轮仅阅读，未执行**：

- `tests/test_capability_hooks.py`、`tests/test_model_request_message_persistence.py`：完整Hook顺序、临时请求修改与持久历史分离。
- `tests/models/test_openai.py`、`tests/test_transcript_repair.py`：流内错误、思考字段和重复调用ID。
- `tests/test_http_client_lifecycle.py`、`tests/test_concurrency.py`：客户端所有权、流式结束及并发槽释放。
- `tests/harness/code_mode/test_repl_store.py`：跨Run/流式恢复、restart、坏dump、限制变化、取消及Store失败。
- `tests/models/test_openai_decisions.py`：类型化决策、图片、请求上限、拒答与路由。

历史研究位于 `/Users/wuzhiruge/PycharmProjects/AI-hub/agent-labs/upstream-agent-references/indexes/` 的 `deepagent-upstream-assessment-20260922.md` 与 `deepagent-d-series-review-20260925.md`。它们保留当时取舍，本轮不重写历史结论。上游入口见[主仓库Release](https://github.com/pydantic/pydantic-ai/releases/tag/v2.57.0)、[独立仓库迁移说明](https://github.com/pydantic/pydantic-ai-harness#readme)。

**已验证的只是版本、源码路径和接入差异。** 没有声称新版已在DeepAgent运行、真实业务更准/更快、订阅支持Decisions、跨Pod状态已持久化或新沙盒已安全部署。后续版本变化需重做相关核验。
