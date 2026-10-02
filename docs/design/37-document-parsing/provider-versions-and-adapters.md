# 国内专业文档解析：版本、开通与适配边界

核验：2026-09-30。范围为 MinerU、PaddleOCR、Logics、DeepSeek-OCR。只做官方协议与源码核对，本文没有新增模型调用、产品工具、数据库或环境变量。

后续在线计划调整：用户已放弃Logics在线候选；DeepSeek-OCR-2已核验PPIO托管入口，并新增智谱GLM-OCR、百度Unlimited-OCR，详见[追加在线候选](online-ocr-candidates.md)。下表Logics/DeepSeek仍按官方开源管线口径比较，不代表市场不存在第三方服务。

## 决策

采用一个面向 Agent 的独立文档精细解析工具、多个供应商适配器的方向合理。第一版建议只有一种精细解析能力，不公开快速/精准/自动档位；此前提出的 `mode=auto/fast/accurate` 暂不采用。普通可读文件继续使用现有 `read_file`；扫描合同、复杂表格使用专业解析，不把“请求成功”当成内容正确。这里的“精细”描述任务定位，不承诺识别无误。

只有 MinerU 托管服务明确提供轻量/精准两套接口。Paddle 的普通 OCR、结构化管线和 VL 属于不同技术路线；Logics V3、DeepSeek OCR2 也没有统一的快速/精准服务档位。适配器保留真实供应商能力，未来只有速度、费用和效果实测证明双档有用时才增加产品档位。

必须分开：软件版本、模型权重版本、管线/任务类型、托管服务接口版本、账户额度。`MinerU 4.x` 软件、`MinerU2.5-Pro` 权重、HTTP `/api/v4` 和 `vlm` 请求值属于四个不同概念。

## 四家横向对比与文件边界

核验口径：MinerU 对比官方托管服务，Paddle 区分 AI Studio 与智能云商业服务；Logics、DeepSeek 对比官方开源管线。托管服务支持 Office 不能据此推断裸模型可以直接读 Office 二进制。

| 角度 | MinerU | PaddleOCR | Logics | DeepSeek |
| --- | --- | --- | --- | --- |
| 当前重点路线 | 精准 API 的 `vlm`；云端具体权重未披露 | PaddleOCR-VL-1.6 | Logics-Parsing-V3 | DeepSeek-OCR-2 |
| 是否有快速/精准双档 | 托管服务明确有；两套接口 | VL 没有对应统一双档；PP-OCR/PP-Structure/VL 不是同一模型的档位 | 未提供统一双档 | 未提供统一双档 |
| PDF/扫描图片 | 支持 | 支持 | 支持，PDF先渲染 | 支持，PDF先渲染 |
| Word/PPT | 云服务支持 DOC/DOCX/PPT/PPTX | 商业 API 支持 DOC/DOCX/PPT/PPTX/WPS；不能套用于所有入口 | 官方管线未声明原生支持，需另转 PDF | 官方管线未声明原生支持，需另转 PDF |
| Excel | 云服务支持 XLS/XLSX | 当前商业 API 文件清单未列 XLS/XLSX | 未声明原生支持 | 未声明原生支持 |
| TXT/CSV/JSON/YAML/LOG 等 | 非通用原生输入清单；HTML用独立管线 | 商业 API 列 TXT；其他不作为通用原生输入承诺 | 非通用原生输入清单 | 非通用原生输入清单 |
| 产物与重点 | Markdown、JSON、图片，布局/表格/公式 | Markdown、JSON、布局；商业接口有跨页表格合并、标题分级 | Markdown、JSON、标题层级树与跨页结构 | OCR/Markdown、定位输出；完整服务需再封装 |
| 调用方式 | Token、上传或URL、异步提交/查询 | AI Studio Token+SDK；商业 API Key/Secret换Token、异步提交/查询 | 开源推理脚本，自己封装服务 | 开源推理脚本，自己封装服务 |
| 集成形态 | 官方 HTTP API、SDK、开源本地 CLI/服务、官方 MCP | 官方 HTTP API、SDK、CLI/本地Python、自建推理服务、官方 MCP | 官方推理脚本/模型；HTTP/MCP需另封装 | 官方推理脚本/模型；HTTP/MCP需另封装或核验第三方 |
| 官方 MCP | `mineru-open-mcp`；stdio/Streamable HTTP，调用 MinerU 云端 API | `paddleocr-mcp`；stdio/Streamable HTTP；本地、AI Studio、千帆、自建服务四种后端 | 本轮未查到官方 MCP | 本轮未查到官方 MCP |
| 免费与价格 | 官方 SDK 声明当前解析免费；每日1000页最高优先级，超出降优先级 | AI Studio 公布每日每模型3000页免费规则，最新模型以账户/接口为准；商业按量0.05元/成功页 | 未查到官方稳定托管 API 单价；自部署承担算力/运维成本 | 未查到官方托管 OCR API 单价；自部署承担算力/运维成本 |
| 31页合同解析一次 | 当前免费服务下无按页费用 | AI Studio 配额内免费；商业标准价1.55元 | 不能未实测就估算；不是0成本 | 不能未实测就估算；不是0成本 |
| 主要限制 | 精准200MB/200页；轻量10MB/20页 | 商业 PDF 100MB/500页；AI Studio 规则页写超过100页只处理前100页，须核对覆盖页数 | Demo额度不代表生产服务；需承担部署维护 | 需要推理、PDF渲染、队列和后处理维护 |
| 当前接入建议 | 第一轮在线合同对比候选 | 第一轮在线合同对比候选 | 整文档结构与自部署候选 | 自部署候选 |

来源：[MinerU API](https://mineru.net/apiManage/docs)、[免费服务SDK](https://github.com/opendatalab/MinerU-Ecosystem/blob/main/sdk/python/README.md)、[Paddle商业文件清单与参数](https://cloud.baidu.com/doc/OCR/s/3mi73at9o)、[Paddle商业价格](https://cloud.baidu.com/doc/OCR/s/tlrzzplc1)、[AI Studio额度](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm)、[Logics源码](https://github.com/alibaba/Logics-Parsing)、[DeepSeek OCR2源码](https://github.com/deepseek-ai/DeepSeek-OCR-2)。

集成依据：[MinerU官方MCP](https://github.com/opendatalab/MinerU-Ecosystem/blob/main/mcp/README.md)、[MinerU生态入口](https://github.com/opendatalab/MinerU-Ecosystem)、[Paddle官方MCP](https://www.paddleocr.ai/main/en/version3.x/integrations/mcp_server.html)。MCP是调用协议与封装，不增加模型精度或免除后端额度/费用；不同后端的文件支持清单仍须分别核验，不能把商业HTTP API的Office清单套到所有MCP工具。

专业解析不是 `read_file` 的严格升级版或全集。当前 `files/toolset.py` 原生读取文本/日志、CSV、Markdown、HTML、XML、JSON、YAML、DOCX、XLSX。原生文本不需要视觉识别；Excel按工作表和行读取原生单元格文本，比转成图片再识别少一层误差。专业工具补充 PDF、扫描件和复杂版面，必要时也可重新解析含扫描图或复杂布局的 Office，但不能因此取代原生读取。

价格判断：商业按量31页1.55元，1000份同样页数合同为1550元，尚未含后续大模型字段提取。价格页也有新人限时9元/千页活动，折算31页约0.28元，仅能作为满足活动条件的短期价格。先测试 MinerU 免费精准与 Paddle AI Studio，再根据真实稳定性决定付费入口；相同文件、解析配置和模型版本只解析一次，在授权范围内复用产物。GPU自部署是否便宜取决于用量、利用率和维护成本，不能直接称免费。

## 模型与管线主线

本表列影响当前选型的主线，不枚举所有历史权重、语言包、量化及硬件变体。

| 家族 | 主线/版本 | 任务与选择 |
| --- | --- | --- |
| PaddleOCR | PP-OCRv3/v4/v5/v6，当前默认v6 | 文字检测/识别；主要输出文字与位置，不能等同完整表格结构还原 |
| PaddleOCR | PP-StructureV3（另有历史v1/v2） | 版面、OCR、表格、公式等多模型文档管线，适合结构化解析 |
| PaddleOCR | PaddleOCR-VL、VL-1.5、VL-1.6 | 专用视觉语言文档解析；当前测试优先1.6，0.9B模型仍需渲染、布局及后处理 |
| PaddleOCR | PP-ChatOCRv4 | 在解析产物上结合大模型做问答/字段提取；不是另一档纯OCR |
| PaddleOCR | HPD-Parsing | 另一路高吞吐并行解码解析模型；需定制vLLM/NVIDIA运行环境，暂不作为第一轮在线测试入口 |
| MinerU | 2.0-2505-0.9B、2.5-2509-1.2B | 文档VLM权重代际；2.5使用版面分析与局部高分辨率识别 |
| MinerU | 2.5-Pro-2604-1.2B、Pro-2605-1.2B | 同为1.2B；Pro主要改进训练数据与能力，2605进一步修正版面检测并加强图表/印章等分析，不是付费会员 |
| MinerU | pipeline、vlm、MinerU-HTML | 托管精准API暴露的管线选择；不是可指定的2.5/Pro checkpoint。HTML是网页提取路线 |
| MinerU | Diffusion-V1-0320-2.5B | 另一路扩散文档OCR，不能推断精准API的`vlm`就在调用它 |
| Logics | Logics-Parsing、v2、V3 | 文档解析的三代主线；V3重点是整文档标题树及跨页结构 |
| Logics | Logics-Parsing-Omni | 扩展研究分支，不是V3之后的“V4收费档” |
| DeepSeek | DeepSeek-OCR、DeepSeek-OCR-2 | 两代专用视觉OCR；开源脚本支持图片和PDF，普通聊天服务充值不能推导已购买OCR接口 |

版本依据：[Paddle官方SDK模型表](https://www.paddleocr.ai/main/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)、[Paddle各主线说明](https://www.paddleocr.ai/main/en/index.html)、[HPD官方部署](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/HPD-Parsing.html)、[MinerU Pro模型卡](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B)、[MinerU权重列表](https://huggingface.co/opendatalab/models)、[Logics发布记录](https://github.com/alibaba/Logics-Parsing)、[DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR)、[OCR2](https://github.com/deepseek-ai/DeepSeek-OCR-2)。

## MinerU：两套API，精准内部才换参数

| 项目 | Agent轻量 | 精准 |
| --- | --- | --- |
| URL提交 | POST `/api/v1/agent/parse/url` | POST `/api/v4/extract/task` |
| 本地文件 | POST `/api/v1/agent/parse/file`申请上传URL后PUT | POST `/api/v4/file-urls/batch`申请上传URL后PUT |
| 查询 | GET `/api/v1/agent/parse/{task_id}` | GET `/api/v4/extract/task/{task_id}`；本地上传批次按batch查询 |
| 凭据 | 免Token，IP限频 | Bearer Token |
| 管线 | 固定轻量pipeline | `model_version`: `pipeline`默认、`vlm`推荐、`MinerU-HTML` |
| 限制 | 10MB、20页、单文件 | 200MB、200页；批量章节与总览数量冲突，先按50分批 |
| 结果 | Markdown链接 | ZIP内Markdown、JSON、图片等 |
| 生命周期 | 异步提交、轮询 | 异步提交、轮询；可配回调 |

精准URL提交的关键字段：

```json
{"url":"https://example.cn/document.pdf","model_version":"vlm"}
```

轻量URL提交到另一个路径，`url`不变，不传这个模型选择字段。不能把二者实现为仅换一个请求参数。[API文档](https://mineru.net/apiManage/docs)

托管文档未提供可指定的Pro checkpoint或实际部署权重版本字段；云端`vlm`不能直接标成榜单中的某个Pro。自部署可固定权重/revision。速度宣传不等于整文档完成SLA，也不能保证所有文档VLM一定更慢。

当前[官方SDK](https://github.com/opendatalab/MinerU-Ecosystem/blob/main/sdk/python/README.md)说明解析免费、没有隐藏费用；从[Token管理](https://mineru.net/apiManage/token)取免费Token，无需为此次测试先充值。每日1000页是最高优先级额度，超出降优先级，不是1000页硬截止；还存在任务/IP限频等限制，不能承诺无限量。

文档冲突保留：轻量概述说禁用表格/公式，参数表和SDK却写默认开启；因此不能声称轻量完全不识别表格。批量总览200与操作章节50、轻量正式20页与错误示例50页亦不一致，适配前用受控调用验证；默认采用更保守限制。

## Paddle：模型相同，不同服务的凭据和计费不同

### PP-ChatOCRv4与MCP是否要用

PP-ChatOCRv4 是文档信息抽取管线：先 `visual_predict` 获取版面/文字，再按需求 `build_vector` 做检索，并通过 `chat(key_list=...)` 抽取字段；也可加入多模态模型预测。向量检索和多模态融合不是所有调用都必须开启。它不是比VL更高的一档纯文档解析，也不等于官方MCP。

我们的租赁合同流程已有Agent负责租金、日期、出租人等字段提取及业务核验，当前建议专业解析器只提供带页码的内容和表格，再交给现有Agent；不额外嵌套ChatOCR的一套大模型/检索/提示词配置。若以后要把“输入合同→直接给字段JSON”交给独立抽取服务，再与ChatOCR做同样本端到端比较。[官方用法](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-ChatOCRv4.html)

Paddle官方MCP当前列出 `ocr`、`pp_structurev3`、`paddleocr_vl` 三类工具，VL支持1.6，未列PP-ChatOCRv4。可连本地推理、AI Studio官方API、千帆API或自建服务；千帆仅列PP-StructureV3与PaddleOCR-VL，不能推断所有模型版本都可用。支持stdio和Streamable HTTP。凭据按后端分别配置，不混用。[MCP文档](https://www.paddleocr.ai/main/en/version3.x/integrations/mcp_server.html)

试验可以直接接官方MCP，减少重复写上传/查询代码；产品独立解析工具优先使用HTTP/SDK适配器控制授权附件、取消、页覆盖和结果缓存。若选择MCP作为后端，也必须经过我们的文件引用与结果适配，不直接开放任意本机路径或外部URL。此处仅记录接入方式，未安装MCP或新增环境变量。

### AI Studio官方托管API

[Access Token入口](https://aistudio.baidu.com/account/accessToken)，[SDK说明](https://www.paddleocr.ai/main/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html)。SDK接受本地文件或URL，异步任务可由便利方法代为轮询；文档解析默认1.6，可以显式指定版本。

```python
from paddleocr import PaddleOCRClient

client = PaddleOCRClient(token=private_token)
try:
    result = client.parse_document(
        file_path=authorized_pdf_path,
        model="PaddleOCR-VL-1.6",
    )
finally:
    client.close()
```

这是官方客户端调用方式示意，本次未执行。凭据显式传入，不要求项目新增环境变量。SDK版本需随真实安装核验。

[额度规则](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm)当前写每用户每模型每日3000页，超额429，可申请免费提额；该规则页还写超过100页只处理前100页，不能默默当全文完成。规则页日期2026-02-10，未逐个列最新1.6，实际账户、异步接口限制仍需核实。未查到可据此承诺的自助按页付费流程。

### 百度智能云商业文档解析

[应用开通](https://console.bce.baidu.com/textmind/application/textParser/create)。当前[接口文档](https://cloud.baidu.com/doc/OCR/s/3mi73at9o)在2026-09-29更新，明确底层为PaddleOCR-VL-1.6；在线平台和该API共享服务额度。

- API Key + Secret Key换access_token；与AI Studio Access Token分开。
- POST `https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task`，URL附access_token，表单含`file_name`及`file_data`（Base64）或`file_url`。
- 再POST同一路径加`/query`，表单含`task_id`，取Markdown/JSON结果链接。不是OpenAI chat completions。
- 可开跨页表格合并、标题分级、行坐标、图表解析；轮询建议5–10秒，提交/查询QPS分别2/5。
- PDF支持100MB/500页，其他文件类型与上传方式按文档规则；下载结果链接不是永久存储，应保存授权范围内结果。

[价格页](https://cloud.baidu.com/doc/OCR/s/tlrzzplc1)提供购买资源包或开通按量后付费按钮。当前按成功页0.05元，个人实名200页、企业1000页测试额度；31页标准价1.55元，活动和实际账单另外记录。只向账户充值不等于已开通所需产品。

## Logics / DeepSeek：模型开源不等于已提供收费API

Logics V3：[魔搭模型与下载](https://www.modelscope.cn/models/Alibaba-DT/Logics-Parsing-V3)、[官方Demo](https://www.modelscope.cn/studios/Alibaba-DT/Logics-Parsing)、[官方代码](https://github.com/alibaba/Logics-Parsing)。官方以脚本接收PDF/图片，产出Markdown、层级与JSON；尚未查到对应公开稳定商业API和充值单价。不能把百炼或DocMind充值当成购买Logics。Demo不是生产协议。

DeepSeek OCR2：[官方代码及PDF脚本](https://github.com/deepseek-ai/DeepSeek-OCR-2)。配置输入/输出并运行`run_dpsk_ocr2_pdf.py`；尚未查到DeepSeek官方托管OCR按页API。普通DeepSeek聊天平台余额不等于OCR2配额。自部署或第三方托管需单独核验接口、实际模型及成本。

这两家适配器应接我们管理的解析服务或经过核验的托管服务，由服务封装渲染、页处理、后处理和队列；不把模型裸推理直接当成完整PDF解析API。

## DeepAgent适配建议（待实现）

当前网络搜索在`src/deep_agent/web/settings.py`保存供应商选择，由`web/runtime.py`统一执行并返回`ToolReturn`；`files/toolset.py`中的`read_file`校验附件/Skill引用并渐进读取。复用这些产品边界，但文档任务还需要上传、异步等待、结果资源下载和分页读取。

拟对Agent提供`parse_document(resource, pages=...)`，定位为文档精细解析；第一版不公开档位、供应商Token、外部原始URL和所有厂商特有参数。后台配置选择供应商及已验证的专业解析路线，没有配置时明确失败，不自行借用普通聊天Provider凭据。

建议分工：

1. 工具入口校验当前会话/授权Skill的文件引用、页范围及可用供应商；不能凭文件Hash或缓存命中授予访问权限。
2. 供应商适配器负责能力校验、上传/提交/查询/结果转换，保留实际模型版本（未知就标未知）。不同服务支持的格式按实际能力注册，不能因切换供应商就静默把原生文件转为低保真图片。
3. 通用任务层控制总超时、取消、限频、重复提交及部分完成。明确本地等待取消是否真的取消了远端任务，不能虚报已停止计费。
4. 保存Markdown、表格、布局、页码、图像资源和原始供应商响应；不丢掉供应商额外结构，也不伪造不存在的置信度。
5. 工具结果先返回解析引用、覆盖页数、预览、警告和续读位置；按页/块读取完整结果，不一次把31页全文塞入模型上下文。

策略：普通TXT/LOG/DOCX/XLSX先沿用原生读取；PDF、扫描合同、租金阶梯、复杂/跨页表格使用独立专业工具。缺页、空白、结构警告需显式报告，不能把格式转换或部分完成冒充全文识别成功。“自动发现复杂文档再切精准”目前缺少可靠判定依据，第一版不引入这种自动升级承诺。

本合同31页已超MinerU轻量20页上限，完整合同测试应直接精准，不拆成两份再冒充整文档跨页测试。研究阶段可保留不同管线产物，但后续业务提取必须标明具体来源，不混合成无来源文本；研究双管线不意味着产品需提供双档。

建议先验证MinerU精准VLM和PaddleOCR-VL-1.6，再定首个产品适配器；Logics/DeepSeek保留自部署候选。未批准接入前，不新增产品配置、部署环境变量或数据库迁移。
