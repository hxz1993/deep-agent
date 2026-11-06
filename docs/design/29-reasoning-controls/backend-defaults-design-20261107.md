# Agent 默认思考配置与模型能力元数据：本轮方案

> 2026-11-07，C09-THINKING。Provider／模型不配置产品默认档位，默认只在 Agent 配置；本轮已按最新要求制作 HTML 交互原型，尚未开发产品功能。
> 只读基线 test / 77e4179c / 0.15.14，数据库013；Pydantic AI 2.46.0、Harness 0.32.0。未修改产品代码、数据库或本地配置。

## 1. 配置层级

| 位置 | 职责 | 配置默认强度 |
| --- | --- | --- |
| Provider 适配器 | 该接入协议的原生字段及发送方式 | 否；不能给所有模型统一设档位 |
| 模型目录／已配置模型 | 能力元数据、原生可选项、已知供应商缺省及来源 | 否；默认信息只读，未知留空 |
| Agent | 默认模型旁选择默认思考方式，初始供应商默认 | 是 |
| 当前请求／Console | 可选覆盖本轮，不回写 Agent | 仅本轮 |
| 电子表格等客户端 | 可只选模型，不发思考字段时继承适用默认 | 不强制适配 |

优先级：**本轮指定 → 适用的 Agent 默认 → 实际模型的供应商默认**。“模型默认”只指不传参数时供应商的行为，不再增加可编辑的模型产品默认层。

- Agent 与本轮都未配置：本功能不追加 effort、开关或预算，沿用既有 Provider／SDK 请求路径。
- 请求省略 reasoning 或为 null：继承 Agent；显式 provider_default 跳过 Agent 默认，不追加本功能的控制。
- 请求改用其他模型：不携带原模型专属强度，采用新模型供应商默认，除非本轮另行明确指定有效值。
- 保存影响后续新轮；工具循环和 Deferred 接续维持本轮已经解析的选择。不做会话长期偏好。
- 已知“供应商默认 xhigh”只用于提示，不自动转成显式 xhigh。省略与显式发送的预算可能不同。
- 所有模型都保留“供应商默认”，其语义固定为不传本功能的思考控制参数；不根据已知缺省值自动补传。没有可调档位、不支持思考和目录未知均可按默认运行。最新原型要求增加简单的手工原生值数组编辑，不采用逐项添加或复杂能力编辑器。

## 2. AI Labs 框架复核

本轮读取本地源码并交叉核对官方资料；没有更新学习仓库或升级产品依赖。

| 框架／本地版本 | 配置位置与原理 | 能力和默认的来源 |
| --- | --- | --- |
| Google ADK 2.13.0 / d68ca857d5e3 | Agent 的 generate_content_config.thinking_config；BuiltInPlanner 也可提供 ThinkingConfig 并覆盖请求值 | Gemini 按型号用 level 或 budget；原生 Anthropic 用专用 effort；LiteLLM 路径另有转换，不是全厂商完整档位目录 |
| OpenAI Agents Python 0.24.0 / 17d994bd3045 | Agent.model_settings.reasoning；RunConfig.model_settings 可覆盖 | 原生 effort 按模型／API 支持；SDK 对部分 GPT 型号另注入隐式默认，不等于上游不传默认 |
| Pydantic AI 产品安装版 2.46.0 | 通用 thinking 和 Provider 原生设置并存，原生优先 | 通用入口是布尔＋五个字符串，再作映射；Profile 维护部分模型特征，不是完整档位数据库 |
| Pydantic AI 学习版 2.57.0 / 54a31abfb7bb | Core＋Harness／CLAI2 模型设置 | CLAI2 控件由 Profile／型号规则生成；目录组合 genai-prices、已知型号和指定供应商发现；在线文档可能更新于该快照 |

Pydantic AI 的 OpenRouter 接入不是所有 Provider 的能力来源。安装版按厂商／型号选 Profile，并使用 OpenRouter 的 reasoning 协议；学习版 CLAI2 的 models/openrouter.py 查询 /models，但当前只提取模型 ID，并未读取完整 effort／供应商缺省。models/model_catalog.py 首个来源为 genai-prices，models.dev 在注释中作为可扩展来源，不是已经接通的通用能力服务。

我们采用统一的意图结构，保留不同模型的原生枚举，再按协议翻译字段。不把 Qwen xhigh、DeepSeek max、Gemini high 视为可互换等级。安装版通用 thinking 的近似映射不用于生成产品选项；最新文档增加 max 等行为不能直接当作安装版保证。

### pi-ai 最新发布版补核

2026-11-07只读核对npm latest及官方Tag：DSH依赖的包名为`@earendil-works/pi-ai`，最新发布版1.5.0。另有旧包名`@mariozechner/pi-ai`（本次latest为0.73.1），不能混用其版本号。

1.5.0仍有thinkingLevelMap，键为off、minimal、low、medium、high、xhigh、max；每个模型映射到自身原生值，null明确不支持。此结论核对了实际npm发布包声明和同Tag的src/providers/model-schema.ts，不只引用旧DSH代码。

新版内置目录的来源为models.dev v2，加OpenRouter、Vercel、NVIDIA、Radius等专用来源；Provider定义和family规则处理纠正、例外及未收录型号。构建时运行catalog/generate.ts生成目录并随包发布；静态目录不是每次调用自动拉新。部分动态Provider有单独refresh能力，也不表示所有静态目录都会实时刷新。

所以新模型更新路径是上游资料／人工规则更新 → 重新生成目录 → 发布包，或由使用者补自定义模型资料。我们直接复用已有models.dev目录刷新即可，不需要为了档位引入pi-ai依赖。

## 3. models.dev 有什么，缺什么

2026-11-07 读取公开 api.json 与 Schema：reasoning_options 支持 toggle、effort.values、budget_tokens.min/max。Schema 总枚举只是数据库允许容纳的值域，**逐模型 values 子集**才是候选档位。

| Provider／型号 | 当前公开目录资料 |
| --- | --- |
| alibaba／qwen3.8-flash、qwen3.8-max | toggle；low／medium／xhigh；另有预算资料 |
| deepseek／deepseek-flash | toggle；low／high／max |
| moonshotai／kimi-k3 | low／high／max，没有 toggle |
| alibaba／kimi-k3 | low／high／max，并列 toggle，与原厂不同 |
| openai／gpt-6.1-sol | low／medium／high／xhigh／max |
| google／gemini-2.5-pro | 只有预算范围128～32768，不应伪造成三档 |

当前产品解析的v1没有“未传参数默认哪档”专用字段。补核实验版v2 Schema后，已确认其`capabilities.reasoning.default`可描述toggle／effort／budget缺省，本次11个Provider同范围154条记录中65条填写；OpenAI、Anthropic未填。values中的default／null也不代表已知其具体对应哪档。reasoning=true不说明默认开启、可关闭或可调档；空options也不能证明始终思考。

已按现有生产过滤完成[逐模型CSV及汇总](Report%20models.dev思考档位核对-20261107/README.md)：135条声明支持思考、85条有档位，近三个月28条接入记录均有档位。v1和v2对这154条的思考声明、档位、开关、预算一致；首版读取档位无需先换解析器，v2可作只读默认资料增强。

缺省信息来自可信供应商模型列表或官方文档，记录来源、核验时间和适用协议；未取得则展示“供应商默认（具体档位未提供）”，不能填关闭或 medium。

### 补查：哪家资料更全，其他 Harness 怎样更新

用户指出：已配置模型当前只有标签，且新模型／自定义网关必须有可维护的档位兜底。这里区分“目录已保存原始资料”和“已配置模型已有可调功能”；后者尚未实现。上文 models.dev 缺的是完整缺省信息，不是缺少逐模型档位。

本次读取 OpenRouter 公开 `/api/v1/models`：457条模型中337条有reasoning对象，215条带supported_efforts和default_effort字段。此为当时目录快照，不是供应商原生兼容率或所有新模型的覆盖率。当前字段包括：

- supported_efforts：网关接受的档位；null表示接受全部网关档位，省略表示不暴露档位选择，二者不能混淆。
- default_effort、default_enabled：该接入路径的缺省信息；不能把启用时建议选项与原厂完全省略参数的效果混为一谈。
- mandatory：是否强制思考；supports_max_tokens：是否支持预算控制。

**OpenRouter的控制字段比models.dev v1完整；models.dev v2也已增加可选默认资料，仍非全量覆盖。models.dev更适合作为跨供应商原生能力目录。没有一份来源可直接覆盖所有原厂、转售、订阅及客户私有协议。**

已见实际差异：OpenRouter 的 qwen/qwen3.8-flash 本次只提供开关／预算资料，没有supported_efforts；models.dev的alibaba/qwen3.8-flash则有low、medium、xhigh。OpenRouter的Kimi K3.1资料包含none，而Kimi原厂文档声明K3系列始终思考。因此不能用OpenRouter的能力覆盖原厂路由；网关还会把部分档位映射至相近原生值。

| 项目／来源 | 更新方式及自定义兜底 |
| --- | --- |
| models.dev | 社区维护的供应商／型号资料，客户端刷新目录；effort.values是逐模型子集，不是所有型号共享全集 |
| OpenRouter | 聚合服务维护其路由能力，通过/models返回；字段适用于OpenRouter请求语义，不能当原厂契约 |
| DeepSeek Harness，本机66491b05e3f0 | 原厂列表在使用时约十分钟刷新，失败用内置；第三方使用已安装pi-ai目录，自定义模型可在UI声明reasoningEfforts和API值，配置下次请求生效 |
| OpenCode，本机学习副本 | 远端模型目录＋本地缓存＋内置快照，源码后台每60分钟刷新；variants及Provider规则控制发送，用户可补自定义variant；所读源码默认目录源为models.opencode.ai |
| Pydantic AI | Core的Profile／参数转换随包版本更新；CLAI2另有目录和型号选项规则，不能靠模型列表自动取得所有原生控制 |

DeepSeek Harness原厂是off、low、high、max；第三方适配器pi-ai可容纳off、minimal、low、medium、high、xhigh、max，再按模型的thinkingLevelMap过滤和映射。自定义声明可增删行，行对应显示档位和API值；这是一层有限统一词汇映射，不是把其他模型全部限制为三档。我们建议保留原生值，不照搬其max→xhigh这类显示改名。

首版覆盖目标建议以最近三个月发布的常用模型为优先，半年内仍使用的型号继续覆盖：OpenAI、Anthropic、Google、Qwen、DeepSeek、Kimi、GLM、MiniMax、StepFun。此为后续验收范围，不表示本轮已经逐一发生成请求。按每个实际渠道列出证据和缺项；别名、API创建时间、服务商上架时间不能直接当作模型发布日期。

## 4. 现有链路及更新方式

当前 models/catalog.py 的 _capabilities() 已保存 reasoning_options；provider_metadata 与目录导出也保留。无需从头再建一个抓取服务。

缺口在后半段：Console 从目录新建／刷新模型仅带名称、型号、窗口、输出、模态和 tags；llm_models 无完整能力字段，模型选择接口也没有原生档位。因此资料已进入目录，尚未贯通至已配置模型和 Agent。

```text
现有目录在线更新／离线导入
→ 按供应商渠道＋模型编码匹配能力
→ 从目录新建模型，或刷新已有模型能力
→ 保存能力元数据
→ Agent／Console 列原生选项
→ 运行时校验，交给 SDK 原生字段
```

- 不在 Run 中拉取目录，不在每次聊天中探测供应商。已有模型显式刷新后更新能力，不改 Agent 已保存选择。
- 匹配需要 Provider 实例、已知目录渠道、型号和协议。同名私有网关／转售不能直接套原厂全部能力。
- 未声明的未知路径使用供应商默认，不发送思考控制；本轮原型加入管理员粘贴数组的能力声明兜底，见下节。能力定义与默认强度配置分开，产品仍未实施。
- 档位资料随目录更新；原生发送规则随 Provider 适配代码更新。上游新值不代表本机 SDK 已能发送，未适配值提示不可用原因，不近似映射。
- 刷新后已有选择失效时要求重新选择；不静默降档，也不在运行中换档。

## 5. 界面及未知模型

| 资料状态 | 展示／发送 |
| --- | --- |
| 明确不支持思考 | 不支持思考调节，不发参数 |
| 有 effort | 供应商默认＋原生 values；已知缺省可旁注 |
| 明确有 toggle | 增加开启／关闭；开启取供应商默认强度 |
| 明确始终思考 | 无关闭；仍可选已有 effort |
| 只有 budget | 保留范围，首版不做预算编辑，不造档位，使用供应商默认 |
| 有思考标签但无可调资料，或未知 | 仅供应商默认，注明可调信息未提供，不推断默认关闭或固定思考 |

模型弹框中英文并列展示原生档位，可通过统一编辑入口粘贴数组或逗号文本；供应商缺省只读，无产品默认下拉。Agent 默认模型在左、默认强度在右；Console 本轮用中文选择，默认继承 Agent。业务客户端可不新增控件。完整交互和差异见[原型说明](Prototype%20思考强度配置/README.md)。

### 简单兜底：自定义模型能力声明

最新要求已将简单手工声明纳入原型。模型配置者可直接粘贴字符串数组或逗号文本，不逐个添加；日常Agent／对话界面仍只使用下拉选项，不要求用户手写字符串。模型不增加默认强度配置。

- 优先复制已知模型的能力模板，再按目标接口核对；也可声明“无调节／仅开关／原生档位”。
- 档位回显为中英文并列值；一个编辑入口支持`["low","medium","high"]`或`low, medium, high`，有序去重并校验字符串数组。允许声明客户原生新值，不强制从固定五档中选择。
- 发送字段仍由已配置Provider／协议及适配器决定，例如OpenAI effort、Qwen enable_thinking＋effort、DeepSeek thinking＋effort、Google level。不新增逐字段请求JSON编辑器。只新增枚举通常是资料更新；新增字段／协议需要代码适配，不能把原型保存成功当成网关兼容证明。
- 声明保存后，Agent／Console获得稳定的枚举列表；只展示该模型已声明选项。不自动为未知型号挂五档，不把未声明的关闭当作省略参数。
- 手工声明优先于目录，标明来源，不被在线刷新覆盖；管理员可恢复目录来源。原生API值原样发送，不将high近似变成xhigh。
- 默认始终是供应商默认（不传）；具体产品默认仍只由Agent选择。未声明能力时仍可正常聊天，采用供应商行为。

因此更新来源建议为：当前路由的明确人工声明／原厂契约 → 对应Provider的目录资料；OpenRouter资料仅自动用于OpenRouter路由，其他路由可作核对参考。目录预览保留英文，模型配置中英文并列，对话中文；实际候选来自该模型能力，不做统一五档。

### 历史配置回显建议

pi会话有thinking_level_change，DeepSeek Harness将reasoningEffort写入request/header，OpenCode保存model.variant。来源与源码位置见原型说明。因此建议沿用现有Run事件增加轻量的本轮选择、设置来源及是否发送参数，不新增会话长期偏好或统计维度；旧记录显示未记录，不按当前默认回填。原型仅展示此建议，事件契约仍待实施计划明确。

## 6. 存储建议：能力与选择分开

上一版 llm_models.reasoning_config.default 建议撤回，模型不持久化产品默认。

- 目录表复用现有 capabilities／provider_metadata，不为 reasoning_options 新增目录列。
- 已配置模型建议一个可空 reasoning_metadata，只保存能力、来源和只读供应商缺省事实。不是可配置默认入口；是否新增列在开发方案中另行说明。
- Agent 建议一个可空 reasoning_config，保存默认模型对应选择；空值表示供应商默认，更换模型需校验／重选。
- 请求可选 reasoning，表示继承或本轮覆盖；不新增会话长期设置。

模型资料示意：

```json
{
  "version": 1,
  "reasoning": true,
  "options": [
    {"type": "toggle"},
    {"type": "effort", "values": ["low", "medium", "xhigh"]}
  ],
  "provider_default": null,
  "source": {"kind": "models_dev", "provider": "alibaba", "model": "qwen3.8-flash"}
}
```

provider_default=null 表示未取得可信缺省资料，不等于关闭；后续补官方缺省保留独立来源。历史模型不回填默认，无元数据按未知处理，可由原刷新入口补齐。

Agent／请求意图示例分别为：{"mode":"effort","effort":"low"}、{"mode":"enabled"}、{"mode":"disabled"}、{"mode":"provider_default"}。不做全局五档 Literal，按实际模型原生选项校验。请求省略表示继承，不与显式 provider_default 混淆。

字段名、格式和迁移未落地。用户的条件性同意不视为本轮开工，实施轮说明实际结构后再执行。数据库保持013，本轮没有新增迁移。

## 7. 运行与回归边界

统一入口解析一次，经真实 ModelSettings 用于普通／流式／工具／PTC；Deferred 保持本轮选择。按实际协议发送，保留千问 Schema、系统消息、视频和结构化输出适配。extra_body 集中合并／清除，避免浅合并丢字段。

Profile 的“默认启用”事实不是强度值，不能凭 supports_thinking 自动发送 enabled。标题／压缩保留现有独立策略，避免主任务高强度误传辅助请求。摘要展示和强度选择仍为独立功能。

回归覆盖：默认不传与基线一致、Agent 默认与本轮覆盖、显式供应商默认、模型切换、各种能力状态、目录刷新、安装备份、SDK实际请求体、Deferred及工具／PTC。真实模型分别对比耗时、用量、结果；不承诺 Low 一定更快或质量完全不变。

## 8. 官方依据及源码入口

- [models.dev Schema](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema.ts)、[公开目录](https://models.dev/api.json)：三类控制和逐型号 values，非完整供应商缺省表。
- [OpenRouter逐模型控制](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#discovering-per-model-reasoning-options)、[公开模型接口](https://openrouter.ai/api/v1/models)：五类reasoning字段及路由映射；[Kimi原厂参数](https://platform.kimi.ai/docs/api/models-overview)用于核对同名路径差异。
- [DeepSeek Harness配置](https://github.com/deepseek-ai/deepseek-harness/blob/66491b05e3f0/docs/user/guide/providers.md)，同提交llm-pi-ai/src/models.ts、catalog.ts及llm-deepseek/src/model-info.ts；[OpenCode自定义variants](https://docs.opencode.ai/docs/models/#custom-variants)。
- [Pydantic AI Thinking](https://pydantic.dev/docs/ai/capabilities/thinking/)：通用映射与原生参数；安装版行为按2.46.0源码核验。
- [pi-ai 1.5.0发布](https://github.com/earendil-works/pi/releases/tag/v1.5.0)、[该Tag目录生成说明](https://github.com/earendil-works/pi/blob/v1.5.0/packages/ai/catalog/README.md)、[该Tag映射定义](https://github.com/earendil-works/pi/blob/v1.5.0/packages/ai/src/providers/model-schema.ts)：来源、生成流程与七类映射键。
- [ADK Agent](https://adk.dev/agents/llm-agents/)、[2.13.0 BuiltInPlanner](https://github.com/google/adk-python/blob/d68ca857d5e3/src/google/adk/planners/built_in_planner.py)，同提交 models/lite_llm.py、anthropic_llm.py。
- [OpenAI Agents 模型设置](https://developers.openai.com/api/docs/guides/agents/models#model-settings-prompts-and-feature-support)、[0.24.0隐式默认](https://github.com/openai/openai-agents-python/blob/17d994bd3045/src/agents/models/default_models.py)，同提交 run_internal/turn_preparation.py。
- Pydantic AI学习版：src/pydantic_clai2/pydantic_clai2/models 下 model_catalog.py、openrouter.py、model_options.py；安装版settings.py、profiles/、models/、providers/openrouter.py。
- 本仓：models/catalog.py、models/provider_registry.py、schemas/llm_model.py、api/v1/deps.py、agent/spec.py、console/configuration/llm-models.js。
