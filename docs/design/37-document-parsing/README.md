# 37 · 文档解析：复杂 PDF 选型与工具化

> 首次研究：2026-09-29。状态：v0.12.9 已完成工具接入与产品回归；Paddle/GLM 完成真实合同测试，MinerU 本轮云端任务尚未返回，业务效果限制单独记录。数据库结构保持不变。

本课题重点评估扫描 PDF、复杂表格、多级表头、跨页内容、数字与单位对应、图表和图片提取。以 MPDocBench-Parse 为主参考，区分公开榜单、公开反馈、本机实测和待取得 Key 的在线验证。

- [产品接入与使用指南](../../guides/document-parsing.md)：本轮实际工具、参数、24K/48K分页、Run缓存、配置与上传边界。
- [v0.12.9 实施与验收说明](document-parsing-implementation.md)：配置与运行链路、自动回归、浏览器验收、真实效果及未通过的服务项目。
- [当前产品三供应商回归报告](evaluation/lease-contract-parsers/product-runtime-20261002/production-regression-report.md)：六份PDF、图片、46组无损分页、模型主动续读及金额风险；区分本轮请求、历史产物重放和独立测速。
- [选型调研与接入建议](document-parsing-research.md)
- [真实实验、样本及逐格错误](evaluation/README.md)：MinerU本机两档、免费线上pipeline、PaddleOCR-VL-1.6官方Demo；Logics Demo受阻，商业API待Key。
- [前期 PDF/PPT 架构研究](../16-pdf-and-presentation-parsing/README.md)：保留原有文件授权、独立解析工具与结果外置边界。
- 后续事项统一记录在 [研发台账 DOCUMENT-001](../TODO.md)，不在本课题另建排期。

研究不等于上线承诺；榜单版本与在线服务版本必须分别记录。涉及原始数字的结果保留页码和位置证据，不能用语言模型补齐看不清的数值。
