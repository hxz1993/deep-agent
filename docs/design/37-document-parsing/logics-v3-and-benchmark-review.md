# Logics-Parsing-V3、MPDocBench 与专业模型路线核验

核验日期：2026-09-30。用户本轮明确采用专业文档解析模型；没有稳定海外通用模型 Key，因此停止为 Claude/Gemini 做合同对比的安排。本次未执行 Logics 模型推理。

## 两类能力的关系

“支持文件上传”是模型服务输入接口的能力；“文档解析”是把页面还原为文字、表格、版面和文档结构的任务。它们不是完全互斥的模型架构：专业解析器也常用视觉语言模型，通用视觉模型也能完成部分解析；比较时应区分模型本体、PDF渲染与布局后处理管线、业务字段提取三个层次。

合同流程建议是：PDF→专业解析产物（Markdown/布局JSON/表格/页图）→固定下游业务模型→字段与付款规则校验。专业解析模型不负责凭空推断合同月、工作日换算或租金建模规则。

大模型厂商也提供这类模型：

| 厂商 | 专业模型 | 已核实入口 |
| --- | --- | --- |
| DeepSeek | DeepSeek-OCR、DeepSeek-OCR-2 | [官方OCR2仓库](https://github.com/deepseek-ai/DeepSeek-OCR-2)，提供PDF/图片推理；不能由此推导普通聊天Key就能调用OCR2 |
| 阿里 | Logics-Parsing-V3 | [官方仓库](https://github.com/alibaba/Logics-Parsing)、[模型卡](https://huggingface.co/Logics-MLLM/Logics-Parsing-V3)；独立于百炼PDF理解与DocMind服务 |
| 百度 | PaddleOCR-VL、Qianfan-OCR | PaddleOCR-VL已有[正式商业解析接口](https://cloud.baidu.com/doc/OCR/s/3mi73at9o)，不能等同当前合同Demo的PP-OCRv6 |
| 智谱 | GLM-OCR | [官方仓库](https://github.com/zai-org/GLM-OCR)提供开源与MaaS完整管线，输出Markdown和布局JSON；[Key入口](https://open.bigmodel.cn/) |

## MPDocBench-Parse 的可信程度

名称是 Parse（解析），不是 Price（价格）。[论文](https://arxiv.org/html/2605.22100v1)作者来自中国科学技术大学与阿里通义实验室；[公开项目](https://github.com/Tongyi-Zhiwen/Qwen-Doc/tree/main/MPDocBench)提供数据、评价代码与对照模型。公开版本420份PDF、3135页、15个中英文领域；原论文433份与公开版420份需分开。

这是有实用参考价值的公开多页研究基准，覆盖文字、公式、表格、跨页截断元素、图片、阅读顺序、标题层级。它不是独立行业认证或租赁合同数字准确率保证；Overall不能解释成“85%的租金字段正确”。论文自己承认结构标注有判断差异，跨页表格的结果TEDS不能精确判断合并逻辑是否正确。

Logics作者发布的对比图与MP维护者的榜单分别记录。本次MP公开README仍列Logics-v2，未列V3；V3的以下数据来自Logics官方结果图，不标成MP维护者独立复验。已有研究文档还记录了交互榜与README的MinerU分数差异，比较需冻结来源版本。

## V3 的效果与运行成本

作者称0.8B参数，2026-09-22发布。核心是跨页传递紧凑结构状态，恢复标题树、跨页段落/表格与图文关系；输出 `markdown.md`、`hierarchy.md`、`raw_output.dsl`、`result.json`。它的强项直接对应长合同的页面连接，但还没有本合同实测。

本轮重新目检[作者原图](https://raw.githubusercontent.com/alibaba/Logics-Parsing/main/imgs/mpdocbench_results.png)，留存[快照](evaluation/logics-author-chart-20260930.png)：

| 作者图中的型号 | Overall | 表格TEDS | 跨页截断表格TEDS | 标题TEDS |
| --- | ---: | ---: | ---: | ---: |
| Logics-Parsing-V3 | 85.26 | 87.74 | 88.05 | 62.33 |
| MinerU2.5 pro | 79.77 | 89.23 | 91.44 | 36.10 |
| PaddleOCR-VL-1.5 | 80.70 | 83.87 | 83.09 | 47.11 |

V3综合和标题层级更强，不是所有指标第一；这张图的MinerU Pro表格和跨页表格更高。不得把Paddle1.5成绩写成1.6的成绩。

作者使用H100、batch=1、window_size=3测得1.39秒/页；这不是本机、A10G或便宜GPU的速度。默认推理配置窗口为2，不能不记录参数就复用作者速度和成绩。

当前公开发布主要是权重/代码与Demo；[代码许可](https://raw.githubusercontent.com/alibaba/Logics-Parsing/main/LICENSE)是Apache-2.0，Hugging Face模型页显示暂无Inference Provider。未找到官方按页收费API/价格表；不能把阿里DocMind费用写成Logics费用。自部署成本是GPU占用、渲染/后处理、存储与维护，不按0.8B参数直接断言最低总成本。

## 可测试入口的实际状态

- [魔搭官方Demo](https://www.modelscope.cn/studios/Alibaba-DT/Logics-Parsing)：本次直接读取公开config仍HTTP403，不绕过访问控制，未上传合同。
- 新发现[Hugging Face Demo](https://huggingface.co/spaces/hugging-apps/logics-parsing-v3-demo)：公开空间状态RUNNING（revision `437f9348572e17a42929438270cffb9241cb7cec`）；读取了[源码](https://huggingface.co/spaces/hugging-apps/logics-parsing-v3-demo/blob/main/app.py)。它用Transformers BF16而非作者vLLM，ZeroGPU单次120秒上限，源码建议最多3页、约15–40秒/页。因此只能作为小页数烟测入口，不等同31页整合同的作者管线运行；本次未提交推理。
- [百度PaddleOCR-VL](https://console.bce.baidu.com/textmind/application/textParser/create)与[MinerU精准API](https://mineru.net/apiManage)：适合作为有正式账户的合同两家对照。百度价格页当前标按成功页0.05元；个人实名200页、企业实名1000页免费测试资源，具体以账户实际额度为准。MinerU需精准API Token并指定`vlm`，免登录轻量接口最多20页，不能覆盖本合同31页完整输入。
- [智谱GLM-OCR](https://open.bigmodel.cn/)：国内正式MaaS备选，不需要本机GPU；必须核对其实际接口及版本，不能直接用聊天接口替代。

租赁原件与人工关键基线已复制至[合同实验目录](evaluation/lease-contract-parsers/README.md)，没有海外模型真实对比成绩，也没有专业模型的整合同A/B成绩。原先通用模型测试脚本与空配置已删除，不留未使用产品逻辑。
