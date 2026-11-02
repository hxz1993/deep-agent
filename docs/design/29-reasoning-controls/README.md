# 模型原生思考配置：能力来源、默认值与接入影响

> C09-THINKING · 2026-10-31 整理（10-30～10-31 核验）· **方案评审，未实施**。
>
> 产品基线 v0.15.0，Pydantic AI Slim 2.46.0、Harness 0.32.0。本次仅整合设计，未增加字段、迁移、接口、控件、依赖或运行时适配。

## 1. 建议形态

先选 Provider／模型，再显示该接入路径真正支持的原生控制，不提供统一五档再转换值。

- 模型维护能力、来源、供应商默认说明，以及可选的产品默认；初始为“跟随供应商”。
- Agent 默认思考配置与默认模型一起选择，初始继承模型默认。
- 会话选择当前模型后，再选思考方式；发送时解析为本轮设置。
- Provider 负责协议，不再加一套对其所有模型生效的默认强度。
- 没有 effort 不代表不思考：固定思考模型只能显示“供应商默认／始终思考”，不能提供无效关闭按钮。

9 月旧讨论曾建议“模型不保存默认，只做请求级设置”。本轮用户重新提出按模型表达能力与默认，该旧建议不再作为当前设计约束；实施仍需另行确认。

## 2. 默认值的含义

| **选项** | **含义** |
| --- | --- |
| 继承 | 取适用的模型默认、Agent 默认或会话选择 |
| 跟随供应商 | 清除本功能添加的开关、effort、budget，实际不传这些字段 |
| 明确档位 | 发送当前模型支持的原生值，例如 Qwen low、Kimi K3 max |

“供应商默认 xhigh”是说明，不等于发送 xhigh。Qwen 不传与显式 xhigh 对应的预算甚至可能不同。Pydantic AI 的 thinking=True 也不等于省略参数。

Codex model/list 的 defaultReasoningEffort 是客户端建议，不是 API 缺省值。本机 Codex Sol 推荐 low，而官方 API 默认 medium；二者分开保存／展示，不能互相覆盖。

## 3. 能否自动取得档位

| **来源** | **已确认能取得** | **缺口** |
| --- | --- | --- |
| 供应商 /models | DeepSeek 返回 effort.supported_levels/default_level；Kimi K3 返回 reasoning.effort.valid/default | 不是所有供应商都有；Step 模型列表只声明支持 reason，没有列档位 |
| models.dev | reasoning_options：toggle、effort.values、budget_tokens.min/max；按 Provider＋模型区分 | 没有覆盖所有型号的独立供应商默认字段，也不是完整请求适配规范 |
| Pydantic AI Profile | supports_thinking、thinking_always_enabled 和部分协议特征 | 当前安装版不是完整的逐型号档位及默认目录 |
| 官方文档、管理员修正 | 原生字段、允许值、默认、互斥条件与私有网关契约 | 需要来源和核验时间；目录刷新不覆盖人工修正 |

以 **Provider 实例／实际服务＋模型编码＋协议** 定位能力。同名 Kimi、DeepSeek 经原厂、百炼、OpenCode、OpenRouter 接入时可能不同。

模型列表、目录与文档冲突时标为待核验，不用最后一次抓取覆盖全部。缺失能力表示未知；reasoning=true 且 reasoning_options=[] 表示目录没有可调控制，不等于不思考。

本仓 models/catalog.py 已保留 reasoning_options。复用现有目录刷新，在配置阶段补能力；不在每轮 Run 拉 models.dev 或探测供应商。能力未知不补成 medium，也不能通过一次简单回答猜默认。

## 4. 各框架做法

核对本地源码与官方资料，不代表执行过全部付费模型矩阵。

| **项目** | **档位定义／发现** | **默认与使用方式** | **借鉴点** |
| --- | --- | --- | --- |
| Pydantic AI Core（安装版） | 统一 thinking 为布尔或 minimal/low/medium/high/xhigh；另有供应商原生设置 | Model → Agent → Run 设置逐层合并；原生字段优先于统一 thinking | 复用原生请求入口，UI 不照搬统一五档 |
| Pydantic AI Harness／CLAI2（54a31ab） | /model settings 按模型提供控制；/effort 复用原生字段及 choices，并校验 | 按模型保存，支持 reset；部分能力由型号规则维护 | 官方应用层也需要单独的模型能力解析，不是只有 Core 通用设置 |
| DeepSeek Harness（477b4f4205） | 核心 effort 是适配器拥有的字符串；pi-ai 用 reasoning、thinkingLevelMap 过滤可选项 | Provider／模型／effort 成组选择，支持 Agent 默认和会话选择；非法显式值拒绝 | 借鉴适配器提供能力；其 pi-ai off 可能只是省略参数，不可视为真实关闭 |
| Codex | model/list 返回 supportedReasoningEfforts 和 defaultReasoningEffort | 目录可随账号／客户端变化；配置默认、会话和轮次选择 | 最符合先模型后档位；Ultra 含产品编排行为，不是通用 API effort |
| OpenCode（014614d） | 优先 models.dev.reasoning_options → 模型 variants；缺失时回退型号／SDK 规则 | 模型 options／variants，Agent 可覆盖，界面切换 variant | 原生枚举＋字段格式适配；预算 variant 是产品定义，不必等同原生档位 |
| Hermes（345cd2b0） | 内部宽梯度，多数入口先提供通用选择，再按供应商集合 clamp／转换 | 全局、模型覆盖、单次运行；缺省处理存在路径差异 | 并非严格原生档位方案，不照搬宽梯度或静默近似映射 |
| OpenClaw | 统一梯度＋Provider policy＋模型能力／映射 | 消息、会话、Agent、模型、全局等多层默认；fallback 可能回填 medium | 借鉴能力过滤，不照搬复杂继承和默认回填 |

OpenCode 本体是 Agent 客户端／Harness；Zen、Go 是其模型服务入口。opencode/<model> 与 opencode-go/<model> 仍需区分模型和协议，不存在所有模型共用的一种“OpenCode 思考协议”。

本次还发现 Hermes 的 Kimi K3 默认 high 注释与原厂不符：官方文档和真实 /models 都返回 max。第三方框架的映射只能作参考。

## 5. 我们已配置／试用的模型

口径为 2026-10-30 本地配置、此前 Alpha 试用、官方文档、实时 models.dev、供应商 /models。本轮只做模型元数据 GET，未重新执行这些型号的生成请求；前一轮只对 Qwen 做过档位实验。

| **模型／接入路径** | **原生档位／开关** | **完全不传控制参数时** |
| --- | --- | --- |
| Qwen3.8 Flash／百炼 | low、medium、xhigh；可关闭；budget 与 effort 互斥 | 开启，xhigh |
| Qwen3.8 Max／百炼 | low、medium、xhigh；可关闭；budget 与 effort 互斥 | 开启，xhigh |
| Qwen3.8 Omni Flash／百炼（此前配置） | low、medium、xhigh；none 关闭；兼容别名不重复展示 | 开启，xhigh |
| deepseek-flash／原厂 | low、high、max；可关闭 | 开启，high；真实 /models 当前说明别名为 V4.1 Flash |
| deepseek-v4-pro／原厂 | low、high、max；可关闭 | 开启，high；真实 /models 已确认 |
| deepseek-v4-flash-0731／百炼 | low、high、max；可关闭 | 开启，high |
| GLM-5.2／百炼 | 有效深度 high、max；可关闭；兼容输入会归并，不重复列档 | 开启；具体 effort 默认未取得无歧义结论，不填 medium 或 max |
| Kimi K2.6／Moonshot | 只有开关，无 effort | 开启 |
| Kimi K2.7 Code Highspeed／Moonshot | 固定思考、不能关闭，无 effort | 始终思考；Highspeed 表示服务速度，不是思考档位 |
| Kimi K3／Moonshot | low、high、max；不能关闭 | max；官方文档与真实 /models 一致 |
| Kimi K3／百炼 kimi-k3 | low、high、max；该直部署 Chat 参考说明允许关闭 | max；不等于转售的 kimi/kimi-k3，后者只支持 max。百炼不同页面开关描述仍有差异，实际入口须验收 |
| MiniMax-M2.7／MiniMax | 固定思考，无公开可调 effort，不能有效关闭 | 始终思考，不标成 high 或 max |
| MiniMax-M3.1-Flash／此前 Alpha 试用 | low、medium、high、xhigh、max；不能关闭 | max |
| GPT-6.1 Sol／OpenAI 官方 API | low、medium、high、xhigh、max；无 none/minimal | medium |
| GPT-6.1 Terra／OpenAI 官方 API | none、low、medium、high、xhigh、max | medium |
| GPT-6 Luna／OpenAI 官方 API | none、low、medium、high、xhigh、max | medium |
| 同名 GPT／ChatGPT Codex 登录路径 | 使用该账号实际模型目录，不照搬 API 枚举；Ultra 不透传 API | 本机目录推荐 Sol low、Terra low、Luna medium；这不是已验证的上游缺省值 |
| Step-5-Preview／StepFun | low、medium、high | 档位默认尚未核实；模型列表仅声明支持 reason |
| OpenRouter Free | 实际模型动态选择，没有固定通用档位 | 由当次路由和上游决定 |
| 自定义 OpenAI 兼容 GPT 网关 | 取该网关契约与实际验收结果 | 未核实前为未知，不按同名套官方默认 |

百炼 Qwen 接受 high/max 但归并为 xhigh，这些不应做成独立档位。GLM 同理。供应商“兼容接受一个值”与“真正有一个独立强度”是两回事。

## 6. 运行链路与影响面

```text
原厂模型发现＋models.dev＋官方文档／管理员修正
→ 当前 Provider＋模型＋协议的能力
→ 模型默认 → Agent 默认 → 会话／本轮选择
→ 本轮解析、校验一次
→ Pydantic AI 原生 ModelSettings
→ 模型请求、工具循环、PTC 后续请求及 Deferred 接续
```

| **层面** | **必要变化** | **关键边界** |
| --- | --- | --- |
| Provider／协议 | 集中选择原生参数格式 | 不按域名猜 Provider，不默认所有接口都是 OpenAI Chat |
| 模型目录 | 复用 reasoning_options，补能力来源及供应商默认说明 | 不在 Run 内刷新，不覆盖人工修正 |
| 模型配置 | 能力、只读供应商默认、可选产品默认 | 相同模型不同接入路径分别配置 |
| Agent | 默认模型旁配置默认思考方式 | 请求换成其他模型时，不携带原模型专属 effort；复制安装导出须覆盖新配置 |
| HTTP／SSE／Console／渠道 | 可选参数传同一种意图，控件随模型变化 | 旧客户端不传有效；继承与跟随供应商分开 |
| AgentSpec／Runner | 集中解析并转换 ModelSettings | 不修改各工具实现，不靠提示词让模型少想 |
| Deferred／历史／诊断 | 小型本轮实际选择与来源记录；接续保持本轮设置 | 不保存完整能力快照，不回填旧事件，不把选择值当实报消耗 |
| 标题／压缩辅助请求 | 分别确定是否继承主请求强度，区分用途 | 不能无意把所有辅助请求提升到高档 |
| 安装／备份／回归 | 模型与 Agent 配置保存、导入导出和旧包兼容 | 不改变 MCP 权限、PTC／Python 预算或结构化输出 |

建议选择优先级：本轮显式选择 → 适用的会话选择 → Agent 默认 → 模型默认 → 供应商行为。每层可显式选择跟随供应商以停止继承；不新增 Provider／全局默认。

切换模型后重新校验，不能把上一模型的 max 静默转成另一模型的 high。Console 对失效选择提示重选，API 对显式非法值返回校验错误。运行开始后固定该轮选择，管理员修改默认只影响下一轮。

## 7. Pydantic AI 复用边界

已有 SDK 可承担实际发送，不需要另写模型执行器：

| **协议例子** | **原生设置** |
| --- | --- |
| Qwen effort | openai_reasoning_effort='low'，原值发送 |
| Qwen 关闭 | extra_body.enable_thinking=false，最终 HTTP 为顶层字段 |
| DeepSeek Chat | extra_body.thinking.type 与原生 reasoning_effort，按该模型契约组合 |
| Kimi K3 | 原生 reasoning_effort；不加 K2 的 thinking 参数 |
| Anthropic | Anthropic 原生 thinking／effort |
| Google | Google 原生 thinkingConfig，按型号选择 level 或 budget |

需要参数格式适配，不需要把用户原生档位换成其他档位。不能同时保留 thinking=low 与 openai_reasoning_effort=max，后者可能覆盖前者。跟随供应商必须清除本功能在设置层加入的原生参数，而不只是清空通用字段。

安装版通用 thinking 为五个字符串；最新上游已扩 max 并调整不可关闭模型行为，不能当作 2.46.0 的保证。安装版原生 OpenAI effort 类型已经有 max，部分接入无需升级；其他 Profile 必须逐项捕获真实请求，不能因支持 extra_body 就宣布所有型号可用。

另有独立协议影响：本仓普通 OpenAI Provider 当前走 OpenAIChatModel。官方 GPT-6.1 Sol／Terra 的工具调用要求 Responses，Luna 的 Chat 工具支持也有限制；自定义网关可能另外适配。因此 effort 不是一个参数就能抹平所有协议。是否增加显式 Responses 接入应单独评估，不借本功能自动迁移所有模型。

## 8. 存储、历史和首版边界

模型与 Agent 当前没有完整的思考配置字段。如果要长期保存模型默认、人工能力修正和 Agent 默认，候选是在 LlmModel、Agent 各加可空 JSON 配置，既有事件只记录少量本轮选择；不另建运行配置表，不塞 global_settings。具体字段、迁移版本和安装备份结构须独立评审批准，本轮不实施 DDL。

- 旧模型、Agent 没配置：不传思考控制，不回填默认记录。
- 历史没有设置：显示未记录，不用今天的默认冒充过去的选择。
- 新轮内部工具循环、PTC 以及 Deferred 续跑使用同一选择；保存少量已解析意图即可，不存完整模型配置快照。
- 目录更新导致显式档位失效：提示并拒绝该组合，不做近似转换。
- 强度、思考过程展示、输出上限、超时、PTC 内存和执行预算分别处理。

首版建议只开放供应商默认、真实关闭（仅支持时）和原生 effort；预算保留元数据，先不做自由配置。是否同时上模型默认、Agent 默认及会话持久偏好，进入实施前再收口，避免一次增加过多配置层。

回归至少覆盖：未知／固定思考／只有开关／有档位；同型号跨 Provider；默认继承与清除；模型切换；HTTP／SSE／渠道／Deferred；工具、PTC、结构化输出；安装备份；原生请求捕获；多次配对质量和延迟。Provider 400、MCP 失败、取消分别统计。

前轮 Qwen 本地实验只证明参数可传入且任务可完成。固定数据答复生成：Flash 默认／Low／Medium 为 15.7／11.2／12.8 秒，Max 为 21.9／27.7／26.1 秒；每格一次，不能据此更改全局默认。完整 Agent 的两个默认案例遇到 MCP 500，未纳入成功任务速度比较。

## 9. 来源与实现入口

- [models.dev Schema](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema.ts)、[实时目录](https://models.dev/api.json)。
- [Pydantic AI Thinking](https://ai.pydantic.dev/capabilities/thinking/)、[Model settings](https://ai.pydantic.dev/agent/#model-run-settings)；本地安装源码与上游分别核验。
- [DeepSeek 模型发现](https://api-docs.deepseek.com/api/list-models/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。
- [Kimi 参数表](https://platform.kimi.ai/docs/api/models-overview)：K3 默认 max、K2.6 开关、K2.7 Code 固定思考；本次 K3 /models 回读一致。
- [QwenCloud Chat](https://docs.qwencloud.com/api-reference/chat/openai-chat)：原生值、兼容归并、默认和预算互斥。
- [MiniMax API](https://platform.minimax.io/docs/api-reference/text-openai-api)：M3.1 Flash 五档、默认 max；M2.x 固定思考。
- [Step 5 调参说明](https://platform.stepfun.com/cookbook/step-5-reasoning)：low/medium/high；默认尚未查实。
- [OpenAI 模型](https://developers.openai.com/api/docs/guides/latest-model)、[Codex model/list](https://learn.chatgpt.com/docs/app-server#list-models-modellist)：API 默认与客户端建议分开。
- [OpenCode 模型配置](https://docs.opencode.ai/docs/models/)，本地 014614d 的 provider/transform.ts、provider.ts。
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f4205/packages/llm/llm-pi-ai/src/models.ts)，同目录 adapter.ts 和 core/agent-default-model。
- [Hermes 适配规则](https://github.com/NousResearch/hermes-agent/blob/345cd2b0/agent/reasoning_effort.py)，同提交 main_provider_setup.py；[OpenClaw](https://docs.openclaw.ai/tools/thinking)。
- 本仓：models/provider_registry.py、models/catalog.py、api/v1/deps.py 的 _model_settings_for_spec、agent/spec.py、agent/runner.py、schemas/chat.py，以及模型／Agent CRUD、安装备份和 Console。
