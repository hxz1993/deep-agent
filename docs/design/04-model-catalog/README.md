# 04 · 模型目录

> 首次研究：2026-07-28
>
> 当前状态：数据库目录与手工刷新已落地，结构已并入 v0.3.0 Alembic 001 基线

[`model-catalog-design.md`](model-catalog-design.md) 合并记录 models.dev 数据结构、
Provider 白名单、标准化、全量替换、Console、备份和发布边界。

目录只提供配置默认值；管理员选择或手工同步后，最终运行事实仍保存在 `llm_models`。
后续事项统一登记在 [`../TODO.md`](../TODO.md)。
