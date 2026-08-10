# DeepAgent Skill 安装与运行指南

DeepAgent v0.3.18 将 Skill 与 ToolPackage 分离。Agent 的 `capabilities` 继续控制可执行工具，
`skills` 只控制任务说明、References 和 Assets；选择 Skill 不会自动授予 MCP 或其他工具权限。

## 管理入口

Console 配置中心的“Skill”页面提供：

- 查看代码内置与上传 Skill。
- 上传 ZIP 安装、替换当前包、启用/禁用、下载原包和删除。
- 查看 Manifest、Reference/Asset 索引和脚本安全提示。
- 在 Agent 编辑弹窗的独立 Skill 区域选择允许使用的 Skill。

Builtin Skill 随代码发布，只读且不能替换或删除。上传包保存在平台文件服务，数据库
`skill_packages` 只保存平台 File ID、Hash、Manifest 和资源索引。

## 包格式

```text
skill.zip
├── SKILL.md              必需，Frontmatter 含 name 和 description
├── manifest.yaml         可选，声明 code/version/schema_version/label
├── references/           可按需读取的文本资料
├── assets/               文本、图片或模板资产
└── scripts/ 或 tools/    允许保存，但当前不可读取或执行
```

ZIP 最大 10 MiB，解压后最大 50 MiB、最多 256 个文件、目录深度最多 8 层。安装器拒绝路径穿越、
绝对路径、符号链接、加密 ZIP、重复路径和压缩炸弹。模型只可分页读取索引内的 UTF-8 文本；
二进制 Asset 仅展示元数据，脚本不进入资源目录。

## API

```text
GET    /api/v1/skills
GET    /api/v1/skills/{code}
POST   /api/v1/skills                         multipart: file=<zip>
PUT    /api/v1/skills/{code}/package          multipart: file=<zip>
PATCH  /api/v1/skills/{code}
DELETE /api/v1/skills/{code}
GET    /api/v1/skills/{code}/resources
GET    /api/v1/skills/{code}/resources/{path}
GET    /api/v1/skills/{code}/package
```

Agent 创建/更新接口增加独立字段：

```json
{
  "skills": ["deepagent-diagnostics"]
}
```

新建 Agent 默认 `[]`；更新不传时保持原值，显式 `null` 返回 422。停用 Skill 的既有引用保留，
但运行时不加载；重新启用后自动恢复。仍被 Agent 引用的上传 Skill不能删除。

## 模型运行

```text
Agent.skills
→ 初始只披露 Skill 名称和简介
→ 模型按需调用 load_capability
→ Harness 返回完整 SKILL.md
→ list_skill_resources/read_skill_resource 按需读取素材
```

`load_capability` 使用 Pydantic AI 官方 Tool Call/Return，正常进入 History，并参与取消和上下文
压缩的工具配对。Skill正文、资源和脚本都不能覆盖系统/Agent Prompt或业务权限。

## 数据库升级和备份

Alembic 003 创建 `skill_packages`，并给 `agents` 增加非空 `skills` JSON。升级前必须确认
`agents.capabilities` 不存在旧 `skill:*` 引用；迁移遇到此类数据会在 DDL 前失败。降级只允许
`skill_packages` 为空且所有 Agent 的 `skills=[]`。

配置备份包含 Skill 元数据和 Agent 引用，但不复制平台文件服务中的 ZIP，所以不是可跨环境独立
恢复的 Skill 安装包。发布到新环境前必须确认平台 File ID 可用，或重新上传原 ZIP。

## v0.3.18 发布前复测

当前实现只在隔离 SQLite 完成开发。Test 恢复后必须：

1. 使用 v0.3.17 备份 Test revision 002。
2. 预检不存在旧 `skill:*` Agent 引用。
3. 验证平台文件服务的 UPL ZIP 上传、下载和删除。
4. 在隔离 Test 副本执行 003，再验证 Agent Skill保存、CRUD和配置备份。
5. 使用真实 DeepFOS MCP与模型验证一次 Skill加载、资源读取和只读调用。

完整设计和安全边界见
[`../design/12-agent-skills/skill-system-design.md`](../design/12-agent-skills/skill-system-design.md)。
