# 租赁合同在线专业解析测试 · 2026-09-30

隔离测试 MinerU 精准 VLM、PaddleOCR-VL-1.6 AI Studio 和智谱 GLM-OCR 官方 API。三份真实扫描文档为31页租约、6页补充协议、55页原租约，共92页、两个业务案例。同一文件Hash、全文范围；人工答案不提交给模型。

本轮结果见[三方质量、速度及费用报告](three-provider-comparison-report.md)。[首次两方结果](results.md)保留为历史记录，不能替代本轮三方审计。

- `run_online.py`：通过隐藏终端输入读取 Key，仅保存在进程内；不新增环境变量，不安装本地推理依赖，不修改产品配置。
- `run_glm.py`：单份PDF调用GLM官方全文解析，保存Markdown、布局和usage。
- `run_compare.py`：对显式给出的样本逐份运行三方，三方同一份文件并行；`--trial`允许明确安排独立重复试验，已有目录禁止覆盖和意外重提交。
- `resume_mineru.py`：只查询并下载已经提交的MinerU任务，遇429退避，不另建任务。恢复记录与原始运行记录分开。
- `local-private/`：Git忽略的原始响应、下载结果及运行记录；含真实合同内容和临时签名链接，不提交。原件位于上级 `local-private/`，不重复复制。
- 外部提交不自动重试，防止重复任务；提交成功后仅轮询查询。等待超时不代表远端任务已取消。
- 当前不调用普通聊天模型做字段提取。解析质量按物理页、数字、单位和表格行列另行核对。

运行：使用项目既有Python环境，依赖 `requests`、`pypdf`；在本目录执行，按提示隐藏输入三方Key：

```sh
python3 run_compare.py --trial selected-cases /absolute/path/to/first.pdf /absolute/path/to/second.pdf
```

`selected-cases`必须是未使用过的试验名，产物仅写入忽略目录。单份GLM的参数见 `python3 run_glm.py --help`。复用既有结果时不要再次提交。

首轮短协议/55页合同的轮询缩进错误已经修复：两处休眠均位于while内部。受影响的首轮时间不作为公平速度排名；修正后的重复请求疑似命中云端缓存，不能冒充首次推理性能。详见报告。临时原图检查产物已清理。

协议依据：[MinerU API](https://mineru.net/apiManage/docs)、[Paddle官方HTTP客户端源码](https://github.com/PaddlePaddle/PaddleOCR/blob/main/paddleocr/_api_client/_http.py)、[Paddle结果解析源码](https://github.com/PaddlePaddle/PaddleOCR/blob/main/paddleocr/_api_client/_poller.py)、[GLM官方模型文档](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-ocr)。测试通过与否、模型声明和页面覆盖以实际运行记录为准，不从HTTP200推断内容正确。
