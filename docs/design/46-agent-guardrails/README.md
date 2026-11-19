# 46 · Agent 运行护栏

> 2026-11-20。渠道 UI 工具的现有提示已核对。新增护栏待评审，本次记录产品方向和框架依据。

## 产品边界

护栏是后台在固定环节执行的检查。规则来自平台权限、应用范围、工具的实际操作和当前入口的交互能力。提示词可以引导模型选工具，服务端根据可信规则决定实际能执行什么，下游业务服务继续做鉴权。

共用规则放在运行层，由各 Agent 使用。只有管理员确实要作业务选择时，才考虑显示具体选项，例如「预算只读」或「允许修改，执行前须确认」。渠道 Agent 由管理员配置和联调，误触发浏览器交互属于运行兜底。

## 检查点和结果

| 检查点 | 检查内容 | 例子 |
| --- | --- | --- |
| 输入 | 准备交给模型的用户输入 | 在本地识别并遮掉密钥，再把剩余请求交给模型。按规则阻断时，直接回复用户。 |
| 工具执行前 | 本次操作、参数、身份和范围 | 模型请求更新预算。后台查到只读权限，跳过修改，将理由作为本次工具结果交回模型。模型可在同一轮改为查询或说明原因。若权限允许修改且要求确认，则在执行前暂停，等有效确认后继续。 |
| 工具执行后 | 即将交回模型的工具结果 | 清理工具结果里的凭据。涉及写入、发送等动作时，执行前必须完成相应权限或审批检查。 |
| 最终输出 | 即将发给用户或调用方的最终回答 | 拦截、脱敏或按运行模式要求模型重写。重写会增加一次模型请求，仍属于原用户这一轮。 |
| 流式输出 | 即将发送的文本等流式片段 | 先缓冲一个完整片段，检查后放行、替换或阻断。已发出的片段无法撤回。 |

语义判断可以调用辅助模型。辅助模型也能看到传给它的内容，因此先在可信服务内做确定性检查和脱敏，再按需要把脱敏内容交给语义检查。任意字符串无法可靠识别为密码。要求模型完全接触不到凭据时，用专用凭据配置和服务端执行，模型只需要知道要使用的连接及执行结果。

一次拒绝、脱敏或重写可以出现在运行诊断里。是否进入会话事件取决于产品记录规则。当前结构化输出已有对应先例。失败尝试可在诊断轨迹里查看，普通聊天展示通过校验的结果。若以后增加敏感信息护栏，诊断只需保留检查位置、规则、判定、脱敏摘要和关联标识。 [现有结构化输出记录](../../guides/structured-output.md#客户端协议)

## 渠道 UI 工具的兜底

**当前工作区。** 同一个 Agent 按配置装配 MCP、Skill 和工具。消息渠道触发已配置的浏览器 UI 工具后，服务端留下待回传的调用；渠道回复「此操作需要在 DeepAgent 控制台继续完成」。集成用例覆盖待答状态与提示发出。浏览器可以在具备该业务交互的入口显示按钮，并把用户结果按原调用交回。 [UI 工具协议](../../guides/ui-tools-external.md) · [现有兜底用例](../../../tests/integration/test_channel_message_persistence.py#L256)

**后续候选。** 在把实际 UI 调用交给客户端、创建等待前，由服务端检查本次入口能否显示交互并回传结果。

- 有承载能力时沿用现有 UI 调用和结果校验。
- 缺少承载能力时，给模型一个明确的工具结果，说明可到哪个受支持的入口完成。由模型回复原渠道或继续能做的步骤。这条路径无需创建待确认项，也无需给渠道新增协议或过滤整包工具。
- 执行需用户批准的动作时，继续核对原调用和原审批协议。普通渠道消息里的「同意」按聊天文字处理。

这项兜底独立于 [30 · UI 工具同 Run 等待](../30-ui-tool-inline-wait/README.md)。当前继续沿用已核对的提示；是否需要改为等待前返回工具结果，在有实际需求时评审。一轮验收覆盖支持交互的客户端、缺少交互能力的消息渠道、其他工具继续运行、聊天文字无法完成审批，以及诊断事件与真实调用一致。

## 框架依据

| 框架 | 对应机制 |
| --- | --- |
| Pydantic AI Core / Harness | Core 提供生命周期 Hook。仓库锁定的 Harness 0.70.0 含输入、工具、最终输出和流式护栏。Harness 工具护栏包住运行器实际执行的工具，DeepAgent 的浏览器 UI 工具交给客户端回传，需要在这次交接处判断。Harness 最终输出护栏的重试在官方 `run_stream()` 中不受支持。 [Hooks](https://pydantic.dev/docs/ai/core-concepts/hooks/) · [护栏文档](https://pydantic.dev/docs/ai/harness/guardrails/) · [锁定版本](https://pypi.org/project/pydantic-ai-harness/0.70.0/) |
| OpenAI Agents SDK（Python） | 输入／输出护栏可以终止运行。函数工具护栏可跳过单次调用并给模型结果。人工审批有独立的暂停与恢复。 [护栏](https://openai.github.io/openai-agents-python/guardrails/) · [人工审批](https://openai.github.io/openai-agents-python/human_in_the_loop/) |
| Google ADK / Claude Agent SDK | ADK 在模型或工具回调中检查，也可跳过工具并返回替代结果。Claude Agent SDK 在权限规则和执行前 Hook 中处理工具授权。 [ADK 回调](https://adk.dev/callbacks/types-of-callbacks/#before-tool-callback) · [Claude 权限](https://code.claude.com/docs/en/agent-sdk/permissions) |
| LangChain / NVIDIA NeMo | LangChain 通过中间件检查模型与工具调用。NeMo 另有检索和多轮对话的护栏。 [LangChain 中间件](https://docs.langchain.com/oss/python/langchain/middleware/custom) · [NeMo 护栏分类](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types) |

通用工具的风险元数据和执行前后检查见 [05 · 工具优化](../05-tool-optimization/tool-optimization-design.md#94-guardrails)。跨课题排期见 [研发台账](../TODO.md#agent-运行护栏2026-11-20)。
