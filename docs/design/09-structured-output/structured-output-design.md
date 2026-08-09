# DeepAgent 结构化最终输出设计

> 首次研究：2026-08-03
>
> 当前运行基线：`pydantic-ai-slim 2.26.0` + `pydantic-ai-harness 0.18.0`
>
> 状态：设计结论；当前 DeepAgent 尚未启用 `ToolOutput`、`NativeOutput` 或 `PromptedOutput`

## 1. 结论

DeepAgent 应把三类结构化数据分开：

| 类型 | 权威生产者 | 运行语义 | 典型用途 |
| --- | --- | --- | --- |
| 业务工具结果 | Function/MCP 后端 | 工具完成后返回给模型，Agent 可继续运行 | 查询结果、公式图、文件内容、来源证据 |
| UI Deferred 结果 | 浏览器、用户或外部任务 | Agent 暂停，收到结果后恢复 | 确认、选择、表单、异步任务 |
| 结构化最终输出 | 模型 + Pydantic AI Output | 校验成功后结束当前 Agent Run | 分类结果、报告摘要、流程节点输入、API 机器可读响应 |

当前推荐：

1. 首版结构化最终输出使用 Pydantic AI Core `ToolOutput`，不修改 Pydantic AI 源码。
2. 普通 Agent 保持 `[str, DeferredToolRequests]`；明确配置输出契约组后，可以同时提供多种 Output Tool。
3. 默认的可选模式仍允许 `str`，模型可按任务需要选择普通文本或某个 Output Tool；只有显式严格模式才移除 `str`，避免绕过结构化契约。
4. 固定业务结构优先使用 Pydantic Model；动态 JSON Schema 必须由 DeepAgent 再执行一次完整校验。
5. 多种互斥结果优先注册为多个小型 Output Tool，不使用一个塞满 Optional 字段的宽松结构。
6. Output Tool 与 UI Deferred 可以存在于同一个 Agent，但不能合并成同一种运行语义。
7. MCP 已经返回完整权威结构时，前端直接渲染 `tool_result`；不得让模型通过 Output Tool 重写事实。
8. Harness 0.18 可补充最终结果业务校验，但不替代 Core 的结构化输出能力，也不是首版前置依赖。
9. Output Tool 不是 Agent 可选执行能力，不加入现有 `agents.capabilities` 或 `/tools` 工具目录；
   Agent 通过独立的输出契约选择一组互斥终止类型。

## 2. 设计边界

本文只讨论“当前 Agent Run 如何形成最终输出”，不讨论：

- 普通工具参数和结果 Schema 的设计。
- MCP 业务结果的计算逻辑。
- UI Tool 的具体表单组件。
- 高风险工具的执行审批和授权。
- 大工具结果的截断、摘要和外置存储。
- 整个多轮会话什么时候算业务完成。

每条用户消息都会触发一次独立 Agent Run。Output Tool 能表示“本次 Run 已完成”，但无法自动
判断一个可能持续多轮的业务会话是否已经最终结束。需要“聊完后生成成果”时，应由 Agent 固定
配置、请求级显式参数或独立的“生成成果”动作决定，不能让前后端猜测用户是否还会继续追问。

## 3. Pydantic AI 的原始语义

### 3.1 Output Tool 从第一次模型请求起可见

Pydantic AI 不会先判断模型是否进入 Final 阶段，再临时添加 Output Tool。启用严格结构化输出后，
本次 Run 的每次模型请求都会携带普通工具和 Output Tool 定义：

```text
模型请求 1
  可见：search、read_file、return_report
  模型响应：调用 search

模型请求 2
  输入：search 的 ToolReturn
  可见：search、read_file、return_report
  模型响应：调用 read_file

模型请求 3
  输入：read_file 的 ToolReturn
  可见：search、read_file、return_report
  模型响应：调用 return_report({...})
```

模型调用 `return_report` 的同一个响应同时表达：

1. 当前任务所需信息已经具备。
2. 当前参数就是最终结构化结果。

协议中不存在一个更早的 `final=true` 信号，也不存在“倒数第二轮先通知框架，再执行最后一轮”
的过程。

### 3.2 校验成功才真正结束

```text
模型调用 Output Tool
  → Pydantic AI 校验调用参数
      ├─ 成功：参数或输出函数结果成为 result.output，当前 Run 结束
      └─ 失败：向模型返回校验错误，在预算内要求重试
```

以下情况尚未形成最终结果：

- 参数不符合 Schema。
- Output Function 主动抛出 `ModelRetry`。
- 输出业务校验要求模型重新生成。

成功结果不会再作为普通 Tool Return 发回模型，因此不会多一次“请把结果复述成最终答案”的模型
调用。

### 3.3 为什么工具定义需要重复发送

需要区分两个调用边界：

| 边界 | 是否需要每轮重复传 Schema | 原因 |
| --- | --- | --- |
| 浏览器 → DeepAgent | 不一定 | Agent 固定 Schema 可以由后端从配置读取 |
| DeepAgent → Model Provider | 需要 | 标准模型请求无状态，每次请求都要声明当前可用工具 |

如果采用 Agent 固定配置，浏览器仍只发送 `agent_id` 和用户消息；DeepAgent 在运行时读取
`output_schema` 并为本次 Run 的所有模型请求准备 Output Tool。只有采用请求级临时契约时，调用方
才需要在该次请求中显式携带 Schema。

为了控制 Schema Token：

- 未配置结构化输出的 Agent 不添加 Output Tool。
- 工具名、描述和 Schema 保持简洁且顺序稳定。
- 结合 Provider Prompt Cache 观测实际缓存命中，不通过延迟注入工具破坏 Agent Loop。

## 4. 三种结构化输出模式

| 模式 | 约束方式 | 优点 | 风险与适用边界 |
| --- | --- | --- | --- |
| `ToolOutput` | 模型调用专用输出工具 | Tool Calling 兼容面广；终止语义明确 | 每次模型请求携带工具 Schema；首版推荐 |
| `NativeOutput` | Provider 原生 response schema | Provider 支持时约束强 | Provider 和模型能力不一致，需要能力探测和回归 |
| `PromptedOutput` | Prompt 注入 Schema，解析模型文本 | 对不支持 Tool Calling 的模型兼容 | 约束最弱，容易出现截断和非 JSON 内容，只作兜底 |

DeepAgent 第一版不自动在三种模式间切换。先使用跨 Provider 更稳定的 `ToolOutput`；未来模型目录
具备可靠的结构化输出能力标签后，再评估按模型选择 `NativeOutput`。

## 5. Output Tool 与 UI Deferred

### 5.1 两者生命周期不同

| 环节 | UI Tool | Output Tool |
| --- | --- | --- |
| Pydantic AI 入口 | `ExternalToolset` | `output_type=ToolOutput(...)` |
| 模型调用的含义 | 请求浏览器或外部系统完成交互 | 提交当前 Run 的最终答案 |
| 调用后状态 | 暂停 | 结束 |
| 应用层结果 | `DeferredToolRequests` | 已校验的结构化对象 |
| 是否等待前端 | 是 | 否 |
| 恢复输入 | `DeferredToolResults` | 无 |
| 模型是否继续 | 恢复后通常继续 | 校验成功后不继续 |

`DeferredToolRequests` 不是模型看到的工具，而是 Pydantic AI 返回给宿主应用的一种暂停状态：

```text
模型调用 builtin__choice
  → Pydantic AI 返回 DeferredToolRequests
  → DeepAgent 持久化 Pending 状态并通过 SSE 通知前端
  → 用户提交选择
  → 前端发送 DeferredToolResults
  → DeepAgent 恢复 Agent Run
  → 结果作为 ToolReturn 发给模型
```

因此下面的输出类型表示两种可能的 Run 结果：

```python
output_type=[
    ToolOutput(FinalResult, name="return_final_result"),
    DeferredToolRequests,
]
```

- `FinalResult`：本轮已经完成。
- `DeferredToolRequests`：本轮尚未完成，等待外部结果后恢复。

### 5.2 不使用 Deferred 模拟 Output Tool

把 Output Tool 做成特殊 UI Tool 会产生不必要的链路：

```text
模型提交最终参数
  → 会话 Pending
  → 前端原样回传
  → DeepAgent 恢复
  → 模型收到 ToolReturn
  → 模型可能再次生成内容
```

如果 DeepAgent 拦截这个特殊 Deferred 调用并直接终止，就等于自行重写 Pydantic AI 已有的输出
状态机，同时会混淆 Pending、取消、恢复、工具配对和历史语义。

需要用户确认时，应保持两个阶段：

```text
UI Deferred Tool 收集或确认用户输入
  → 恢复模型
  → 模型调用 Output Tool 提交最终结构
```

## 6. 多种输出结构

不同业务结果应注册成多个独立 Output Tool：

```python
class ReportResult(BaseModel):
    title: str
    summary: str
    risks: list[str]


class ClassificationResult(BaseModel):
    category: str
    confidence: float


output_type = [
    ToolOutput(ReportResult, name="return_report"),
    ToolOutput(ClassificationResult, name="return_classification"),
    DeferredToolRequests,
]
```

Pydantic AI会把它们作为多个清晰的小型 Output Tool 提供给模型。模型选择其中一种，首个成功
校验的结果成为最终输出。

不推荐把互斥结构合并成一个所有字段均为 Optional 的模型：

```python
class BadFinalResult(BaseModel):
    report_title: str | None = None
    category: str | None = None
    confidence: float | None = None
```

这种设计不能保证字段组合有效，也让模型和前端都需要猜测结果类型。只有以下情况才使用
Optional：

- 字段在同一种业务结果中确实可选。
- 分支结构使用明确 discriminator 并能严格校验。

前端需要统一协议时，由 DeepAgent 在 API 层归一化，不要求模型面对宽松 Schema：

```json
{
  "output_type": "report",
  "data": {
    "title": "分析报告",
    "summary": "...",
    "risks": []
  }
}
```

### 6.1 输出契约组是 Agent 选择颗粒度

需要区分四层：

| 层级 | 例子 | 作用 |
| --- | --- | --- |
| UI Tool 定义 | `deep_cube__clarify` | 一项稳定的交互协议和参数 Schema |
| UI Tool Package | `ui:deepcube` | Agent 一次选择的一组 UI Tool；运行时展开为多个 External Tool |
| Output Tool 定义 | `return_deepcube_code` | 模型可选择的一种终止结果 Schema |
| 输出契约组 | `deepcube.v1` | Agent 或本次请求选择的一组互斥 Output Tool |

Output Tool 不应复用 `agents.capabilities`：

- Tool Package 表示模型在运行中可以调用的一组执行、查询或交互工具。
- Output Contract 表示模型如何结束本次 Run。
- UI Tool 调用后可能 Pending 并恢复；Output Tool 校验成功后直接结束。
- Console 可以都展示在 Agent 配置中，但必须分成“能力”和“输出契约”两个配置区。

推荐配置形态：

```json
{
  "capabilities": ["builtin:general", "mcp:deepcube", "ui:deepcube"],
  "output_contract": "deepcube.v1"
}
```

`deepcube.v1` 内部可以注册多个互斥输出：

```text
return_deepcube_answer
return_deepcube_code
return_deepcube_config
return_deepcube_mixed
```

Agent 选择的是整个契约组，不应要求管理员逐个勾选这些 Output Tool。否则可能出现只启用
`return_deepcube_config`、却漏掉普通说明或混合结果，导致同一 Agent 无法覆盖完整终止路径。

UI Tool Package 与 Output Tool 是两条独立配置链路。v0.3.2 已确定 UI 工具整包选择，旧
`ui:<tool_name>` 只在 Alembic 002 中一次性迁移；这不改变 Output Tool 的实施前置条件，也不改变
工具协议名、SSE 和历史记录。

## 7. 普通工具与 Output Tool 同响应边界

模型可能在同一个 ModelResponse 中同时生成普通工具调用和 Output Tool。Pydantic AI通过
`end_strategy` 决定同批其他调用的处理方式；流式 API 还存在“先识别到的最终输出优先”的行为。

DeepAgent 实施时必须：

1. 在 Output Tool 描述中要求：只有普通工具、UI 交互和审批全部完成后才允许提交最终输出。
2. 明确选择并记录 `end_strategy`，不得依赖框架默认值而不测试。
3. 覆盖“普通工具在前、Output Tool 在后”和相反顺序。
4. 覆盖同响应出现 `DeferredToolRequests` 与 Output Tool 的优先级。
5. 确认普通和 SSE 链路结果一致；不能出现普通请求完成、SSE 请求 Pending 的差异。
6. 已成功形成 Output 后，不再向模型发送普通工具结果或追加一次模型请求。

这个边界是结构化输出落地的主要运行风险之一。

## 8. AI Labs 参考项目

| 项目 | 做法 | 对 DeepAgent 的启示 |
| --- | --- | --- |
| Pi | 示例扩展注册 `structured_output`，结果带 `terminate: true`；底层 Loop 在终止工具批次后停止 | Output Tool 应具备明确终止语义，不需要额外模型复述 |
| OpenCode | 请求声明 JSON Schema，服务端动态注入固定 `StructuredOutput`，强制调用并单独保存结构化结果 | 适合请求级临时输出契约；客户端只提交 Schema，不提交可执行工具逻辑 |
| Deep Agents | `response_format` 支持 Tool、Provider 和 Auto Strategy，结果保存在 `structured_response` | 输出策略可以抽象，但首版不必过早自动化 Provider 选择 |
| Agno | `output_schema` 按模型能力选择原生结构、JSON Schema、JSON Mode 或文本解析 | 兼容链路完整但故障面较多，适合作为后续演进参考 |
| Hermes | 正常最终结果为自由文本，部分内部流程通过 Prompt 生成 YAML 后解析和兜底 | Prompt 解析适合低风险内部摘要，不适合作为强契约 |

推荐采用“OpenCode 的产品配置边界 + Pi 的明确终止语义”，运行时直接使用 Pydantic AI Core，
不在 DeepAgent 内复制这些项目的 Agent Loop。

## 9. Pydantic AI Harness 的边界

`pydantic-ai-harness 0.18.0` 没有另一套结构化输出机制。结构化结果仍由 Pydantic AI Core 的
`ToolOutput`、`NativeOutput` 和 `PromptedOutput` 负责。

Harness Guardrail 可以在最终结果形成后增加业务规则：

```text
ToolOutput 校验结构
  → OutputGuard 检查引用、敏感数据或业务约束
      ├─ allow
      ├─ block
      ├─ replace
      └─ retry
```

流式链路仍有明确边界：

- 只检查最终输出，已经通过 SSE 发出的文本无法撤回。
- 流式模式不支持 OutputGuard 输出重试。

因此不把 Harness 作为结构化输出的前置依赖。首版可使用 Pydantic AI
输出校验、DeepAgent `jsonschema` 二次校验和有限重试；需要引用、合规或敏感信息检查时再评估
Harness Guardrail。

Harness 的 `OverflowingToolOutput` / Tool Output Limits 管理的是普通工具结果过大，不是 Agent
最终结构化输出，两个概念不得混用。

## 10. DeepAgent 推荐运行设计

### 10.1 输出契约来源

首版推荐支持 Agent 固定契约组，并区分可选和严格两种模式：

```text
Agent 未配置 output_schema
  → output_type=[str, DeferredToolRequests]

Agent 配置 output_contract，mode=optional
  → output_type=[str, ToolOutput(...), DeferredToolRequests]

Agent 配置 output_contract，mode=strict
  → output_type=[ToolOutput(...), DeferredToolRequests]
```

可选模式适合同时承担自由问答和格式化成果的业务 Agent；严格模式适合分类、审批材料提取和
固定 API 响应。完全自由聊天的 Agent 可以不配置输出契约。

后续可增加：

- 请求级临时 Schema：供工作流/API 调用者明确指定本次返回结构。
- “生成结构化成果”动作：多轮聊天后由用户显式触发，不自动猜测会话是否完成。

优先级建议为：

```text
请求级显式契约
  > Agent 固定契约
  > 普通文本输出
```

请求级接口只接收 JSON Schema、模式和必要元数据，不允许客户端提交工具代码、URL 或任意执行
逻辑。Output Tool 名称、描述、校验和终止行为全部由 DeepAgent 固定实现。

### 10.2 DeepAgent 改造面

不修改 Pydantic AI 源码，但需要调整应用层：

1. Agent 配置和 Schema：保存可选输出契约，校验大小、深度、字段数和保留名称。
2. Runner：将当前固定 `[str, DeferredToolRequests]` 改为请求级动态 `output_type`。
3. 校验：固定结构用 Pydantic Model；动态 Schema 使用 `StructuredDict` 时再执行 `jsonschema` 校验。
4. SSE：`final.output` 支持 JSON 原始类型，并增加稳定的 `output_type` 标识。
5. 历史：保留原始 ModelResponse 中的 Output Tool Call，并能重建最终结构化结果。
6. Console：终止型 Output 不显示为普通工具卡片；按 Schema 或通用 JSON 只读组件展示。
7. Deferred：保留当前 Pending、恢复、取消和 UI Tool 逻辑，不让 Output Tool进入该分支。
8. Usage：Output Tool 调用纳入正常模型请求和输出 Token 统计，不额外伪造工具执行 Usage。

是否为 Agent 增加独立数据库字段、复用配置 JSON，需在实施计划中结合检索、版本化和迁移要求
单独决策，本文不提前锁定 DDL。

### 10.3 0805 实验实现的归档结论

`feat-ruge-0805@9f528b8` 曾在旧基线上完整实现一次固定输出契约。该分支不合并代码，但以下
结果是后续重新实施的有效证据：

已验证可行：

1. 独立 `Output Contract → 多个 Output Definition` 的建模可以和 ToolPackage 分离。
2. 动态 JSON Schema 可通过 `StructuredDict` 提供给模型，并由 DeepAgent 使用 `jsonschema`
   二次校验和有限 `ModelRetry`。
3. `final.output/output_type`、原始 ModelResponse、历史 `structured_output` 和 Console 通用只读卡片
   可以保持同一份结构化结果。
4. UI Deferred 可以先 Pending，恢复模型后再由 Output Tool 结束本轮；两者不需要合并状态机。
5. Output Call/acknowledgement 可以作为完整配对参与安全切分；契约与 Schema 指纹可以让不兼容的
   Checkpoint 失效，而不改写原始历史。

不应沿用：

1. 实验把 Agent 固定为一个严格契约并移除 `str`，无法覆盖同一 Agent 的普通解释、图表、建议、
   配置等不同终止形态；正式方案必须支持契约组内多种输出，并默认允许普通文本。
2. 实验采用 `end_strategy=early`：模型若在同一响应同时调用普通/UI 工具和 Output Tool，只接受
   Output，其他调用不会执行。它能避免副作用，但也可能让最终结果引用尚未执行的工具，因此正式
   实现必须确定性拒绝这种混合响应并让模型重试，不能静默忽略。
3. 实验的 Alembic `003` 建立在旧迁移链上；当前数据库 head 仍为 `002`。以后若实施，必须重新
   设计新的 migration，不能复制或 stamp 旧 `003`。
4. 实验对直连 DeepSeek 的 thinking/tool_choice 做过特殊处理。当前已升级为 Pydantic AI 官方
   DeepSeek Provider，这条兼容结论只能作为回归用例，不能直接恢复旧适配代码。

因此当前结论是“技术路径已验证，产品配置与混合调用规则未完全锁定，暂缓实施”。删除 0805
分支不会丢失上述证据，也不代表把实验数据库结构认定为当前方案。

## 11. 典型适用案例：DeepCube 助手

DeepCube 助手当前会在完成 MCP 查询和必要的 UI 澄清后，用 Markdown 正文同时返回：

- DeepCube 脚本。
- 运行参数和数据加载范围。
- 元素名称与说明。
- 风险和下一步建议。

其中 `config_fill` 是 Markdown 代码块中的 JSON，Python 前端再执行 `JSON.parse`、归一化和
页面语义校验。这种方式已经表达了结构化输出意图，但仍有明显缺口：

- JSON 引号、截断或 Markdown Fence 错误只能在前端发现。
- 后端不能在同一 Run 内把具体校验错误反馈给模型纠正。
- 历史只能把最终成果恢复成普通 Assistant 文本。
- 配置、脚本和说明的组合依赖 Prompt 约定，缺少稳定结果类型。

因此 DeepCube 是 Output Tool 的典型场景。推荐定义一个 `deepcube.v1` 输出契约组，至少包含：

```text
deepcube_answer：普通解释、能力边界和下一步建议
deepcube_code：脚本、解释、风险和建议动作
deepcube_config：运行参数、数据范围、元素信息和风险
deepcube_mixed：配置、脚本、说明和风险
```

DeepCube 助手默认采用可选模式：普通解释可以继续输出文本，需要交付脚本、配置或组合成果时
调用对应 Output Tool。只有业务前端通过独立“生成严格成果”动作明确要求机器可读结果时，才切换
严格模式并移除 `str`。`deep_cube__clarify` 继续作为 UI Deferred Tool：信息不足时先 Pending，
用户提交后恢复模型，最后再选择普通文本或上述 Output Tool。

Output Tool 只返回待应用成果，不直接修改 Python 页面。填充配置、插入脚本和覆盖脚本仍由业务
前端按钮执行；前端也继续负责当前 Cube 维度、Scope 表达式、中文占位和页面版本等环境语义校验。

实施时必须拒绝“普通 MCP Tool 与 Output Tool 在同一响应中、最终输出却依赖该工具结果”的情况。
Output Tool 从第一次模型请求起可见，并不会自动只在最后一轮出现；提示词、输出校验和
`end_strategy` 测试必须共同保证它只在查询和 Deferred 已闭环后提交。

### 11.1 分仓边界

- DeepAgent 负责 Output Contract、校验与重试、终止语义、SSE `final.output` 和历史恢复。
- `element-python3` 负责业务卡片、页面配置校验以及填充、插入、覆盖等本地动作。
- DeepAgent 不修改 Python 前端，Python 前端也不模拟 DeepAgent 的 Output Tool 和 Agent Loop。

## 12. 混合适用案例：电子表格数据追溯

电子表格来源分析同时包含“权威事实”和“模型综合判断”，不能简单归为完全适用或完全不适用。
公式节点、汇总关系、写入历史、金额、权限遮罩和证据状态来自后端业务服务，继续以 MCP Tool
Result 为事实源；模型根据多类证据判断最可能来源、矛盾和缺口时，可以使用 Output Tool。

```text
电子表格提供当前单元格事实
  → Biz-Go MCP 编排财务模型、公式和维度服务
  → 多个 MCP 返回标准 Evidence Envelope
  → DeepAgent 写入 Run 级 Evidence Ledger
  → 模型综合判断并调用 source_analysis.v1 Output Tool
  → Validator 检查证据覆盖、引用和结论等级
  → 前端按 final.output 渲染 SourceAnalysisCard
```

Output Tool 不复制完整节点图、日志、金额序列和权限数据，只返回对原始证据的引用以及模型观点：

```text
MCP Tool Result：客观证据和权威业务结果
Output Tool：confirmed / inferred / ambiguous / unknown、证据引用、矛盾、缺口和建议
```

第一版不建设跨服务“大证据收集器”，优先采用：

```text
多个只读 MCP 工具
  + DeepAgent 通用 Evidence Ledger
  + source_analysis.v1 Coverage Policy
  + Output Validator
```

输出结构只返回引用 `analysis_id` 的结论、风险和建议，不复制完整节点图：

```json
{
  "analysis_id": "sa_xxx",
  "status": "inferred",
  "conclusion": "该值最可能来自营业收入成员公式",
  "evidence_refs": ["ev_formula_1", "ev_audit_2"],
  "contradictions": [],
  "gaps": ["历史公式版本不可用"],
  "suggested_action_ids": []
}
```

系统提示词负责告诉模型应该查询哪些来源，Coverage Policy 和 Validator 负责确定性兜底。电子表格
侧定义业务适用性规则和 SourceAnalysisCard；DeepAgent 只实现通用编排、输出约束、SSE 与历史，
双方不得跨仓代改。

## 13. 测试要求

实施结构化最终输出时至少覆盖：

1. 未配置 Schema 时仍返回字符串，并兼容现有 Deferred UI Tool。
2. 配置单一 Schema 时只接受 Output Tool，不允许普通文本绕过。
3. 多种结构注册为多个 Output Tool，能返回正确的 `output_type`。
4. 参数校验失败触发有限重试，超限返回明确错误。
5. 动态 JSON Schema 由服务端二次校验，不能只依赖 `StructuredDict`。
6. Output Tool 成功后不再发起模型请求。
7. Output Tool、普通工具和 Deferred 同响应时覆盖不同顺序及 `end_strategy`。
8. 普通请求和 SSE 的完成、Pending、失败语义一致。
9. Output Tool 不进入普通工具卡片或 UI Deferred 渲染分支。
10. 实时响应和历史恢复得到相同结构化结果。
11. Schema 大小、深度、字段数、递归和非法关键字受到限制。
12. Provider 不支持或拒绝 Tool Calling 时返回明确错误，不静默降级为非结构化文本。
13. DeepCube 的 answer、code、config、mixed 四类终止结果均能严格校验并恢复历史。
14. DeepCube 先 Deferred 澄清、恢复后提交 Output Tool；页面应用失败不恢复模型或篡改最终历史。
15. 来源分析只引用 Ledger 中存在的 Evidence，不复制权威 Tool Result；缺证据时有限重试后降级。

## 14. 待决问题

| 问题 | 影响 | 候选方案 | 当前建议 |
| --- | --- | --- | --- |
| 输出 Schema 存储位置 | DDL、查询和版本管理 | Agent 独立字段 / 现有配置 JSON | 实施规划时结合版本化需求决定 |
| 是否首版开放请求级 Schema | API 安全和产品理解 | 仅 Agent 固定 / 同时支持请求覆盖 | 先做 Agent 固定，真实工作流提出需求后再开放 |
| 是否启用 Provider Native Output | 兼容性和 Schema 约束 | 仅 ToolOutput / 按模型能力切换 | 首版仅 ToolOutput |
| 最终结果如何展示 | Console 可用性 | 通用 JSON / Schema 专用卡片 | 首版通用只读展示，业务前端自行渲染 |
| 同响应多工具策略 | 副作用与一致性 | graceful / early / exhaustive | 结合当前 SSE Runner 做专项实验后锁定 |
| 是否接入 Harness OutputGuard | 业务校验和流式风险 | 首版接入 / 后续接入 | 后续按引用、合规等真实需求评估 |
| UI Tool 是否按包分配 | Agent 配置复杂度和历史迁移 | 整包选择 / 逐工具授权 | v0.3.2 采用 Tool Package 整包选择；逐工具授权作为后续独立能力 |
| Output Tool 配置颗粒度 | 是否容易漏配终止路径 | 逐 Output Tool / 输出契约组 | Agent 选择契约组，组内注册多个互斥 Output Tool |

## 15. 参考资料

- [Pydantic AI Outputs](https://ai.pydantic.dev/output/)
- [Pydantic AI Deferred Tools](https://ai.pydantic.dev/deferred-tools/)
- [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)
- [Pi structured output extension](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/structured-output.ts)
- [OpenCode structured output implementation](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts)
- [Deep Agents](https://github.com/langchain-ai/deepagents)
- [Agno](https://github.com/agno-agi/agno)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
