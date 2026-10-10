# 租赁合同专业解析模型对比

日期：2026-09-30。用户明确本轮采用专业解析模型，比较MinerU精准VLM、PaddleOCR-VL-1.6 AI Studio与智谱官方GLM-OCR。已完成三份31/6/55页扫描文档的两轮全文调用，并对关键字段、三张真实表格和抽样正文作原图审计；不安排海外通用模型、Logics或第三方托管DeepSeek测试。

[三方实测比较报告](online-api-20260930/three-provider-comparison-report.md) · [在线API测试目录](online-api-20260930/README.md) · [首次两方结果与限制](online-api-20260930/results.md)。

## 已准备样本

已从租赁合同Demo复制31页租约、6页补充协议、55页原租约至`local-private/`，共92页、两个业务案例；均为扫描页，含正文、签章、OA材料和附件。

SHA-256：`eed541ff251bff4d4f699b120fe6f5e32fd8b26fbd3d9f5d6099a9b77c74fe3d`。

`local-private/gold.json` 是本次人工核对物理页7–10形成的27项事实、5个提成租金阶段和2个物业阶梯，不是全合同所有表格的完整答案。原件、清单、人工答案与后续模型响应均被本目录 `.gitignore` 排除，不含 Key，不提交真实合同。

## 测试路线

先比较“文件→正文/布局/表格”的解析产物，统一核对字形、数字与单位、页码、行列和跨页结构；之后如评估合同字段提取，给不同解析结果接同一个已授权业务模型和同一提示词，分别统计解析错误与业务提取错误。不能把租金业务建模能力直接计作 OCR 精度。

本轮三方均使用用户授权的官方在线服务Key；没有安装GPU推理环境，未以Demo代替正式全文API。早期Logics研究保留为历史资料，已退出在线计划。

| 候选 | 入口与条件 | 当前状态 |
| --- | --- | --- |
| PaddleOCR-VL | [AI Studio Token](https://aistudio.baidu.com/account/accessToken)；VL-1.6官方 API；商业入口另开通 | 三文档全文结果齐全；日期表格与签字留白有错误；商业接口未调用 |
| MinerU | [API管理](https://mineru.net/apiManage)；精准 API 的 model_version=vlm，区别免登录轻量 pipeline | 三文档全文结果齐全；数值冲突和拆行需处理；下载故障/查询故障按原任务恢复 |
| Logics V3 | [ModelScope模型](https://www.modelscope.cn/models/Alibaba-DT/Logics-Parsing-V3)、[官方Demo](https://www.modelscope.cn/studios/Alibaba-DT/Logics-Parsing) | 用户决定退出本轮；早期Demo受阻及开源研究仅作历史资料 |
| GLM-OCR | [智谱开放平台](https://open.bigmodel.cn/)、[官方源码](https://github.com/zai-org/GLM-OCR) | 三文档全文结果齐全；约9–55秒，便宜，但多阶段租金重复冲突；详见报告 |

MinerU免登录接口上限20页，而本合同31页，不以拆分运行冒充整份多页解析。Logics新找到的 Hugging Face Demo 公开空间状态为RUNNING，本次未执行推理；它使用 Transformers 后端，单次120秒GPU上限，实际建议最多3页；不能把它的耗时当官方 vLLM 成绩，也不能冒充31页跨页实测。

## 验收

- 签章遮挡、五个租金阶段的日期和百分号、装修管理费与营业期首期管理费区分。
- 真实表格的表头/行列、数字/单位/空值、跨页连接；需要逐页增补人工标准。
- 工作日与自然日、原文“1个月”与顾问合同月映射分开；不让解析模型计算业务规则来填补原文。
- 同原件Hash、同完整页范围、记录实际模型/后端/版本/参数；不能混用 Demo、本机与商业 API。
- 提交/排队/解析/下载的总耗时与各阶段分开；保留失败和截断，费用依实际成功页与账单。

[Logics与榜单的本次核验](../../logics-v3-and-benchmark-review.md)。未改产品代码、数据库、配置或环境变量。
