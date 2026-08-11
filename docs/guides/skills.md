# DeepAgent Skill 安装与运行指南

DeepAgent v0.3.18 将 Skill 与 ToolPackage 分离，v0.3.20 收口上传 Skill 的运行文件。Agent 的 `capabilities` 继续控制可执行工具，
`skills` 只控制任务说明、References 和 Assets；选择 Skill 不会自动授予 MCP 或其他工具权限。

## 管理入口

Console 配置中心的“Skill”页面提供：

- 查看代码内置与上传 Skill。
- 上传 ZIP 安装、替换当前包、启用/禁用、重新加载、下载原包和删除。
- 查看 Manifest、Reference/Asset 索引和脚本安全提示。
- 在 Agent 编辑弹窗的独立 Skill 区域选择允许使用的 Skill。

Builtin Skill 随代码发布，只读且不能替换或删除。上传包保存在平台文件服务，数据库
`skill_packages` 只保存平台 File ID、Hash、Manifest 和资源索引。

上传包在每个 Pod 只生成 `runtime-skills/<code>/` 一份当前运行副本。服务启动、安装、替换、启用
和手工“重新加载”负责同步；普通 Run 只读本地目录，不访问平台文件服务。运行副本丢失或同步
失败时，该 Skill 暂时跳过，其他 Skill、工具和普通回答继续工作。

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
二进制 Asset 仅展示元数据；脚本不进入资源目录，只返回数量和当前不可读/不可执行状态。

一个 ZIP只能包含一个 Skill。培训 Library或第三方合集包如果包含多个同级 `SKILL.md`，需要先拆成
多个 ZIP分别安装；API会返回 422而不是隐式创建多条配置。

## API

```text
GET    /api/v1/skills
GET    /api/v1/skills/{code}
POST   /api/v1/skills                         multipart: file=<zip>
PUT    /api/v1/skills/{code}/package          multipart: file=<zip>
PATCH  /api/v1/skills/{code}
DELETE /api/v1/skills/{code}
POST   /api/v1/skills/{code}/reload
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

Run终态的 `context.skills` 会列出 configured、available、loaded和 unavailable，便于确认配置的
Skill是否已就绪及本轮实际加载了哪些正文。该状态不写数据库，客户端不适配也不影响聊天。

## 数据库升级和备份

Alembic 003 创建 `skill_packages`，并给 `agents` 增加非空 `skills` JSON。升级前必须确认
`agents.capabilities` 不存在旧 `skill:*` 引用；迁移遇到此类数据会在 DDL 前失败。降级只允许
`skill_packages` 为空且所有 Agent 的 `skills=[]`。

配置备份包含 Skill 元数据和 Agent 引用，但不复制平台文件服务中的 ZIP，所以不是可跨环境独立
恢复的 Skill 安装包。发布到新环境前必须确认平台 File ID 可用，或重新上传原 ZIP。

## v0.3.20 发布后复测

1. 备份 Test revision 003配置。
2. 安装拆分后的三个培训 Skill，确认运行状态为“运行文件已就绪”。
3. 用专用临时 Agent验证 `load_capability`、Markdown/HTML资源读取和跨 Skill加载。
4. 确认 XLSX Asset和 Python Script只展示索引，不能读取或执行。
5. 确认普通后续 Run不重复访问平台文件服务；重载失败只影响对应 Skill。

完整设计和安全边界见
[`../design/12-agent-skills/skill-system-design.md`](../design/12-agent-skills/skill-system-design.md)。
