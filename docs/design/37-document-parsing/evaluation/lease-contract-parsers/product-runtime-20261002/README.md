# 文档解析生产适配器与租赁回归（2026-10-02）

本目录检验新增 `read_document` 与真实生产适配器，不修改租赁 Demo 或线上业务记录。原件、原生响应、人工答案、租赁提示词快照和映射输出放入 Git 忽略的 `local-private/`，凭据仅用隐藏终端输入驻留内存。

## 固定协议

- 六份 PDF 共 192 页，三方各一次，共 576 请求页；两份 DOCX 只测 MinerU，正文／表格页图片按供应商声明的格式独立测。
- 首先核对原图建立人工事实，不把供应商输出或业务 Demo 解析 JSON 当答案。已知源文矛盾保留为歧义，不自动纠正。
- 生产适配器任务目录存在时仅恢复同一任务，提交结果未知禁止重新提交；完整归一化产物存在时不再调用服务。
- 原生正文完整性、24K／48K 分页重建、实际模型工具续读次数、核心／租金／物业字段映射分开验收。
- 部分金标准不等于全文准确率；Token 字符串出现只作筛查，不能证明日期／金额所属行列正确。
- 延迟是本机端到端观察值，原件重复提交可能命中供应商云缓存，不能声称纯模型推理速度。

## 运行

在 deep-agent 根目录运行：

```bash
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/run_adapters.py
```

每个供应商的 Key 使用隐藏输入，不使用环境变量。`--samples` 可选择清单中的文件名；`--providers` 可选择供应商；只有明确新增实验才用新 `--trial`，不能以此绕过不明确提交状态。

当前清单 12 份文件（6 PDF、2 DOCX、2图片、PPTX/XLSX）、192 个 PDF 源页；新增北京／德思勤／国金原图 73 项事实，旧三份合同人工审核记录单独保留。真实执行和业务效果见本目录报告，不将准备状态写成通过。

分页和模型回归（已有原生结果时不请求OCR）：

```bash
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/run_paging.py
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/run_mapping.py --case-hash eed541ff251bff4d 474b22ee4e95075c b0e89fbe4b9aeb55 295260b5e91cc7b9 2a6b38b73c43fa7d 007754614a6d3b1e
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/run_mapping.py --paired
PYTHONPATH=src .venv/bin/python docs/design/37-document-parsing/evaluation/lease-contract-parsers/product-runtime-20261002/audit_mapping.py
```

`run_mapping.py`只读本机SQLite中已启用的同一个DeepSeek模型配置，不记录凭据；仅测试隔离Schema输出。`--concurrency`必须为正数；并发不会为同一任务生成多个提交。一次成功结果和模型输出直接复用，不把恢复速度当作首次解析性能。

追加证据：`replay_mineru_artifacts.py`只重放旧授权ZIP并验证原件Hash，不发HTTP请求；`run_mapping.py --trial historical-mineru-replay`产生新的同模型提取结果；`audit_model_reads.py`直接检查真实模型ToolReturnPart正文的逐字符覆盖，保留首轮跳页失败及复测结果，不用最终has_more代替完整读取。
