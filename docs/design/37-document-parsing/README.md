# 37 · 文档解析：复杂 PDF 选型与工具化

> 首次研究：2026-09-29。状态：v0.13.0 新增可选Block布局与完整证据JSON导出，验收状态见新报告；v0.12.11 保留供应商原始布局信息并补回Markdown遗漏；v0.12.10 完成可读格式适配与回归；v0.12.9 已完成工具接入；Paddle/GLM 完成真实合同测试，MinerU 云端可用性限制单独记录。数据库结构保持不变。
> 2026-10-09 当前范围：MinerU 产品选项与后端实现已移除，仅保留 PaddleOCR / GLM-OCR。下方三供应商研究、测试及发布审计为历史证据，不表示当前支持范围；历史重放脚本若依赖已删除的产品内部函数须使用其对应历史版本。


本课题重点评估扫描 PDF、复杂表格、多级表头、跨页内容、数字与单位对应、图表和图片提取。以 MPDocBench-Parse 为主参考，区分公开榜单、公开反馈、本机实测和待取得 Key 的在线验证。

- [产品接入与使用指南](../../guides/document-parsing.md)：本轮实际工具、参数、24K/48K分页、Run缓存、配置与上传边界。
- [文档证据引用与PDF／Word原文定位设计](document-evidence-and-preview-design.md)：本轮DeepAgent实现、租赁文件交付与原PDF坐标边界，不修改公共预览服务。
- [布局与证据文件使用指南](../../guides/document-evidence.md)：两个独立开关、完整Schema、合成样例、下载与引用校验。
- [v0.13.0布局与导出回归](evaluation/evidence-layout-20261011/validation-report.md)：双供应商、六合同、双预算重复读取、真实Agent及文件闭环，区分通过与外部阻塞。
- [v0.12.9 实施与验收说明](document-parsing-implementation.md)：配置与运行链路、自动回归、浏览器验收、真实效果及未通过的服务项目。
- [当前产品三供应商回归报告](evaluation/lease-contract-parsers/product-runtime-20261002/production-regression-report.md)：六份PDF、图片、46组无损分页、模型主动续读及金额风险；区分本轮请求、历史产物重放和独立测速。
- [提交前审计与真实Agent复测](evaluation/lease-contract-parsers/product-runtime-20261002/release-audit-20261002.md)：900秒累计等待修复、三家协议回归、供应商顺序及尚未满足的MinerU发布门槛。
- [PDF工具选择与命名修复](evaluation/lease-contract-parsers/product-runtime-20261002/pdf-tool-routing-20261002.md)：当前工作区采用`parse_document`／解析文档，消除PDF附件误导到`read_file`的提示，真实Agent直接解析验收。
- [三供应商可读格式适配与回归](evaluation/lease-contract-parsers/product-runtime-20261002/model-readable-adapter-20261003.md)：先适配再计算Token，普通Markdown/复杂精简HTML，原生对比、分页次数和最终Agent金额风险；MinerU在线未通过，按用户决定在v0.12.10置灰禁选后发布。
- [原始布局保留与补漏回归](../../reports/document-layout-retention-20261008.md)：修复供应商Markdown过滤已识别页眉等内容后的投影遗漏，保留三家原始有效信息；区分页覆盖与字段识别，记录162块恢复、Token代价及无损续读验收。
- [选型调研与接入建议](document-parsing-research.md)
- [Logics V3、榜单与专业模型路线核验](logics-v3-and-benchmark-review.md)：区分厂商公开成绩、接口价格与真实合同实测。
- [国内四家版本、开通与适配方案](provider-versions-and-adapters.md)：四家横向格式/价格对比、MinerU轻量/精准协议、Paddle两套托管入口；第一版建议独立精细解析工具，不公开双档。
- [国内在线 OCR 追加候选](online-ocr-candidates.md)：Logics退出在线计划；核验智谱GLM-OCR、PPIO托管DeepSeek-OCR-2及百度Unlimited-OCR的Key、价格与榜单口径。
- [真实实验、样本及逐格错误](evaluation/README.md)：MinerU本机两档、免费线上pipeline、PaddleOCR-VL-1.6官方Demo；Logics Demo受阻，商业API待Key。
- [租赁合同专业解析模型对比](evaluation/lease-contract-parsers/README.md)：三份真实扫描文档31/6/55页，已完成MinerU精准VLM、PaddleOCR-VL-1.6与GLM-OCR两轮在线全文调用；[三方报告](evaluation/lease-contract-parsers/online-api-20260930/three-provider-comparison-report.md)记录关键字段、真实表格、抽样CER、速度、费用和缓存限制，未宣称完成全文质量评分。
- [前期 PDF/PPT 架构研究](../16-pdf-and-presentation-parsing/README.md)：保留原有文件授权、独立解析工具与结果外置边界。
- 后续事项统一记录在 [研发台账 DOCUMENT-001](../TODO.md)，不在本课题另建排期。

研究不等于上线承诺；榜单版本与在线服务版本必须分别记录。涉及原始数字的结果保留页码和位置证据，不能用语言模型补齐看不清的数值。
