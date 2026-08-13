# 12 · Agent Skill

> 首次研究：2026-08-07
> 实施状态：v0.3.18 已完成线上安装、Agent授权和Harness渐进披露；v0.3.20 已收口每个Pod的单一当前运行目录；v0.3.23 统一文件读取；v0.3.24 统一开放Skill脚本执行；当前 Alembic head 为 006

本课题记录 DeepAgent 线上 Skill 的最终边界：Skill 是任务方法与素材包，不属于 ToolPackage，
也不自动授予工具权限。上传包原 ZIP 保存于平台文件服务，数据库保存元数据和资源索引；
Harness 0.18 `Skills` 负责按需加载 `SKILL.md`。上传 Skill在启动或管理操作时同步到
`runtime-skills/<code>/`，普通 Run只读本地当前目录。

同 Hash替换会先核验平台原 ZIP：原包可用返回 `noop`，原包丢失则重新上传并返回 `repaired`。
2026-08-12 已在 Test revision 004验证三个培训 Skill原包下载、重载和真实 XLSX读取链路。
v0.4.3增加单个与全部上传Skill导出；每个导出的Skill ZIP仍使用现有安装格式。

- [`skill-system-design.md`](skill-system-design.md)：已实施架构、包格式、API、迁移、安全与发布复测。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：Skill 与工具检索、工具授权的关系。
- [`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)：MCP Tool Search，不用于替代 Skill 加载。
- [`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)：ToolPackage 配置与授权模型。
- [`../TODO.md`](../TODO.md)：Revision、签名和Marketplace等后续状态。
