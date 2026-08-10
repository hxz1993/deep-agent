# 12 · Agent Skill

> 首次研究：2026-08-07
> 实施状态：v0.3.18 已完成线上安装、Agent 授权、Harness 渐进披露和资源读取；Alembic 003

本课题记录 DeepAgent 线上 Skill 的最终边界：Skill 是任务方法与素材包，不属于 ToolPackage，
也不自动授予工具权限。上传包原 ZIP 保存于平台文件服务，数据库保存元数据和资源索引；
Harness 0.18 `Skills` 负责按需加载 `SKILL.md`。

- [`skill-system-design.md`](skill-system-design.md)：已实施架构、包格式、API、迁移、安全与发布复测。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：Skill 与工具检索、工具授权的关系。
- [`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)：MCP Tool Search，不用于替代 Skill 加载。
- [`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)：ToolPackage 配置与授权模型。
- [`../TODO.md`](../TODO.md)：Revision、脚本、签名、Marketplace 和便携备份等后续状态。
