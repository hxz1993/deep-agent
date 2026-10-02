# 国内在线 OCR 追加候选：GLM、DeepSeek 二代与 Unlimited

核验日期：2026-09-30。服务与论文部分为调研快照；此后GLM已加入用户授权的三方真实合同测试，结果见[实测报告](evaluation/lease-contract-parsers/online-api-20260930/three-provider-comparison-report.md)。PPIO、Unlimited未测试。

## 当前选择

按用户决定，Logics和第三方托管DeepSeek不再列入本轮在线测试计划，历史开源与榜单研究保留。本轮仅比较MinerU精准VLM、PaddleOCR-VL-1.6与智谱官方GLM-OCR；均已取得全文结果并完成选定范围审计。下文其他服务只作为已有调研资料，不安排额外开通或调用。

硅基流动目录只有 DeepSeek-OCR 一代，是该供应商上架范围；不能推导二代没有托管 API。PPIO 的官方模型页明确列出二代模型名、Token 单价与调用入口。这里使用 PPIO Key，不使用 DeepSeek 官方聊天 Key，也不需要为此读取本地聊天模型凭据。

## 服务与 Key

| 项目 | 智谱 GLM-OCR | PPIO 托管 DeepSeek-OCR-2 | 百度 Unlimited-OCR |
| --- | --- | --- | --- |
| 服务性质 | 官方完整文档解析管线 | 第三方供应商托管官方开源 OCR 模型 | 百度官方基于开源底座的完整解析服务 |
| 模型标识 | `glm-ocr` | `deepseek/deepseek-ocr-2` | Unlimited-OCR；API 未给可选 checkpoint |
| Key 入口 | [智谱 API Key 管理](https://bigmodel.cn/usercenter/proj-mgmt/apikeys) | [PPIO API 密钥管理](https://ppio.com/settings/key-management) | [百度 OCR 控制台](https://console.bce.baidu.com/ai-engine/ocr/overview/index)，创建应用并获取 API Key、Secret Key |
| API | POST `https://open.bigmodel.cn/api/paas/v4/layout_parsing` | POST `https://api.ppio.com/openai/v1/chat/completions` | POST `https://aip.baidubce.com/rest/2.0/brain/online/v2/unlimited-ocr-parser/task`，后续轮询 |
| 原生服务输入 | PDF、JPG、PNG；URL 或 data URI | 模型页列 Text/Image；暂不承诺直接上传 PDF，测试时渲染分页图像 | PDF、JPG/JPEG/PNG/BMP/TIF/TIFF/OFD；DOC/DOCX/TXT/WPS/PPT/PPTX |
| 主要限制 | PDF≤50MB、100页；图片≤10MB | 模型页上下文和最大输出均8.2K；调用限制随账户档位变化 | PDF≤100MB、500页；图片≤10MB；流式文档≤50MB；超过50MB须URL提交 |
| 输出 | Markdown、JSON布局；SDK薄封装 | 文本；定位或 Markdown 输出与后处理需实测，不假定具有整文档管线 | JSON、Markdown结构化结果 |
| 价格 | 输入0.2元／百万Token，输出0.2元／百万Token | 输入0.216元／百万Token，输出0.216元／百万Token | 文档称限时免费；个人实名200页、企业实名1000页测试资源，长期费用未确认 |
| 租赁场景价值 | 低价官方完整服务，可对照印章遮挡、表格与关键数字 | 补充二代专用模型基线，适配成本高于直接PDF管线 | 多页连续解析路线，值得验证跨页条件与表格；论文能力不等同线上保证 |

上述 Token 价格按输入与输出分别计算，不是按页计费；实际合同费用必须从接口 usage/账单记录。31页合同不能直接套用商业 Paddle 的0.05元／页，也不能在未测时给出精确费用。已有 AI Studio Token 不等于百度智能云应用 Key/Secret，须按服务入口分别开通。

来源：[GLM 官方说明](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-ocr)、[GLM 官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing.md)、[GLM 开源 SDK 与 MaaS](https://github.com/zai-org/GLM-OCR)、[PPIO 二代模型及价格](https://ppio.com/model/deepseek/deepseek-ocr-2)、[Unlimited API 与限额](https://cloud.baidu.com/doc/OCR/s/fmr1p39gb)、[百度开通与凭据流程](https://cloud.baidu.com/doc/OCR/s/dk3iqnq51)。智谱文档由官方 Markdown 地址直接读取核验；Context7 的 PPIO 检索仍返回一代 OCR 示例，不能把其一代限制直接套到二代。

## 分数该怎样理解

| 公开成绩 | 分数 | 来源及解释 |
| --- | --- | --- |
| GLM-OCR，OmniDocBench v1.5 | 94.62 | [智谱官方发布结果](https://github.com/zai-org/GLM-OCR)；发布时成绩，不称截至今天全榜第一 |
| DeepSeek-OCR-2，OmniDocBench v1.5 | 91.09 | [PPIO 引述模型成绩](https://ppio.com/model/deepseek/deepseek-ocr-2)；不是 PPIO 在线服务实测 |
| Unlimited-OCR，OmniDocBench v1.5 / v1.6 | 93.23 / 93.92 | [作者论文表1](https://arxiv.org/html/2606.23050v1)；论文同表二代v1.5为89.17，与上面的91.09不同，不跨报告强排统一名次 |
| MPDocBench-Parse：PaddleOCR-VL-1.5 / MinerU2.5 pro | 80.70 / 80.49 | 同一[榜单代码快照](https://github.com/bang123-box/MPDocBench-Parsing-Leaderboard/blob/main/index.html)，不是已测试云服务的当前权重证明 |
| MPDocBench-Parse：DeepSeek-OCR2 / GLM-OCR | 76.43 / 75.01 | 同一快照，多页结构任务分数低于前两项；不能用94.62单页成绩推出GLM更适合跨页租赁合同 |

高分且有国内可开通 API 的补充候选中，优先测 GLM。其他开源高分模型可以继续观察，但没有核验到可用的正式托管入口、Key和收费时，不因论文高分要求用户逐家注册。

下一轮使用同一31页扫描合同、既有页7—10人工基线，记录服务版本、覆盖页数、耗时、usage/费用、印章遮挡、日期/金额/比例和跨页表格错误。服务开通证据与真实合同质量结论分别报告，不安装GPU推理栈来替代在线API测试。
