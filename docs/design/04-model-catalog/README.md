# 04 · 模型目录

> 首次研究：2026-07-28

本课题是当前最新研究方向，负责把 models.dev 目录数据库化，并明确模型目录与
`llm_models` 运行时配置的边界。

- [`model-catalog-database-refresh-plan.md`](model-catalog-database-refresh-plan.md)：0.2.2 数据库、刷新、Console、备份与发布方案。
- [`models-dev-ingestion-and-filtering.md`](models-dev-ingestion-and-filtering.md)：models.dev 原始结构、全量抓取、逐层过滤数据与最终落库口径。

目录只提供配置默认值；管理员选择或手工同步后，最终运行事实仍保存在 `llm_models`。
