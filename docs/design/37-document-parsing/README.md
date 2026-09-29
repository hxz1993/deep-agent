# 37 · 文档解析：复杂 PDF 选型与工具化

> 首次研究：2026-09-29。状态：研究与隔离验证；未接入产品，未调整上传限制、数据库或发布版本。

本课题重点评估扫描 PDF、复杂表格、多级表头、跨页内容、数字与单位对应、图表和图片提取。以 MPDocBench-Parse 为主参考，区分公开榜单、公开反馈、本机实测和待取得 Key 的在线验证。

- [选型调研与接入建议](document-parsing-research.md)
- [真实实验、样本及逐格错误](evaluation/README.md)：MinerU本机两档、免费线上pipeline、PaddleOCR-VL-1.6官方Demo；Logics Demo受阻，商业API待Key。
- [前期 PDF/PPT 架构研究](../16-pdf-and-presentation-parsing/README.md)：保留原有文件授权、独立解析工具与结果外置边界。
- 后续事项统一记录在 [研发台账 DOCUMENT-001](../TODO.md)，不在本课题另建排期。

研究不等于上线承诺；榜单版本与在线服务版本必须分别记录。涉及原始数字的结果保留页码和位置证据，不能用语言模型补齐看不清的数值。
