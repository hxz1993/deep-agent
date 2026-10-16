# 决策 API 与轻量路由研究

> 资料核验日期：**2026-10-14**。记录时间：**2026-10-14 14:35 CST（Asia/Shanghai，UTC+8）**。
> 状态：调研与隔离连通性测试；未接入 DeepAgent 产品，未验证 BOM Sheet 选择效果。
> 本文是时间快照，不是长期能力承诺。模型版本、输入格式、上下文、价格、支付方式、授权范围及网关路由均可能在未来数月变化；正式选型、充值或实施前重新查官方文档并做小样实测。本文没有设置自动复查任务。

## 结论与适用场景

决策模型值得用于“给定证据，从有限候选中选择、判断或评分”，例如上传 Excel 后筛选 BOM 工作表。它不替代文件读取器、算术校验或业务 Agent，也不能仅凭返回了置信度就自动删除其他 Sheet。

建议先在 BOM 应用做隔离实验：Python 提取各 Sheet 名称、表头、代表行、合并区域与公式引用，决策模型挑选候选 Sheet，再由主 Agent 读取相关完整内容。允许多个 BOM、unknown 和人工确认；保留引用到的辅助工作表。尚无本项目实测证明它比现有 Agent 更快且更准。

## API 与普通 GPT 调用的差别

| 接口 | 主要输入 | 主要输出 | 接入含义 |
| --- | --- | --- | --- |
| Chat Completions / Responses | 对话、文本、支持的多模态内容及工具定义 | 生成文本、结构化输出、工具调用等 | 现有聊天 Provider 链路 |
| OpenAI Decisions | `model`、`input`、`questions` 数组；问题为 `predicate/choice/score` | `answers` 数组；按问题类型返回判断、候选及评分，可有拒答 | 独立 `/v1/decisions`，不能只换模型名 |
| TypeSafe / OpenRouter 决策入口 | `state` 与按名称组织的 `questions`；`noul/choice/score` | 按问题返回类型化答案 | 与 OpenAI 原生请求形状不同，需适配 |

Pydantic AI 已有 TypeSafeModel 参考实现，将结构化输出字段映射为问题；SDK 有适配不等于 DeepAgent 已接入，也不等于网关支持新接口。普通 LLM 的 JSON Schema 约束保证输出形状，不自动获得专用决策接口的概率语义、价格或延迟。

## 供应商与渠道快照

价格单位均为 **美元 / 百万输入 Token**；基础在线价格，不含平台充值手续费、区域或长上下文附加价。JSON 是输入表示，不代表可直接上传 Excel/PDF。

| 模型／渠道 | 支持输入 | 上下文与限制 | 基础价格 | 可用性与评价 |
| --- | --- | --- | --- | --- |
| OpenAI GPT-6 Luna Decisions | 文本、图片；JSON 可作为文本 | 1,050,000 Token：模型页与 OpenRouter 决策模型页口径；不能据此假设所有请求享受基础价 | $0.10；该端点输出及缓存读写不收费 | 官方公测。约快 10 倍是官方相对 Responses 的说法，本项目未测出该速度；未取得中文 BOM 独立验证 |
| OpenRouter `openai/gpt-6-luna-decisions` | 文本、JSON、图片 | 1,050,000 Token；最多 200 个问题 | $0.10；输出免费 | 同一个 OpenAI 模型的渠道，不是另一个模型；未完成付费请求及 Alpha 网络验证 |
| TypeSafe `jev-1.13.0` / `jev-latest`；OpenRouter `typesafe/jev-1.13` | 文本、JSON；不支持图片、音视频 | 请求总计 64,000 Token；`state + 最长单个问题` 不超过 32,000 Token | $0.042；输出免费 | 早期本地官方接口曾成功，见下文；英文更有优势，中文需单测。官方列出数学、日期、多跳与干扰信息等弱项 |
| Uprelic Strom | 文本、JSON、图片；视频按帧作为图片输入 | 总计 32,000 Token；最多 8 张图片、每张 10MB；最多 256 个问题及每题 256 选项 | $0.042；输出免费；按需 ZDR 私有入口 $0.0504 | 官方有按量服务；图片计费另按像素折算。未实测账号、付款及国内网络，未核验可靠的独立中文准确率评测 |

规格来源：[OpenAI 指南](https://developers.openai.com/api/docs/guides/decisions)、[GPT-6 Luna 模型页](https://developers.openai.com/api/docs/models/gpt-6-luna)、[OpenRouter GPT Decisions](https://openrouter.ai/openai/gpt-6-luna-decisions)、[TypeSafe 模型](https://docs.typesafe.ai/models)、[Jev 模型页](https://openrouter.ai/typesafe/jev-1.13)、[Strom 定价及限制](https://platform.uprelic.com/docs/pricing)。

两个需要保留的口径差异：

- OpenAI 指南与 API Reference 对图片 URL 的说明不一致：指南要求 Base64 data URL，Reference 列出 HTTP(S) URL。未实测前不依赖公网图片 URL；[Reference](https://developers.openai.com/api/reference/go/resources/decisions/methods/create)。
- Jev 不能简单写成“32K”或“64K”：需同时满足总请求和单问题两个限制，旧页面片段可能仍只显示 32K。

### 国内使用与支付

[OpenRouter 支持页](https://openrouter.ai/support/)列出 AliPay、银行卡和 USDC；充值手续费另计。支持支付宝不代表 Alpha 或国内服务器一定能直连，也不代表任何具体模型账号已获授权。

本轮有限范围调研没有确认国内大平台提供完全同类的通用决策端点，不据此断言市场上不存在。国内备选是普通 LLM 加结构化输出，例如[阿里云百炼](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-structured-output)、[火山方舟](https://docs.volcengine.com/docs/ark/responses-api-structured-output?lang=zh)；应按具体模型另查模态、价格与上下文，不将其与专用决策 API 混算。

## 反馈与评价的证据等级

| 来源 | 公开结果 | 可以说明什么／不能说明什么 |
| --- | --- | --- |
| OpenRouter，2026-09-22 发布、09-24 更新，Banking77 的 3,080 条英文银行意图、77 类 | Jev 准确率 81.0%，Opus 5 关闭推理并使用严格 JSON 为 84.4%；端到端中位数 175ms / 2,266ms；每千次 $0.11 / $2.42 | 渠道方测试，有商业关联，不是完全独立评测；支持特定任务的速度成本优势，不证明中文 BOM 更准确 |
| 独立预印本《A First Glance at Jev for Network Traffic Classification》，2026-09-30 | 网络流分类中，40 个示例条件下 Jev 28.42%；另一个 100 条配对子集 Jev 29%、750ms，GPT-5.6 Sol 37%、6,036ms | 数值型网络任务不等于 BOM；实验监督量与配置不同，不能泛化模型排名，但说明“决策专用”不保证准确率更高 |
| OpenAI 官方 | Decisions 比 Responses 约快 10 倍 | 官方主张；尚未在本地链路复现，不作为 SLA |
| Strom 官方延迟页 | 1,000 输入 Token 模型时间中位数 122ms、P90 135ms | 仅模型耗时，不能直接和包含网络的 Jev 175ms 比快慢；国内网络及业务准确率未测 |

来源：[OpenRouter Banking77](https://openrouter.ai/blog/insights/jev-vs-claude-opus-5-classification/)、[独立预印本](https://arxiv.org/abs/2610.00376)、[Strom 延迟](https://platform.uprelic.com/docs/latency)。以上成本是各自测试输入规模的结果，不是固定的每文件价格。

## 本地测试记录：明确区分日期和未通过原因

### 2026-10-14：本地 8000 已配置的两条 OpenAI 链路

本次以原生 HTTP 探测新接口，避免把 Pydantic AI 未封装误判为服务不支持。未修改产品接入代码或业务配置；没有得到一次成功的 Decisions 推理。

| 链路 | 证据 | 结论 |
| --- | --- | --- |
| 内部 OpenAI API 转发网关 | 同凭据普通 `/chat/completions` 流式调用 HTTP 200，收到文本 delta；`/decisions` 重复 HTTP 404、`404 page not found` | 被测网关未提供该路由；不能推断官方模型不支持 |
| GPT 订阅 | 订阅 `/responses` 返回 HTTP 401 `token_revoked`；本地模型测试接口应用结果 `ok:false/request_failed` | 当时连普通订阅链路也未打通，凭据问题阻断，不能用此判定 Decisions 能力 |
| 订阅凭据直接探测其他路径 | 官方 Decisions 地址出现 401 或连接失败；猜测的 Codex Decisions 路径出现 403 HTML 或超时 | 猜测路径不是已公开支持的接口；没有足够证据证明订阅支持或不支持 Decisions |

本地模型测试经过正常 SDK 流程，但未证明 OAuth 刷新成功。官方订阅说明目前用于 Responses 推理，不默认具备 API 新端点额度或权限：[推理说明](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[错误恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)。

脱敏探测文件位于私有目录 `docs/local-artifacts/TMP to delete Decisions连通性/`。`results.json` 是最后一次重跑的结果，不包含每次历史探测；不将本文提及的所有尝试声称为该文件可逐条复现。凭据及账号信息不进入课题。

### 2026-09-21：早期 Jev 实验，仅作历史依据

既有本地研究现位于 `/Users/wuzhiruge/PycharmProjects/AI-hub/jev-lab/`，其 README 内仍有迁移前路径。此次复核的是历史记录，没有重新调用该账号：

- 官方 TypeSafe API 曾返回 HTTP 200 和有效 answers，实际模型 `jev-1.13.0`；四个简单虚构样例的 7 项业务断言通过，单例端到端 523–580ms。包含一次网络失败记录。
- MCP 工具选择实验使用真实 69 项工具目录、10 个模拟上下文；8 个目标明确场景中 7 个排第一、8 个进入前二，但有跨领域误选。全目录请求中位数 1.70 秒，明显不能拿官方模型时间代替应用耗时。
- 没有真实 BOM Sheet 分类验收，也没有与现有搜索或当前 Agent 的公平基线比较。历史成功不代表今天凭据、额度或模型别名仍有效。

## 下次实施前的复核清单

1. 重新核对官方端点、模型固定版本、输入格式、上下文两级限制、价格、长上下文附加价与充值费；记录新的核验日期，保留本次快照。
2. 在实际部署网络验证鉴权、超时、限流与正确答案，不以文档存在、HTTP 200 或支付宝可充值代替可用性。
3. 先冻结人工标注的中文 BOM 答案，覆盖多个 BOM、无 BOM、成本汇总、工艺表、隐藏表、跨表公式与歧义表头。
4. 对比现有 Agent 与候选决策模型：相同证据、问题与新请求；文本条件公平比较，图片增强另开一组。重复测试，记录准确率、漏选率、unknown、稳定性、端到端 P50/P95、失败率与实际成本。
5. 低置信或冲突结果回退主 Agent／人工，不设未经校准的统一放行阈值；计算与金额校验仍由代码完成。BOM 规则留在业务应用，不硬编码到通用 Harness。

成本量级示例：若每次含问题共 2,000 输入 Token，1,000 次共 200 万 Token，基础输入费约 GPT Decisions $0.20、Jev/Strom $0.084；不含图片额外折算、手续费、附加价及重试。这只是估算，不是当前业务账单。

本轮只更新研究记录，不购买额度、不部署路由、不新增工具、Provider、数据库结构或配置。是否进入实现仍以业务对照测试为依据。
