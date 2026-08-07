# 工具检索研究与验证

## 1. 研究问题

当前 DeepAgent 已由 ToolPackage 表达 Agent 的授权上限。问题不是“管理员是否选中了 MCP”，而是
选中一个大型 MCP 后，该 Server 的完整 Tool Schema 会在每次模型请求中重复披露。

2026-08-06 Test 环境观测到：

- `deepfos-mcp-test` 单包包含 24 个工具。
- 单包纯 Tool Schema 的保守估算约 36k Token。
- 某 Agent 共 35 个可见函数时，预算估算中的工具定义约 66k Token。
- 一次只实际调用两个工具的电子表格请求，仍承担了完整工具目录的输入成本。

不同 Tokenizer、Provider 缓存和预算估算口径会产生不同绝对值，因此上述数字不是结算账单；但
它们足以证明：大型 MCP 的“每轮全量 Schema 披露”已经值得单独治理。

本轮重点回答：

1. Pydantic AI 自带 Tool Search 是否是真正的语义搜索。
2. DeepSeek 和私有 OpenAI-compatible Provider 是否能直接复用该能力。
3. Pi、Hermes、OpenCode、DeerFlow、Deep Agents、Agno 如何处理大量工具。
4. DeepAgent 应如何同时保留 ToolPackage 授权、附件条件过滤和 MCP 语义发现。

## 2. Pydantic AI Core

### 2.1 运行模型

Pydantic AI 的 `ToolSearch` / `DeferredLoadingToolset` 把工具分为：

- 初始直接可见工具。
- 标记为 `defer_loading=True`、等待发现的工具。
- 一个本地 `search_tools` 入口，或 Provider 原生 Tool Search。

模型先看到搜索入口；搜索命中后，Pydantic AI 在后续模型请求中加入命中工具的真实定义。命中后
模型直接调用原协议名，原 Toolset 负责执行；Tool Search 不复制业务执行逻辑。

当前安装版本：

```text
pydantic-ai-slim    2.26.0
pydantic-ai-harness 0.10.0
```

### 2.2 自带本地算法不是语义搜索

下述跨 Provider 关键词回退是 2.14.1 阶段的黑盒基线；DeepAgent 已升级到 2.26.0，后续
`TOOL-201` 实施前必须重新验证 2.26 的 Tool Search、Deferred Loading、Provider 原生路径和
本地回退，不直接照搬旧版本结论：

```text
query → 正则 [a-z0-9]+ 分词
tool.name + tool.description → 同样分词
得分 = 两边去重词元的交集数量
按得分倒序，最多返回 10 个
```

它没有：

- Embedding。
- 向量相似度。
- BM25 词频和逆文档频率。
- 同义词、领域词典或跨语言映射。
- 参数 Schema 内容检索。

因为正则只保留英文小写字母和数字，纯中文查询甚至得不到检索词元。

### 2.3 本地黑盒验证

使用真实 DeepFOS 风格工具名和英文描述执行安装版本的 `keywords_search_fn`：

| 查询 | 返回 |
| --- | --- |
| `查询当前财务模型预算工作版的数据` | `[]` |
| `查询预算数据` | `[]` |
| `financial model budget data` | 查询、写入、维度、当前模型等多个工具 |
| `cube dimensions members` | 维度、成员、查询、写入工具 |
| `查询 budget data from financial cube` | 能命中英文描述相关工具 |

结论：

- 英文词面重合时可以工作。
- 中文业务请求会直接失败。
- 即使英文命中，也只是词面相关，不保证动作语义正确，例如 query 与 save 容易同时入选。
- 不能把“让模型先把中文改写成英文关键词”作为可靠基础设施；这会把检索成败依赖于第一轮模型的
  提示遵循和领域翻译能力。

因此，Pydantic AI 的机制可以复用，但默认本地 `keywords` 算法不能直接作为 DeepAgent 第一版
检索器。

### 2.4 Provider 原生 Tool Search

Pydantic AI 会在支持的模型上使用 Provider 原生能力：

- Anthropic 支持 BM25 和 Regex Tool Search。
- OpenAI Responses 的新模型支持托管 Tool Search。
- 不支持原生能力的 Provider 才走本地 `search_tools`。

原生能力的优势是 Tool Catalog 由 Provider 服务端处理，完整 Tool Schema 不进入模型提示词，
并且 Tool Reference 追加方式更有利于 Prompt Cache。

但是 DeepAgent 的常见模型包含 DeepSeek、私有 OpenAI-compatible 和其他兼容网关，不能假设
它们支持真正的 OpenAI Responses Tool Search。兼容 Chat Completions 协议不等于支持 Provider
Native Tool Search。因此必须有跨 Provider 的本地检索实现。

### 2.5 可替换检索函数

Pydantic AI 允许传入自定义搜索函数：

```text
(RunContext, queries, ToolDefinition[]) → 有序工具名列表
```

自定义函数可以读取真实 `ToolDefinition` 的名称、描述和参数 Schema。这为 DeepAgent 接入中文
BM25、多语言 Embedding 和混合排序提供了正确扩展点，无需自己重写 Tool Search 状态机。

### 2.6 Pydantic AI Harness 没有另一套语义检索器

Harness 0.10 会配合 Core ToolSearch 工作，但没有内置的中文 BM25、Embedding Retriever 或
语义 Tool Finder：

- `CodeMode(dynamic_catalog=True)` 解决的是发现后工具目录变化与 Prompt Cache 稳定性。
- CodeMode 的 Tool Selector 决定哪些工具进入代码执行包装，不负责根据用户语义召回工具。
- Dynamic Workflow 的 `defer_loading` 仍由 Core ToolSearch 驱动。
- Compaction 会识别 ToolSearch 的类型化消息，避免当作普通文本破坏，但不参与检索排名。

因此本轮不需要为了语义工具检索升级 Harness；正确扩展点仍是 Core `ToolSearch` 的自定义 Search
Function。Harness CodeMode 属于另一种执行形态，不能被误认为 Retriever。

## 3. Provider 官方方案

### 3.1 Anthropic

Anthropic 官方 Tool Search：

- 建议工具达到约 10 个或 Tool Schema 超过约 10k Token 时采用。
- 建议 3～5 个高频工具保持直接可见。
- 原生搜索会检索名称、描述、参数名和参数描述。
- 默认一次最多加载少量候选，Tool Reference 在后续请求中追加。
- 官方另提供 Embedding Tool Search Cookbook：把工具名、描述和参数转为文本，使用 Embedding 与
  Cosine Similarity 召回。

这说明“Tool Search”并不等于某一种算法：原生 BM25 是词法检索，Embedding Cookbook 才是
语义检索。

### 3.2 OpenAI

OpenAI 新模型支持轻量 Tool Catalog + Tool Lookup，目标同样是避免所有 Tool Schema 都进入主
提示词。OpenAI 公布的 MCP Atlas 基准中，在保持准确率的同时显著降低总 Token。

该方案适合明确支持原生 Tool Search 的模型，但不能作为 DeepAgent 的唯一实现。

## 4. AI Labs 项目对比

### 4.1 Pi

Pi 支持扩展先注册大量工具，只激活少量工具；一个 `search_tools` Loader 可以通过
`pi.setActiveTools()` 把命中工具追加到下一次模型请求。

特点：

- Loader 和业务工具是分开的。
- 工具必须预先注册，搜索不能创建或越权启用未知工具。
- 官方示例使用简单关键词匹配，但明确允许替换为 BM25、Embedding、远程目录或项目路由。
- 对支持的 Provider 可以使用原生 deferred loading。
- 推荐只增量追加工具以获得更稳定的 Prompt Cache。

Pi 证明了“搜索只负责激活，业务工具仍按原 Toolset 执行”是可行且干净的边界。

### 4.2 Hermes Agent

Hermes 把核心工具保持直接可见，把 MCP 和插件工具藏在三个 Bridge 后：

- `tool_search`
- `tool_describe`
- `tool_call`

目录按规模逐级降级：

1. 名称 + 简短描述。
2. 仅名称。
3. 仅 Server 名称 + 工具数。
4. 完全通过 Bridge 搜索。

Hermes 搜索本身是 BM25 风格的名称、描述和参数名检索，但现有词元规则仍偏 ASCII，不是中文
语义检索。Bridge 的优势是非原生 Provider 的 Tool Definitions 前缀更稳定；代价是模型不再直接
调用真实工具，而是通过 `tool_call` 二次转发，SSE、历史、审批、调试和错误归属都更复杂。

Hermes 自带评估还说明一个重要事实：完全隐藏目录不一定更好。对较弱模型，至少展示紧凑的工具
名称或 Server 能力摘要，通常比只给一个没有线索的搜索框更容易发现正确能力。

### 4.3 DeerFlow

DeerFlow 只延迟 MCP 工具，核心工具始终直接可见。它提供：

- `tool_search`：正则或精确名称搜索，最多 5 个。
- `<available-deferred-tools>`：给模型展示延迟工具名称，不展示完整 Schema。
- Routing Metadata：按最新用户文本的关键词和优先级，首轮自动提升 Top K 工具。
- `catalog_hash`：目录变化后废弃旧提升状态。
- 执行前拦截：未提升的工具即使被模型猜到名称，也不允许执行。
- 授权先于目录和搜索，搜索不能扩大权限。

DeerFlow 的“先自动路由，再允许模型继续搜索”很适合 DeepAgent；但它的关键词/正则路由仍不是
语义搜索，中文领域表达仍需要更好的 Retriever。

### 4.4 OpenCode

OpenCode 常规模式主要做静态 Provider、权限和配置过滤，然后披露允许的 MCP 工具。实验性
Code Mode 会把大量工具替换成一个代码执行/编排入口和目录树。

Code Mode 可以显著减少工具数量，但它改变了执行协议：模型生成代码，由统一执行器再调用底层
工具。对 DeepAgent 而言会扩大 Python Sandbox、权限、审计、SSE 和历史兼容范围，不适合作为
第一版 Tool Search。

### 4.5 Deep Agents 与 Agno

- Deep Agents 更强调 Middleware 根据状态、能力和任务阶段过滤工具，不提供一个通用语义 Tool
  Finder。它适合作为确定性过滤参考。
- Agno 提供工具、Toolkit、include/exclude 和按 Session 依赖动态装配；其知识库有 Embedding，
  但工具目录本身没有统一的语义发现机制。

两者进一步说明：确定性过滤是基础，语义发现通常仍需应用自行组合。

## 5. 其他框架方向

### 5.1 LangChain

LangChain 有两类能力：

- Provider Tool Search：依赖 Provider 原生支持。
- LLM Tool Selector：让另一个 LLM 阅读工具名称和描述，以结构化输出选出最多 N 个工具。

LLM Selector 的语义能力通常强于词面匹配，但它仍要把目录摘要发送给选择模型，并增加一次模型
调用、成本、延迟和新的故障点。它适合作为可选 Reranker 或低置信度兜底，不适合作为 DeepAgent
第一层固定依赖。

### 5.2 Haystack 与 Spring AI

- Haystack `SearchableToolset` 使用 BM25 检索并控制 `top_k` 与启用阈值。
- Spring AI 的 Dynamic Tool Search 把索引抽象成可插拔实现，包括 Vector、Lucene 和 Regex。

它们支持本课题的核心结论：检索状态机和检索算法应解耦，DeepAgent 不应该把某个 Embedding
供应商写死在 AgentFactory 中。

## 6. 算法候选比较

| 方案 | 中文语义 | 新外部依赖 | 额外模型调用 | 主要优点 | 主要缺点 |
| --- | --- | --- | --- | --- | --- |
| Pydantic 默认关键词 | 很差 | 无 | 无 | 直接可用 | 中文为空、无同义词 |
| 中文 BM25 | 一般 | 无或轻量 | 无 | 快、可解释、可离线 | 仍依赖词面和元数据质量 |
| 多语言 Embedding | 好 | 本地模型或远程服务 | 无主模型调用 | 跨语言、同义表达 | 需要模型、索引和版本管理 |
| BM25 + Embedding 混合 | 最适合首选 | 同上 | 无主模型调用 | 精确名与语义兼顾 | 实现和评估更完整 |
| LLM Selector | 好 | 选择模型 | 每轮通常多一次 | 理解复杂意图 | 成本、延迟、目录泄露、稳定性 |
| Provider Native | 取决于 Provider | Provider | 通常无额外应用调用 | Cache 和服务端效率最佳 | DeepSeek/私有兼容性不足 |
| Bridge Search + Call | 取决于检索器 | 可选 | 至少一次搜索轮次 | Tool 前缀稳定 | 改变执行、SSE 与历史语义 |

### 6.1 中文 BM25 快速验证

本轮还用 Unicode/CJK 分词和简单领域元数据进行了 BM25 风格验证。中文请求可以召回财务模型
相关工具，明显优于 Pydantic 默认实现；但“写入预算值”仍可能把 query 排在 save 前面，因为
动作同义词、描述质量和共有领域词会产生噪声。

结论：中文 BM25 是必须具备的离线兜底，但不能冒充语义搜索；推荐与多语言 Embedding 融合。

## 7. 研究结论

1. 复用 Pydantic AI 的 Deferred Loading 和 ToolSearch 状态机是合理的。
2. Pydantic AI 默认本地关键词算法不能满足中文 DeepFOS 请求。
3. DeepAgent 必须提供跨 Provider Retriever，不能只依赖 Anthropic/OpenAI 原生能力。
4. 推荐“确定性过滤 + 混合语义预选 + 模型按需继续搜索”，而不是只给一个裸搜索框。
5. ToolPackage 继续作为授权上限，工具检索只是本轮 Schema 披露优化。
6. 附件等小型工具不经过检索：无附件时整包隐藏，有附件时直接提供三个读取工具。
7. 第一版不采用 LLM Selector、Bridge Tool Call 或 OpenCode Code Mode。
8. 搜索质量最终依赖真实 DeepFOS 中文评估集，不能凭几个演示查询设定固定阈值。

## 8. 主要资料

- [Pydantic AI Tool Search](https://pydantic.dev/docs/ai/capabilities/tool-search/)
- [Anthropic Tool Search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [Anthropic Tool Search with Embeddings](https://platform.claude.com/cookbook/tool-use-tool-search-with-embeddings)
- [OpenAI GPT-5.4 Tool Search](https://openai.com/index/introducing-gpt-5-4/)
- [Pi Extension Tool Loading](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md)
- [Hermes Agent Tool Search](https://github.com/NousResearch/hermes-agent/blob/main/tools/tool_search.py)
- [DeerFlow Tool Search](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/tools/builtins/tool_search.py)
- [LangChain Middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- [Haystack SearchableToolset](https://docs.haystack.deepset.ai/docs/searchabletoolset)
- [Spring AI Dynamic Tool Search](https://docs.spring.io/spring-ai/reference/guides/dynamic-tool-search.html)
