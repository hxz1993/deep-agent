# 会话思考强度与模型能力范围

> 课题：`C09-THINKING` · 整理日期：2026-09-25 · 状态：有潜力，暂不实施。
>
> 唯一研发台账见 [TODO](../TODO.md)。本文保存研究与讨论边界，不表示功能已实现，也不是已批准的实施方案。

## 当前决定

用户确认先不做。现有供应商默认思考行为已满足当前业务；供应商与模型持续更新，维护开关、档位、预算和协议差异的投入暂时不值得。此课题移出近期接入顺序，不预埋数据库字段、API 参数、Console 控件或运行时适配，不因 SDK 升级自动启动开发。

后续出现明确的效果、响应时间或成本调节需求，并由用户明确恢复课题后，再核验届时的 SDK 与模型能力。不能直接按本文的型号表实施。

## 已确认的产品边界

- 模型配置不保存默认思考强度。若未来实施，可保存模型支持的控制类型、可选档位、能否关闭或预算范围；这与默认值是两回事。
- 实际选择放在 Console 聊天中，接口参数可选。旧电子表格助手等客户端不传时，保持现有供应商默认行为；默认不等于中档，也不等于 SDK 的 `thinking=True`。
- 不因参数缺省而继承模型配置或上一轮显式强度。同一轮内部工具循环及 Deferred 续跑应保持原选择；实现时需按本仓真实 Turn/Run 边界核验。
- Event 可留底显式请求的思考设置，没传就不补值；不把该值称为实际思考消耗或模型最终执行档位。
- 是否采用模型动态档位、预算高级设置等仍属候选方向，未锁定最终界面和 API Schema。

## 研究快照：为何不能只接统一五档

核验基线为本地 Pydantic AI 2.46.0、当前 DeepAgent `provider_registry.py`，以及 2026-09-25 查阅的官方文档；没有对所有模型进行真实付费请求验证。

Pydantic AI 的统一 `thinking` 支持 `minimal/low/medium/high/xhigh` 与布尔值，依赖本地 Model Profile 和模型适配器转换，不会逐次请求 models.dev。不同模型可合并档位、转预算或忽略不支持的控制，不能等同于所有供应商完整支持五档。

| 供应商／模型 | 当时核验的差异 |
| --- | --- |
| DeepSeek | 官方当前原生 effort 为 low/high/max；服务端把 minimal/low 映射 low，medium/high/xhigh 映射 high，max/ultra 映射 max。统一 SDK 五档不含 max；Chat 接口关闭需原生 thinking.type，且 SDK 型号识别未覆盖所有新别名 |
| Kimi K2.6 | 官方支持思考开关，不支持 reasoning_effort |
| Kimi K2.7 Code | 官方始终思考，不能关闭，也不支持 reasoning_effort |
| Kimi K3 | 官方支持 low/high/max，默认 max；本地 SDK 的通用 Kimi 规则不能直接视为这些型号的准确协议 |
| OpenAI／Anthropic／Google | 已有原生 SDK 映射，但具体型号允许值不同；旧 Claude、Gemini 还涉及 Token 预算 |
| Alibaba／Qwen | 当前 SDK Profile 未完整接通统一 thinking；原生服务有 enable_thinking、thinking_budget 等参数，需按型号和接入平台核验 |
| Z.AI／OpenRouter | SDK 有专用 Model 转换，但本仓当时通过通用 OpenAIChatModel 装配，不能仅凭 Provider 类存在就认定已使用专用转换 |
| 自定义 OpenAI 兼容 | 协议兼容不保证思考参数兼容；需明确参数格式及允许值 |

DeepSeek Harness 的通用 pi-ai 适配器继承各模型目录能力，并允许自定义“可选档位 → 请求值”，不会把 DeepSeek 档位强加给其他模型。Codex 也由模型元数据提供可选档位，但其会话继承和默认值回填行为不应直接照搬到本项目。

## models.dev 与候选复用方向

当日核验的官方 Schema 和实时 API 已有 `reasoning_options`：

- `toggle`：可开关。
- `effort`：明确的 values 列表。
- `budget_tokens`：可选 min/max，可能不完整。

字段缺失表示信息不足；显式空数组表示无调用方可调控制，不等于模型不思考。目录不提供完整的请求参数映射，也没有统一独立的供应商默认强度字段。目录、源码与供应商协议可能更新不同步，不能把目录声明当作真实调用验收。

本仓 `src/deep_agent/models/catalog.py` 已保留 `reasoning_options`，恢复时优先复用现有目录缓存。候选方案是：目录预填能力、模型配置允许人工修正并保留来源、后端集中适配请求格式、聊天按有效能力展示选项；目录刷新不覆盖人工修正。能力应结合供应商接入路径和模型判断，同名模型经过不同网关可能采用不同参数。

此方案尚未实施。恢复时先验证 DeepSeek、Kimi 及常用实际型号，再决定最小范围，不预先建设全供应商完整映射表。

## 参考入口

- [Pydantic AI Thinking](https://pydantic.dev/docs/ai/capabilities/thinking/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [Kimi 模型参数](https://platform.kimi.ai/docs/api/models-overview)
- [models.dev Schema](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema.ts) 与 [目录 API](https://models.dev/api.json)
- [Provider 与模型分层](../19-model-provider-architecture/README.md)、[模型目录](../04-model-catalog/README.md)、[框架能力基线](../14-framework-capabilities/README.md)
