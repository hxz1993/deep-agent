# DeepAgent 工具检索与渐进披露设计

## 1. 目标与非目标

### 1.1 目标

- 保留 Agent 按 ToolPackage 授权的简单配置体验。
- 从大型 MCP 中为本轮请求找到高相关工具，只披露少量完整 Tool Schema。
- 中文、英文和中英混合业务表达均可检索。
- DeepSeek、私有 OpenAI-compatible 等无原生 Tool Search 的模型也能运行。
- 搜索不改变真实工具协议名、Toolset 执行、Header、SSE、历史和权限语义。
- 小型工具使用确定性条件过滤，不增加不必要的搜索轮次。
- 通过真实评估证明 Token 降低没有换来明显任务成功率下降。

### 1.2 非目标

- 不要求管理员逐个选择 MCP 工具。
- 不用工具检索替代用户授权、MCP 检测或高风险工具审批。
- 不在第一版治理大 Tool Result；结果溢出继续由上下文治理处理。
- 不将所有工具统一改造成 `tool_call(name, args)` Bridge。
- 不把某个 Provider 的原生能力设为唯一实现。
- 不在第一版持久化向量到数据库。

## 2. 核心概念

必须区分四个概念：

| 概念 | 含义 | 生命周期 |
| --- | --- | --- |
| 授权工具全集 | Agent 所选 ToolPackage 在用户权限下允许使用的所有工具 | Agent 配置 + 当前身份 |
| 候选工具目录 | 授权全集经过 enabled、权限、环境和请求条件过滤后的工具 | 当前请求 |
| 已披露工具 | 当前模型请求真正收到完整 Schema 的工具 | 当前 Agent Run |
| 已发现工具 | 本轮由预选或 `search_tools` 提升、后续可以直接调用的工具 | 当前 Agent Run |

工具检索只能从“候选工具目录”向“已披露工具”提升，绝不能把未授权工具加入候选目录。

## 3. 总体链路

```text
agents.capabilities
  ↓ ToolPackage 授权解析
授权 Toolset / MCP Server
  ↓ enabled、用户、App、Space、权限过滤
授权工具全集
  ↓ 请求条件过滤
  ├─ 无附件 → 移除 builtin:attachment
  ├─ 有附件 → 三个附件工具直接可见
  ├─ 小型 builtin / UI / Skill → 直接可见
  └─ 达到阈值的大型 MCP → 进入 Deferred Catalog
        ↓
     当前请求语义预选 Top K
        ↓
     初始模型请求：直接工具 + 预选工具 + search_tools
        ↓
     命中足够 → 调用真实工具
     命中不足 → 模型调用 search_tools
        ↓
     后续模型请求加入新命中工具的完整 Schema
```

第一版推荐“自动预选 + 响应式搜索”同时存在：

- 自动预选减少明显任务必须先搜一次的额外往返。
- `search_tools` 为歧义问题、漏召回和任务中途变化提供修正入口。
- 如果完全依赖自动预选，False Negative 会直接变成“模型认为工具不存在”。
- 如果完全依赖裸搜索，对较弱模型和中文业务词汇的首次搜索提示要求过高。

## 4. 第一层：授权和确定性过滤

### 4.1 ToolPackage 是授权上限

Agent 继续只选择：

```json
[
  "builtin:general",
  "builtin:attachment",
  "mcp:deepfos-mcp-test",
  "ui:deeptable"
]
```

检索不改变数据库结构，不增加 Agent 逐工具清单。未来如有“明确禁止某几个工具”的产品需求，
应单独增加包内 deny/allow 策略；不能用检索是否命中代替授权。

### 4.2 附件包的局部优化

附件工具不进入语义检索：

- 当前请求和原始会话均无当前用户可访问的合法附件：整个 `builtin:attachment` 不披露。
- 存在至少一个合法附件：直接披露 `read_text_attachment`、`read_docx`、`read_xlsx`。
- 不按后缀继续拆分；三个 Schema 很小，后缀和 MIME 还可能不可靠。
- 调用时继续校验 `file_id`、用户和会话归属。
- 过滤依据来自当前请求与原始 `chat_messages`，不能从 Checkpoint 摘要猜测文件权限。

### 4.3 其他直接工具

第一版建议直接披露：

- `builtin:general`
- 已启用的 `builtin:sandbox`
- 少量 UI Deferred 工具
- 当前已激活 Skill 的少量 Function Tool
- 未达到检索阈值的小型 MCP

这些工具数量少，强行统一走搜索只会增加一次模型回合和失败面。

## 5. 第二层：哪些 MCP 启用检索

不要以“所有 MCP 一刀切”启用搜索。按授权过滤后的实际目录评估：

```text
tool_count >= 10
或
estimated_schema_tokens >= 8k～10k
```

满足任一条件时，MCP 工具进入 Deferred Catalog。阈值是第一版内部默认值，不作为管理员必须理解
的业务配置；上线后根据真实观测调整。

一个 Agent 关联多个大型 MCP 时，可以在一次 Retriever 查询中统一召回，但每个结果仍保留
`package_code/server_name`，并继续由各自 MCPToolset 执行。

## 6. 第三层：检索文档

每个工具生成一份只供 Retriever 使用的 `ToolSearchDocument`：

```json
{
  "protocol_name": "deepfos_deepcube_query_cube_data",
  "display_name": "查询财务模型数据",
  "package_code": "mcp:deepfos-mcp-test",
  "package_label": "DeepFOS 平台工具",
  "description": "查询财务模型预算、实际和预测版本数据",
  "parameter_names": ["model_code", "version", "pov", "measures"],
  "parameter_descriptions": ["财务模型编码", "工作版本", "维度组合", "指标"],
  "aliases": ["查数", "取数", "预算查询", "cube query"],
  "domain": ["财务模型", "预算"],
  "actions": ["查询"],
  "entities": ["模型", "版本", "维度成员", "数据"],
  "risk": "read",
  "related_tools": ["deepfos_deepcube_resolve_members"]
}
```

字段来源优先级：

1. MCP 返回的真实 Tool Name、Description 和 Input Schema。
2. ToolPackage/MCP Server 的管理员说明。
3. DeepAgent 管理的非执行元数据：中文展示名、别名、领域、动作、实体和风险。
4. 不从历史聊天自动反向生成并写回工具说明。

Embedding 文本可由上述字段稳定拼接。搜索文档不是模型 Tool Schema，不改变真实 Tool 定义，
也不要求发送给主模型。

工具描述质量仍然重要。语义搜索可以处理“取数/查询数据”这类表达差异，但不能从完全错误或空白
描述中可靠推断业务用途。

## 7. 第四层：查询构造

一次检索由以下可信或半可信信息构造：

- 当前用户真实 `message`。
- 模型调用 `search_tools` 时给出的一个或多个 query。
- `context_refs` 的 `type` 和 `label`。
- 当前已激活 Skill 的名称和简短任务说明。
- 可选的当前业务模块标识。

不直接使用：

- 完整 `context_refs.payload`。
- `metadata`。
- 附件全文。
- Tool Result 大对象。
- Checkpoint 摘要中的资源 ID 作为权限依据。

用户内容可以影响相关性排序，但不能改变授权候选集。所有查询文本都要长度限制、脱敏观测，不能
未经处理写入长期索引。

## 8. 推荐检索算法

### 8.1 混合召回

第一版推荐：

```text
中文感知 BM25 候选
        +
多语言 Embedding 候选
        ↓
Reciprocal Rank Fusion（RRF）
        ↓
精确名称 / 别名 / Package 命中加权
        ↓
权限复检与依赖扩展
        ↓
有序工具名
```

选择 RRF 而不是直接相加原始分数，是因为 BM25 和 Cosine 的分数区间不可直接比较。RRF 只使用
两边排名，第一版更稳定，也更容易解释。后续有足够标注数据后再考虑学习权重。

### 8.2 中文 BM25

BM25 至少需要：

- Unicode/CJK 感知分词。
- 英文、数字、Snake Case 和 Camel Case 切分。
- 领域别名词典，例如“查数/取数/查询”“工作版/版本”“成员/科目”。
- Tool Name 和 Alias 较高权重。
- Description、参数名和参数描述分字段权重。

它既是混合召回的一部分，也是 Embedding 服务不可用时的本地兜底。

### 8.3 多语言 Embedding

Embedding 模型需要满足：

- 中文、英文和中英混合检索。
- Query/Document 非对称检索能力。
- 可固定模型版本，便于索引失效和回归。
- 部署数据边界明确。
- Tool Catalog 规模下延迟可控。

Embedding 后端做成协议接口，不能写死在 AgentFactory：

```text
embed_documents(tool_documents)
embed_query(request_query)
search(query_vector, allowed_tool_names, top_k)
```

### 8.4 排名和数量

推荐初始参数：

- 首轮自动预选：Top 3。
- 模型主动搜索：Top 5。
- 低置信度或结果跨多个 Package：最多扩至 8。
- 相关依赖工具可在 Top K 外额外加入，但必须记录 `selection_reason=dependency`。

这些不是永久业务规则，最终由评估集调整。第一版不使用一个全局固定 Cosine 阈值决定“有/无”，
因为不同 Embedding 模型的分数分布差异很大。

### 8.5 为什么不先用 LLM Selector

LLM Selector 可以作为后续低置信度 Reranker，但第一版不启用：

- 额外一次模型调用。
- 选择模型仍需看到目录摘要。
- 增加 Provider 波动、限流和成本。
- 对工具授权和执行拦截没有帮助。
- DeepAgent 已经需要可离线、可预测的私有部署路径。

## 9. 如何复用 Pydantic AI

推荐复用：

- `DeferredLoadingToolset` / Tool Definition 的 `defer_loading`。
- `ToolSearch` 的自定义 Search Function。
- Pydantic AI 原生/本地路径选择。
- Tool Search Typed Parts 和后续工具激活机制。

DeepAgent 自己负责：

- ToolPackage 授权上限。
- 当前请求条件过滤。
- `ToolSearchDocument` 构建。
- 中文 BM25、Embedding 与融合排序。
- Run 级发现状态边界。
- Catalog Hash、缓存、观测和执行前复检。

不自己重写：

- Provider Tool Search Wire Protocol。
- Tool Call/Result 协议。
- 业务 MCP 执行。
- 模型历史中 Tool Search Part 的基础序列化。

## 10. Run 级状态

发现状态建议限定在一次用户请求触发的完整 Agent Run：

- 同一 Run 内，模型经过工具结果继续思考时，已发现工具继续可见。
- 下一次用户消息开始时重新根据新请求预选，不自动把上一轮全部发现工具继续披露。
- 原始历史保留 Tool Search 事件供审计和调试。
- 构造下一轮模型输入时，不应仅因为历史出现过搜索结果就把所有旧工具再次提升。

Pydantic AI 默认可以从历史 Tool Search Part 恢复发现工具，DeepAgent 需要在历史投影阶段明确控制
“审计历史”和“新 Run 披露状态”的区别，防止会话越聊工具越多、Token 成本逐步回升。

如果未来实测证明连续任务经常复用同一批工具，可增加“最近成功调用工具”轻量提升，但应有数量
上限和过期规则，不在第一版实施。

## 11. Catalog、索引与缓存

### 11.1 Catalog Hash

对稳定序列化后的工具名称、描述和 Input Schema 计算 Hash：

```text
catalog_hash = sha256(sorted(tool definitions))
```

Hash 用于：

- 防止 MCP 重命名或刷新后复用旧命中。
- 定位某次 Run 使用的目录版本。
- 作为检索文档和 Embedding Cache Key 的一部分。

### 11.2 第一版缓存

建议进程内缓存：

```text
{server_name, catalog_hash, retriever_version, embedding_model_version}
→ ToolSearchDocument + lexical index + vectors
```

失效时机：

- MCP 检测并更新。
- MCP 重命名。
- MCP URL/Header/上下文改变并重新检测。
- Tool Schema Hash 改变。
- Embedding 模型或文档拼接版本改变。

第一版不新增数据库表。多实例可能各自重建同一小型工具索引，代价可接受；如果后续目录达到数千
工具、构建明显昂贵，再评估共享索引或数据库持久化。

MCP Tool Catalog 缓存和语义索引相关但不等价：前者减少获取远程 Schema 的网络开销，后者减少
Schema 进入模型的 Token 开销。只做缓存不会降低模型输入 Token。

## 12. Provider 路径

### 12.1 支持原生 Tool Search

满足以下条件时可优先使用 Pydantic AI Provider Native 路径：

- 模型适配器明确声明支持。
- 已通过该 Provider 的真实兼容测试。
- Tool Search 事件可被 DeepAgent SSE 与历史正确归一化。

原生路径可以使用 Provider 自己的 BM25/Regex/Tool Lookup，但 DeepAgent 仍保留相同授权候选集、
Catalog Hash 和观测口径。

### 12.2 不支持原生 Tool Search

DeepSeek 和私有 OpenAI-compatible 默认走本地自定义 Retriever：

- 主模型先看到少量自动预选工具和 `search_tools`。
- 模型搜索后，下一次请求加入真实命中 Schema。
- 目录变化会改变后续 Tool Definitions 前缀，可能降低 Prompt Cache 命中；这是直接工具语义和
  非原生 Provider 的现实代价。

如果后续 Prompt Cache 损失成为主要问题，再评估 Hermes 风格的 `search/describe/call` Bridge；
第一版不为潜在 Cache 收益改变整个执行协议。

## 13. 执行和安全

- Retriever 输入只包含当前已授权工具。
- Retriever 输出只能是候选目录中存在的稳定协议名。
- 加载 Tool Definition 前再次校验 enabled、用户、App、Space 和权限。
- 执行工具前继续走原 Toolset 的全部校验、Header、Hook 和错误处理。
- 未提升的 Deferred 工具即使被模型猜中协议名，也必须拒绝执行。
- Tool Search 不授予写权限，也不替代未来的 ApprovalRequiredToolset。
- 不使用 `RenamedToolset` 做纯 UI 美化；聊天展示名继续与协议名分离。
- 观测日志默认不记录完整参数 Schema、用户正文或敏感 Description。

## 14. Console 和可观测性

### 14.1 配置中心

工具包和工具行增加仅面向管理员的披露标签，不发送给模型：

- `始终可用`
- `按需暴露`
- `有附件时可用`
- `搜索后加载`
- `目录不可用`

大型 MCP 额外展示：

- 工具数。
- Tool Schema Token 估算。
- 披露策略：直接 / 自动 / 搜索。
- Catalog 状态和更新时间。
- Retriever 后端和索引状态。

### 14.2 工作台运行检查器

当前 Run 可展示：

- 授权工具数。
- 条件过滤后候选数。
- 直接披露数。
- 自动预选工具及原因。
- 模型搜索 Query 和命中工具。
- 最终模型可见工具数。
- 减少的 Schema Token 估算。
- 检索耗时和 Retriever 来源。

分数仅用于开发诊断，不要让普通用户理解 Cosine/BM25。历史保存稳定名称和选择原因即可，是否
长期持久化完整分数需另行评估。

## 15. 故障降级

| 故障 | 推荐行为 |
| --- | --- |
| Embedding 服务不可用 | 回退中文 BM25 |
| BM25 与 Embedding 均失败 | 小目录全量披露；大目录返回明确 Tool Discovery 不可用，不静默越过 Token 保护 |
| Search 无命中 | 告诉模型无命中，并允许一次改写查询；不宣称工具一定不存在 |
| MCP Catalog 获取失败 | 沿用现有 MCP 错误隔离，不使用过期 Hash 提升未知工具 |
| 目录更新发生在 Run 中 | 当前 Run 使用固定 Catalog Snapshot；下一 Run 采用新目录 |
| Provider 宣称原生支持但调用失败 | 记录兼容性故障；是否回退本地必须重新开始模型请求，不能在同一响应中混用协议 |

Pydantic 默认搜索描述中的“无命中即工具不存在、不要重试”不适合语义检索第一版。检索器可能
漏召回，建议允许模型用不同表述再搜索一次，同时限制搜索次数避免循环。

## 16. 实施分期

### A. 观测与确定性过滤

- 完成 `TOOL-001` 工具数量和 Schema Token 观测。
- 实施 `TOOL-103`：无附件时隐藏附件包。
- Console 展示披露策略标签。

### B. Retriever 与离线评估

- 定义 `ToolSearchDocument` 和 Retriever 协议。
- 实现中文 BM25。
- 接入选定的多语言 Embedding 后端。
- 建立 DeepFOS 中文评估集并对比候选算法。

### C. Pydantic AI Tool Search 集成

- 大型 MCP 标记 Deferred。
- 自定义 Pydantic ToolSearch Search Function。
- 首轮自动预选 + 模型响应式搜索。
- Run 级状态、Catalog Hash、执行拦截和 SSE/历史归一化。

### D. 真实 Provider 与灰度

- DeepSeek、私有 OpenAI-compatible 真实链路。
- 支持时验证 Anthropic/OpenAI Native 路径。
- 按 Agent 或环境灰度，观察 Recall、任务成功率和 Token 降幅。

### E. 后续增强

- 低置信度 LLM Reranker。
- 最近成功工具轻量提升。
- 超大目录共享索引。
- Prompt Cache 成本明显时评估 Bridge 模式。

## 17. 待决策

### 决策 1：语义 Embedding 部署方式

选项：

1. **本地多语言 Embedding（推荐）**：数据不出部署环境、无在线供应商依赖、成本可预测；增加镜像
   体积和内存，可放主服务或 Sidecar。
2. 远程 Embedding 服务：主镜像轻、模型升级方便；增加数据路径、网络、鉴权、成本和可用性依赖。
3. 第一版仅中文 BM25：最快落地、完全离线；不是语义搜索，对表达变化的 Recall 风险更大。

推荐保留统一 Retriever 接口，产品首选 1；如果当前部署资源不允许，则先用 3 做受控试点，但必须
通过评估集，且文案明确为词法检索。

### 决策 2：首轮自动预选数量

选项：

1. **Top 3（推荐）**：覆盖明显任务，Schema 成本最低，仍可通过搜索补召回。
2. Top 5：更保守地提高 Recall，但每轮多披露 Schema。
3. 不自动预选：实现最简单，但每个 MCP 任务通常增加一次模型搜索往返。

### 决策 3：发现状态生命周期

选项：

1. **每个 Agent Run 重置（推荐）**：成本稳定，不随长会话逐步累积；历史仅用于审计。
2. 整个 Session 保留：连续任务方便，但工具集合会越来越大并削弱本功能价值。
3. Session 保留最近 N 个：折中，但增加状态和失效规则，建议等第一版数据后再做。

除以上三项外，ToolPackage 授权不变、附件整包条件过滤、Pydantic AI 自定义 ToolSearch、混合召回、
执行前权限复检和第一版不新增数据库表，可以作为当前默认结论。
