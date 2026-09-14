# DeepAgent 上游运行时更新评估

核验日期：2026-09-13。产品基线：deep-agent 0.10.0，test@4fd0bd7。本次读取源码、已安装包元数据、Context7、官方 Release/标签源码与 PyPI；未升级依赖、修改运行配置或运行模型测试。下列“直接受益”指通过联合升级和定向兼容验证后，现有调用路径可获得收益，不代表当前已生效。

## 结论与版本

建议安排一次联合依赖升级，先吸收协议、取消和沙箱修复；新产品功能单独评估。候选组合为 AI 2.43.0 + Harness 0.31.0 + Monty 0.0.23，同时调整 OpenAI SDK，不能只升级 Harness。

| 项目 | 产品固定 / 本机实际安装 | 官方最新稳定版 | 发布时刻 UTC | 知识库参考副本 |
| --- | --- | --- | --- | --- |
| pydantic-ai-slim | 2.35.1 / 2.35.1 | 2.43.0 | 2026-09-12 00:54 | 00bc47e，9 月 7 日提交，对应 2.41.0 |
| pydantic-ai-harness | 0.26.0 / 0.26.0 | 0.31.0 | 2026-09-12 00:54 | c897c4e，9 月 7 日提交，对应 0.29.1 |
| pydantic-monty | 0.0.21 / 0.0.21 | 0.0.23 | 2026-09-05 18:36 | 本次通过官方标签与发布信息核验 |

AI/Harness 的 GitHub Release 标题日期写 9 月 11 日，API published_at 为 9 月 12 日 UTC；表中统一用 API 时间。知识库拉过新源码不等于产品已安装新版。此次不改参考仓库的 checkout。

来源：[AI 最新版](https://github.com/pydantic/pydantic-ai/releases/tag/v2.43.0)、[Harness 最新版](https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.31.0)、[Monty 最新版](https://github.com/pydantic/monty/releases/tag/v0.0.23)。完整发布清单和依赖元数据在同目录 `*-releases.json`。

依赖约束已核验 PyPI：AI 2.43.0 的 openai extra 要求 `openai>=3.8.0`，本项目固定 3.5.0；Harness 0.31.0 要求 AI >=2.40.0，其 Code Mode 要求 Monty >=0.0.23，You.com extra 要求 youdotcom>=3.1.2,<4。我们没有安装 code-mode extra，而是直接固定 Monty，因此不能依赖 pip 自动替我们提升该约束。现有 aiohttp/httpx2、deepfos SDK 组合也需一起做依赖解析核对，不预先认定全部兼容。

## 当前到底用了哪些能力

- Core：Provider 适配、Toolset、ToolSearch、IncludeToolReturnSchemas、AbstractCapability、消息/事件、DeferredToolResults、CancellationToken。
- Harness：Planning、Skills、ToolOutputLimits、CodeMode，以及所选 Web Search 后端。
- Monty：Hybrid Code Mode 的隔离 Python 子集执行。真正的第三方 Python/DeepCube SDK 执行仍由独立 Python Sandbox 承担。
- DeepAgent 自己负责：Event Store、普通 History、用户身份、渠道、配置/安装包、文件授权与存储、UI 工具范围、上下文预算和 Compaction Event。

尤其要区分：我们使用 `DeepAgentContextCompaction(AbstractCapability)`，不是直接实例化 Harness SummarizingCompaction。Harness 压缩新增参数不会自动改善现有压缩逻辑。源码入口见文末。

## 一、现有路径直接受益的更新

| 更新与阶段 | 对我们具体有什么用 | 模型/用户看到的变化及条件 | 判断 |
| --- | --- | --- | --- |
| AI 2.35.3 safe_download 的 Cookie 按原始主机隔离；网页下载阶段 | 我们 Web Fetch 直接调用该函数，吸收 Cookie 隔离修复 | 下载内容原则不变；无额外模型请求或 Schema Token。仍保留自己的 DNS/代理防护 | 优先升级收益；不是证明旧环境已经泄漏 |
| AI 2.35.3 修复 DeferredToolRequests.remaining 的跨类别结果 ID 匹配 | 减少 UI 外部调用与审批结果分类异常 | 合法结果继续对应自己的 call ID，不将一个类别的完成误认为另一个 | 对当前 HITL 有关；补 calls/approvals 分类回归 |
| AI 2.40 修复延迟工具披露插入后拆散并行 tool_use/tool_result | 当前 MCP ToolSearch 直接依赖这一链路 | 多个调用与各自返回仍配对，减少 Provider 拒绝历史 | 高价值；验证并行搜索后续聊 |
| AI 2.43 修复 OpenAIChatModel 工具前后文本段边界 | 我们主用 OpenAI-compatible Chat 路径 | “先说明→调工具→再解释”保留分段，而不是错误拼接到旧文本段 | 直接改善实时/历史一致性底层；仍检查产品投影 |
| AI 2.38/2.42 Anthropic 旧 thinking 恢复和规范化历史恢复 | 使用 Anthropic 时降低恢复后下一轮再失败的风险 | Provider 可复用修复后的历史状态；并非删除产品原始 Event | 条件受益；Event 序列化必须保留所需元数据 |
| AI 2.42 ToolReturnContent 校验减少逐 JSON 节点 Python 调用 | 大 MCP JSON 返回的框架处理开销可降低 | 内容不变、不增加 Token；实际延迟收益取决于返回规模 | 可随升级吸收，不承诺未经测量的百分比 |
| Harness 0.30 MontyExecutor 的取消清理加 shield | stop/disconnect 时尽量完成内部任务清理 | 用户停止语义不变；减少取消过程中再次取消导致的清理中断 | 与当前 PTC 直接相关 |
| Harness 0.31 Planning 缓存断点锚定最后一段持久用户内容 | 当前已启用 Planning，可减少无谓的缓存前缀变化 | 计划业务内容不变，费用收益取决于 Provider 的缓存行为 | 随升级吸收 |
| Harness 0.30 Truncate 把截断标记计入 max_chars | 当前溢出降级使用 Truncate，可使长度上限更准确 | 模型可能少看到几个正文字符；不会新增调用 | 直接受益，核对截断边界 |
| Monty 0.0.22 的容器变更 panic、深异步链栈溢出、调度卡死处理与错误路径释放修复 | PTC 面向模型生成代码，异常输入的健壮性有实际价值 | 异常更可能形成可处理错误；不代表沙箱安全已被完整证明 | 与 AI/Harness 联合升级优先 |
| Monty 0.0.23 print 输出合并 | 减少大量细碎 print 的进程通信开销 | 最终结果相同，细粒度输出时序可能变化 | 受益取决于脚本输出量；核对我们的有界审计 |

直接收益来源：[AI 2.35.3](https://github.com/pydantic/pydantic-ai/releases/tag/v2.35.3)、[AI 2.40](https://github.com/pydantic/pydantic-ai/releases/tag/v2.40.0)、[AI 2.42](https://github.com/pydantic/pydantic-ai/releases/tag/v2.42.0)、[AI 2.43](https://github.com/pydantic/pydantic-ai/releases/tag/v2.43.0)、[Harness 0.30](https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.30.0)、[Harness 0.31](https://github.com/pydantic/pydantic-ai-harness/releases/tag/v0.31.0)、[Monty 0.0.22](https://github.com/pydantic/monty/releases/tag/v0.0.22)。

## 二、需要适配或明确验证的变化

### A. 依赖和自定义包装层：联合升级的必要工作

1. 调整 OpenAI SDK 固定值并在隔离环境解析完整 requirements；新 Harness 不能搭配当前 AI 2.35.1。
2. 我们覆盖了 `HybridCodeMode._announce_newly_discovered`、`get_wrapper_toolset` 以及 `HybridCodeModeToolset._partition_callable_tools/_render_catalog/call_tool`，包含上游内部扩展点。必须对照新实现，而非仅依赖 import 成功。
3. AI 2.38 起能力有默认 ID 和合并规则；2.39/2.40 又修复组合不变量。核对 RuntimeDiagnostics、MessagePersistence、Compaction、ToolSearch、ToolOutputLimits 与 HybridCodeMode 的 for_run、状态隔离和 hook 顺序。当前并未证明会报错，但也不能认为自动合并一定等价。
4. AI 2.42 严格校验 DeferredToolResults.approvals。我们的正常入口构造 bool/ToolDenied，符合目标类型；需要关注历史、直接 API 和测试 fixture 中的非法值。不存在“整个 HITL 协议必须重写”的证据。
5. Monty 0.0.22 起未知 resource_limits key 会报错。现有 max_duration_secs/max_memory 是合法键；新增 max_suspensions 如要使用，需要产品配置、预算语义和回归，不是替代 max_tool_calls 的同义字段。

### B. 返回值形状：有收益，也可能改变已有脚本

Harness 0.31 的 Code Mode 会使跨沙箱返回更接近 JSON Schema：Decimal/UUID/日期等叶子值序列化，字典 key 规范为字符串，冲突 key 拒绝；bytes/bytearray 是保留二进制的例外。

发生在 **工具已执行、返回值进入 Monty** 时。例如返回 `{1: Decimal('12.30')}` 更接近 `{'1': '12.30'}`，模型根据 Schema 编写的脚本更容易与实值一致，但旧脚本用整数 key 或把 Decimal 当数值运算可能需要调整。它不是自动保证财务数值正确，更不能替代业务数据类型定义。

本项目应验证 MCP 可信返回 Schema 白名单、普通 JSON、`load_file_data` 的 mapping 访问和不透明文件 token；不要因为新框架能转换类型，就把所有第三方 outputSchema 当成可信输入。[标签实现](https://github.com/pydantic/pydantic-ai-harness/blob/v0.31.0/pydantic_ai_harness/code_mode/_toolset.py)

### C. 指令身份与上下文窗口：逐步接入，不能覆盖产品事实源

AI 2.36 的 InstructionPart.id 能标识指令来自 Agent、Toolset 或 Capability；模型看到的正文未必增加，但诊断可回答“这段提示词来自哪里”。2.38 的 ModelProfile.context_window/RunContext.context_window_used 可以为观测提供标准字段。

我们已有可配置窗口、Tokenizer、预算、Compaction Event 和历史快照。接入前需确定窗口元数据优先级，验证旧 Event 读写、上下文计数和 Provider 专属历史元数据；不能直接以框架默认窗口覆盖管理员配置，不能重复计算指令。

注意：Harness 0.29.1 中 InstructionDeltaPart 兼容代码是为 Core 后续接口预备；本次核验 AI **2.43.0 标签 messages.py 尚无该类**，不要把“动态指令更新已正式完整落地”写成此次升级收益。[AI 2.36](https://github.com/pydantic/pydantic-ai/releases/tag/v2.36.0)、[AI 2.38](https://github.com/pydantic/pydantic-ai/releases/tag/v2.38.0)、[Harness PR 815](https://github.com/pydantic/pydantic-ai-harness/pull/815)

## 三、有潜力，但需要产品设计的能力

| 能力 | 发生阶段与最小变化 | 收益 | 成本、边界及当前决策 |
| --- | --- | --- | --- |
| CodeMode eager（0.31 新增） | 模型完整输出代码前，第一条完整语句就可能启动工具；`fetch A` 与后续代码生成重叠 | 可缩短查询链首段等待 | 外层 run_code hooks 在代码完成后才执行，无法审批已执行语句；部分写入不能撤回、restart 可能重复。当前“完整调用先落库再执行”和有界审计时序需要重构；现有 Hybrid wrapper 也没接 EagerToolset。**暂不启用，可仅对只读查询做隔离实验** |
| CustomEvent / CapabilityEvent / @on_event（AI 2.38/2.40） | hooks/业务代码发出类型化事件，再由监听器映射 `{event,payload}` | 更统一地桥接压缩进度、计划、嵌套调用和诊断 | 不自动持久化，也不自动生成我们的 SSE/History。适合逐步减少自定义回调，保留 Event Store 单一事实源 |
| TrajectoryJudge（Harness 0.30） | 每隔若干步骤用第二个模型审阅近期轨迹，输出 AllGood 或 Steer；Steer 再进入主模型上下文 | 可在复杂数据流/建模跑偏时及时纠正 | 增加模型请求、Token 和新指令；评价失败会在后续 tick/结束时传播，并非默认无损降级；不支持 durable 工作流内运行，不是确定性财务校验。**仅建议复杂长任务试点** |
| SubAgents 委派事件（0.31） | 委派开始/结束新增可观测事件 | 未来展示各子任务状态更方便 | 不是现成的多 Agent 产品；需身份、工具范围、成本、取消、持久化与前端设计。**有潜力，单独课题** |
| Truncate 尾部保留（0.31） | 工具输出过长时模型收到“开头…结尾”，而不是仅开头 | 错误日志末尾/汇总往往有价值 | 需要按资源类型选策略；结构化 JSON 不能当完整结果，已有 FileStore 完整原文继续保留。**小范围值得做** |
| SummarizingCompaction 的 model_settings、嵌套流、tool_return_max_chars（0.27/0.29/0.31） | 压缩请求前限制大工具结果，摘要可流式输出 | 可借鉴摘要延迟与费用控制 | 我们自有压缩链，不会直接获益；需接 CAS、取消、事件锚点、证据保留。摘要内部限长可能丢细节。**借鉴与定向适配，不整体替换** |
| ImageGenerator（AI 2.41） | 直接产生图片，而不依赖正文代码块 | 将来可做图表插图/图片生成 | 需要模型配置、文件存储、权限、成本及前端展示，不是升级后 Agent 自动会画图。**有明确需求再做** |
| Monty 新标准库能力（0.0.22/23） | 代码可使用更多 itertools、functools、base64、str.format 等 | 简单聚合/批处理可少写手工循环 | Harness 0.31 的 CodeMode 描述仍列旧模块集合，需同步实际允许能力、类型 stubs 和测试；不是可用 pandas/numpy 的完整 CPython。**按真实 PTC 场景选用** |

参考：[Code Mode eager 的官方限制](https://github.com/pydantic/pydantic-ai-harness/blob/v0.31.0/pydantic_ai_harness/code_mode/README.md#eager-execution)、[TrajectoryJudge](https://github.com/pydantic/pydantic-ai-harness/blob/v0.31.0/pydantic_ai_harness/trajectory_judge/README.md)、[Core 事件能力](https://github.com/pydantic/pydantic-ai/releases/tag/v2.38.0)。

已有但尚未采用的 Memory、ConversationSearch、SpendLimits、Guardrails 等不应都冒充本次新增。后续可分别研究；尤其 ConversationSearch 默认围绕 Harness StepPersistence 的快照读取，直接增加它会引入另一条会话事实链。我们的跨用户权限和历史搜索应优先适配现有 Event Store。

## 四、本轮暂不投入的更新

- `clai` 启动横幅、MCP 配置、官方 CLI 工具流：我们使用自己的 Console，不需要引入或重做界面。
- ACP、AG-UI/Vercel 协议适配、GitHub Agentic Workflows：当前客户端用自有 SSE/History；更新这些适配器不会自动改善我们的页面。
- Realtime 音频 barge-in、音频队列/挂断：当前企微机器人消息渠道不是 Realtime 双向语音会话。等语音产品需求明确再做。
- Temporal/DBOS/Prefect/AWS Lambda durable 新接口及 StepPersistence 修复：现有 Event Store 不会因此自动具备进程崩溃后无副作用重放。没有 durable engine 接入需求时不迁移架构。
- GitHub Copilot、openai-codex、vLLM Provider、新模型默认配置：新模型支持可在实际使用时受益，但新增认证方式、Provider 枚举与产品配置需要独立设计，不因 SDK 增加就默认开放。
- Bedrock、Azure、Z.AI、XAI 特定修复按实际启用路由判断；我们当前显式工厂主要使用 OpenAI Chat/DeepSeek、Anthropic、Google。Google thinking 档位对齐是选用相应模型时的条件收益。
- Harness 文件系统内容 hash、SnapshotHistorySource 修复：当前用自己的 FileStore、Skill hash 和 Event Store，不能宣称它们会修复我们的安装包校验。
- PlanEventEmitter/on_spend/on_usage/on_fire 弃用：当前 Planning 不使用这些回调，没有需要立即迁移的现有调用；未来扩展优先用事件订阅。

## 五、未发布主干与建议实施顺序

AI 与 Harness 本次 main HEAD 与最新发布标签一致；Monty main 比 0.0.23 多 16 个提交，含 CWD、date.today/datetime.now、format/百分号格式化、math 扩展和数值修复。它们**不在 0.0.23 安装包内**。主干移除 experimental 文案也不代表已发布 1.0。不要为文案或个别语法直接追主干。

Monty 的 CWD/时间等 OS 能力尤其要回到 DeepAgent 的显式权限边界审视：上游支持不等于给 Agent 宿主文件/网络/时间访问权。未发布清单见 `monty-unreleased.json` 与[官方差异](https://github.com/pydantic/monty/compare/v0.0.23...main)。

建议顺序：

1. **联合升级与兼容**：三个包 + OpenAI SDK，定向验证 import、模型工厂、自定义能力组合、旧 Event 读回、MCP 搜索/并行配对、HITL、PTC 返回类型/取消/审计、Web Fetch；先保持 eager 关闭。涉及运行时逻辑，这一步不同于纯 UI 冒烟。
2. **低成本利用**：长日志截断尾部保留、上下文来源诊断、接入已有事件通道。每项有实际触发场景再做。
3. **独立实验**：只读 PTC eager、复杂任务 TrajectoryJudge、SubAgents 可观测性；比较任务成功率、延迟、Token 和取消/失败边界，再决定上线。
4. **暂缓**：替换 Event Store、整体改用 Harness 压缩、durable engine、Realtime 语音、宿主挂载。没有业务需求不为跟进框架而扩张产品。

“最新依赖可以解析安装”与“DeepAgent 已验证可升级”是两回事。本报告完成的是前者的约束核对及适配清单，尚未做联合升级运行测试。

## 本地源码定位

- `requirements.txt:4`：AI/Harness/Monty/OpenAI 固定版本。
- `src/deep_agent/agent/factory.py:323`：返回 Schema、HybridCodeMode 与 Planning 装配。
- `src/deep_agent/agent/message_persistence.py:32`：完整消息持久化及最外层 hooks。
- `src/deep_agent/agent/deferred.py:19`：calls 与 approvals 构造。
- `src/deep_agent/agent/tool_discovery.py:377`：自定义 ToolSearch。
- `src/deep_agent/ptc/capability.py:13`、`ptc/toolset.py:187`：Hybrid 覆盖点、原生工具保留及审计。
- `src/deep_agent/agent/tool_output_limits.py:378`：大工具结果与平台文件存储。
- `src/deep_agent/agent/context/compaction.py:781`、`context/estimator.py:363`：自有压缩和计数。
- `src/deep_agent/web/runtime.py:240`：safe_download 使用位置。
