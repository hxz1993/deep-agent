# models.dev 思考档位核对

核验日期：2026-11-07，Asia/Shanghai。只读研究，产品基线 `test / 77e4179c / 0.15.14`；未刷新本地目录数据库、修改配置或发起模型生成请求。

## 结论

现有 Provider 白名单及生产过滤函数保留 **154 条 Provider＋模型记录，对应 152 个 canonical model**。其中 135 条声明支持思考，85 条提供原生档位枚举，48 条列开关，30 条列预算范围。最近三个月发布的 28 条接入记录全部列出了档位。

因此首版可采用“目录原生选项＋供应商默认”。无需先设计统一五档，也无需为了读取档位先切换到 v2。默认始终表示不发送思考控制，元数据不足时仍可正常使用默认。

新增发现：models.dev **v2 有可选的 `capabilities.reasoning.default` 字段**，本次同范围 65 条记录填写了默认值；OpenAI、Anthropic 尚未填写。此前“目录缺少默认字段”的结论只适用于本项目当前读取的 v1。

## 文件与统计口径

- [逐模型明细 CSV](models-detail.csv)：154 行，保留原生枚举、开关、预算、原文、v2 默认、模型类型及来源时间。
- [Provider 汇总 CSV](providers-summary.csv)：11 行，含各类控制数量、近三个月覆盖和完整档位组合。
- 两份 CSV 使用 UTF-8 BOM，便于电子表格软件识别中文。空值表示未提供；预算 0 保留为数字 0，不与空值混用。

过滤直接调用本仓 `normalize_models_dev_bundle()`：11 个 Provider、排除 deprecated、文本输入输出、有效上下文及输出限制。未人为缩减旧型号，也未使用当前已配置模型替代完整模型目录。

“近三个月”按目录 `release_date` 统计 2026-08-07～2026-11-07，包含模型别名，不表示 28 个独立底模，也不以 `last_updated` 代替发布日期。开关、档位和预算可能同时存在，各列不能相加作为总数。

| Provider | 保留记录 | 支持思考 | 有档位 | 有开关 | 有预算 | 思考但未列可调项 | v2 有默认 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Alibaba | 29 | 24 | 6 | 23 | 19 | 0 | 23 |
| Anthropic | 17 | 17 | 13 | 2 | 8 | 0 | 0 |
| DeepSeek | 2 | 2 | 2 | 2 | 0 | 0 | 2 |
| Google | 29 | 25 | 17 | 4 | 3 | 3 | 11 |
| MiniMax 国际 | 2 | 2 | 1 | 1 | 0 | 0 | 2 |
| MiniMax 中国 | 2 | 2 | 1 | 1 | 0 | 0 | 2 |
| Moonshot AI | 6 | 6 | 3 | 1 | 0 | 2 | 4 |
| OpenAI | 38 | 29 | 29 | 0 | 0 | 0 | 0 |
| StepFun | 4 | 4 | 4 | 0 | 0 | 0 | 0 |
| xAI | 8 | 7 | 5 | 0 | 0 | 2 | 4 |
| Zhipu AI | 17 | 17 | 4 | 14 | 0 | 0 | 17 |
| 合计 | 154 | 135 | 85 | 48 | 30 | 7 | 65 |

现有过滤还保留了 12 条 v2 标为非普通聊天的记录：5 个 embedding、3 个 image、2 个 realtime、2 个 audio。按 v2 `type=chat` 单列时是 **142 条，130 条支持思考、80 条有档位**。这说明当前“文本输入输出”过滤并不等价于“普通聊天类型”；本轮按现有代码口径完整展示，并在明细增加 v2 类型，未修改产品过滤。

## 典型型号与枚举

| 接入路径／型号 | 原生档位 | 其他控制 | v2 默认描述 |
| --- | --- | --- | --- |
| Alibaba / qwen3.8-flash | low, medium, xhigh | 开关；预算最高 262144 | effort=xhigh |
| Alibaba / qwen3.8-max | low, medium, xhigh | 开关；预算 0～262144 | effort=xhigh |
| Alibaba / qwen3.8-omni-flash | none, low, medium, xhigh | 未另列 toggle | effort=xhigh |
| DeepSeek / deepseek-flash | low, high, max | 开关 | effort=high |
| Moonshot / kimi-k3 | low, high, max | 未列开关 | effort=max |
| Alibaba / kimi-k3 | low, high, max | 开关，与原厂路径不同 | effort=max |
| OpenAI / gpt-6.1-sol | low, medium, high, xhigh, max | 未列开关 | 未提供 |
| OpenAI / gpt-6.1-terra | none, low, medium, high, xhigh, max | 未另列 toggle | 未提供 |
| MiniMax / MiniMax-M3.1-Flash | low, medium, high, xhigh, max | 未列开关 | effort=max |
| Google / gemini-2.5-pro | 无 effort 枚举 | 只有预算 128～32768 | 未提供 |

不能把这些档位并成通用五档：有的缺 medium，有的缺 high，有的支持 none，有的只有预算。“none”是原生值，“供应商默认”是本产品省略参数的意图，两者分开。`toggle` 的缺失不能用“effort 没有 none”补成一个关闭选项。

7 条声明支持思考但未列可调项：Google 的两个 deep-research preview 及 gemini-2.5-computer-use-preview-10-2025；Moonshot 的 kimi-k2.7-code、kimi-k2.7-code-highspeed；xAI 的 grok-4.20-0309-reasoning、grok-build-0.1。空选项只表示目录未列控制，不能据此确认它们全部为固定思考。

## v1、v2 和 pi-ai

| 内容 | 本项目当前 v1 | 实验版 v2 |
| --- | --- | --- |
| 数据入口 | `https://models.dev/catalog.json`，根为 models＋providers | `https://models.dev/experimental/v2.0/api.json`，Provider 为根 |
| 思考能力 | `reasoning`＋`reasoning_options[]` | `capabilities.reasoning` |
| 档位 | `{type:effort,values:[...]}` | `effort:[...]` |
| 开关／预算 | toggle、budget_tokens | toggle、budget |
| 供应商默认 | 无独立的思考默认字段 | 可选 default，type 为 toggle／effort／budget，value 对应布尔／枚举／数值 |
| 其他资料 | 混合后的一般能力 | 更明确的模型类型、协议/API 及兼容资料 |

v2 是同站点的 **JSON API／Schema 格式版本**，当前路径明确带 experimental；不是第二个模型网站，也不是大模型服务版本。本次逐条匹配全部 154 条 v1 记录，v2 均有同 Provider＋模型，思考声明、档位、开关及预算对照 **0 条差异**。这只说明本次快照一致，不保证未来同步或请求协议都兼容。

已核对 `@earendil-works/pi-ai 1.5.0` 的正式 Tag：其 models.dev source 确实读取这个 v2 地址，内部再转换为自己的 `reasoningOptions`。Provider 定义、model family 规则和其他来源共同生成随发行包携带的目录，并非只抓一个 JSON 或每轮实时更新。

## 与其他 Harness 的一致性及风险

我们采用“按实际接入模型筛选原生值、默认不传、适配层处理字段”的方向，与 pi-ai／OpenCode 的能力过滤原则一致。Agent 默认＋Run 覆盖也属于常见结构。它们的产品档位、别名或近似转换不构成必须照抄的接口标准。

需要保留的边界：

1. **能力按 Provider 路径区分。** 原厂 Kimi 与百炼 Kimi 的开关不同，不能只按底模名称合并；私有网关不能因同名就自动采用公网契约。
2. **档位清单不是请求适配器。** `reasoning_effort`、`thinking`、Google `thinkingConfig` 等仍须通过当前协议正确发送；目录出现枚举不等于现有 Pydantic AI 路径已经全部通过真实请求验收。
3. **默认与关闭分开。** 未知或无思考能力模型使用默认，不发参数；已知默认只回显，不自动传其值。切模型后不能把上一模型的 max 转成 high。
4. **更新在配置阶段进行。** 复用目录刷新，不在 Run 中请求 models.dev。v2 如后续接入，只补资料并保留 v1 回退，不能把现有解析器 URL 直接替换。

本轮结论支持先落地 v1 原生选项；v2 默认资料可作为下一步只读增强。当前代码和数据库均保持不变。

## 来源和可复核性

抓取时间为 2026-11-07 03:14～03:15，Asia/Shanghai。公开原始快照和生成脚本位于忽略目录 `docs/local-artifacts/TMP to delete models-dev-reasoning-audit-20261107/`，不属于产品运行资源。

| 来源 | SHA256 |
| --- | --- |
| [v1 catalog.json](https://models.dev/catalog.json) | `a1ea5c56e106720e26a55b17721e250deddef7c1c33f16297f238324d1fa0d73` |
| [v2 api.json](https://models.dev/experimental/v2.0/api.json) | `9de7736bb029e91a97854d3c8c568d1576d61f01f2c8f9929c1fcf98274b51fe` |
| [v2 Schema](https://github.com/anomalyco/models.dev/blob/dev/packages/core/src/schema-v2.ts) | `330c07111150f7f952a19c793dcbc4c2ae4d34b3d3f8194d1568ae493c6a5223` |

其他依据：[pi-ai 1.5.0 models.dev source](https://github.com/earendil-works/pi/blob/v1.5.0/packages/ai/catalog/sources/models-dev.ts)、[目录构建流程](https://github.com/earendil-works/pi/blob/v1.5.0/packages/ai/catalog/README.md)、[OpenCode 模型说明](https://docs.opencode.ai/docs/models/)。

核对了生产过滤、逐条去重、汇总合计、v1／v2 同键对照及 CSV 重读。未执行真实推理调用，以上数量表示元数据覆盖，不能代替模型服务兼容验收。
