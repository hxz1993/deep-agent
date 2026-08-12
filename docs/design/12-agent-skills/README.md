# 12 · Agent Skill

> 首次研究：2026-08-07
> 实施状态：v0.3.18 已完成线上安装、Agent授权和Harness渐进披露；v0.3.20 已收口每个Pod的单一当前运行目录；v0.3.22 完成安全脚本执行协议；v0.3.23 由系统工具统一读取附件与Skill文件；Alembic 004

本课题记录 DeepAgent 线上 Skill 的最终边界：Skill 是任务方法与素材包，不属于 ToolPackage，
也不自动授予工具权限。上传包原 ZIP 保存于平台文件服务，数据库保存元数据和资源索引；
Harness 0.18 `Skills` 负责按需加载 `SKILL.md`。上传 Skill在启动或管理操作时同步到
`runtime-skills/<code>/`，普通 Run只读本地当前目录。

- [`skill-system-design.md`](skill-system-design.md)：已实施架构、包格式、API、迁移、安全与发布复测。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：Skill 与工具检索、工具授权的关系。
- [`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)：MCP Tool Search，不用于替代 Skill 加载。
- [`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)：ToolPackage 配置与授权模型。
- [`../TODO.md`](../TODO.md)：Revision、脚本、签名、Marketplace 和便携备份等后续状态。
