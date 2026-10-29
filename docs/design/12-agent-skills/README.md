# 12 · Agent Skill

> 首次研究：2026-08-07
> 实施状态：v0.3.18 已完成线上安装、Agent授权和Harness渐进披露；v0.3.20 已收口每个Pod的单一当前运行目录；v0.3.23 统一文件读取；v0.3.24 统一开放Skill脚本执行；v0.6.1 / Alembic 010删除Skill自身启停层，是否运行只由Agent关联决定。

本课题记录 DeepAgent 线上 Skill 的最终边界：Skill 是任务方法与素材包，不属于 ToolPackage，
也不自动授予工具权限。上传包原 ZIP 保存于平台文件服务，数据库保存元数据和资源索引；
Harness 0.26 `Skills` 负责按需加载 `SKILL.md`。上传 Skill在启动或管理操作时同步到
`runtime-skills/<code>/`，普通 Run只读本地当前目录。

同 Hash替换会先核验平台原 ZIP：原包可用返回 `noop`，原包丢失则重新上传并返回 `repaired`。
2026-08-12 已在 Test revision 004验证三个培训 Skill原包下载、重载和真实 XLSX读取链路。
v0.4.3增加单个与全部上传Skill导出；每个导出的Skill ZIP仍使用现有安装格式。

- [`skill-system-design.md`](skill-system-design.md)：已实施架构、包格式、API、迁移、安全与发布复测。
- [`../05-tool-optimization/tool-optimization-design.md`](../05-tool-optimization/tool-optimization-design.md)：Skill 与工具检索、工具授权的关系。
- [`../13-tool-discovery/README.md`](../13-tool-discovery/README.md)：MCP Tool Search，不用于替代 Skill 加载。
- [`../11-tool-packages/tool-packages-design.md`](../11-tool-packages/tool-packages-design.md)：ToolPackage 配置与授权模型。
- [`../TODO.md`](../TODO.md)：Revision、签名和Marketplace等后续状态。

## Skill 维护界面

v0.14.11 将维护页收敛为紧凑表格＋第二页详情，具体使用见[Skill 指南](../../guides/skills.md)。

- 顶部为全部 Skill、未被引用和引用 Agent 的下拉筛选，分割线与 library/unlink/bot 图标区分口径；隐藏没有现存 Skill 的 Agent。
- 所有筛选中自定义排在内置之前，保持组内默认顺序；仅内置显示来源标签。共享 Skill 不重复存储或复制，引用列一个机器人名称＋其余 +N 非模态浮层。
- 运行文件就绪状态单独列出，不代表业务验证。Skill 关联仍由 Agent 页维护，此处不增加配置入口。
- 点击进入详情，复用现有编辑、替换、下载、重载与删除；标题左侧返回。详情最大1080px，左栏320px，余下为撑满高度的预览；目录与预览独立滚动，1000px以下纵向排列。
- 引用查询只投影现有 Agent.id/name/skills，不读提示词、模型或账号资料；失败与无引用区分，不存新的关联副本。

本次已落地的 HTML/JS/CSS 交互原型与8024预览服务已清理；设计决策保留在本课题，历史原型可从Git读取。
